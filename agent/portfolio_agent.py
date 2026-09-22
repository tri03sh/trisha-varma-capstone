"""Portfolio Agent - CLI entry point.

Loads agent.md (role/workflow) and skills/portfolio_guide.md +
skills/image_placeholder.md (the Skills) as the system instruction, gives
the model read-only Google Drive tools, and runs an interactive session so
it can ask clarifying questions before producing a case study draft.

Uses Groq's OpenAI-compatible chat completions API. This is a purely
text-based agent - no images are ever read from Drive or sent to the model
(see drive_client.py); the Image Placeholder Skill instead has the model
suggest, from the text it already wrote, what image would fit each section.
This is stateless per call - conversation state (the CLI's multi-turn loop)
is kept explicitly as a `messages` list threaded through each turn, rather
than a server-side interaction id.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from pathlib import Path

import groq
from dotenv import load_dotenv
from groq import Groq

from . import drive_client

REPO_ROOT = Path(__file__).resolve().parent.parent

# A Groq-hosted text/reasoning model with strong tool-calling support,
# confirmed available on this account via client.models.list(). No vision
# needed - see module docstring for why images aren't part of this agent.
MODEL = "openai/gpt-oss-120b"
MAX_TOOL_ITERATIONS = 15
MAX_RATE_LIMIT_RETRIES = 4
DEFAULT_RETRY_DELAY = 5.0

# Enforced ourselves in create_completion (see its docstring) rather than
# relied on the SDK's own timeout handling.
REQUEST_TIMEOUT_S = 120.0

# Upper bound on total wall-clock time for one generate_case_study() call
# (prefetch + all tool-call iterations combined). Individual steps are
# already bounded (REQUEST_TIMEOUT_S per Groq call, MAX_RATE_LIMIT_WAIT_S
# per retry below), but nothing caps their sum - a folder that's slow across
# several such steps can still run past gunicorn's own --timeout 300
# (Procfile) or an upstream platform proxy timeout, which kills the
# connection outright instead of letting this function return its own JSON
# error. That surfaces to the browser as a raw fetch failure (e.g. Safari's
# "TypeError: Load failed") instead of the graceful error/insufficient-info
# response the frontend already knows how to render - see BUILD_LOG.md for
# two prior incidents in this same class. Comfortably under 300s to leave
# margin for prefetch/response overhead.
MAX_GENERATION_WALL_CLOCK_S = 240.0

# Prefetch bounds: a folder's whole subtree is read in plain Python before
# the model is ever called (see prefetch_folder), so a normal generation
# costs one model request instead of one per file discovered. These caps
# stop a pathologically large/deep folder from blowing up that one request.
#
# MAX_FILE_CHARS/MAX_TOTAL_TEXT_CHARS are sized against this account's
# measured tokens-per-minute (ITPM) limit on MODEL - 8000 for
# openai/gpt-oss-120b, measured directly via response headers
# (x-ratelimit-limit-tokens). The system prompt + tool schema alone cost
# ~4500 tokens as of the last measurement (via response.usage.prompt_tokens
# - it keeps growing as skills/*.md gain guidance, so re-measure whenever
# they change), leaving ~3500 tokens of headroom; these caps target well
# under that (~2250 tokens of text, ~9000 chars) to leave real margin for
# tool-call round trips within the same rolling minute. There's no fallback
# left for an oversized request (that was removed with the Visual Curator) -
# a 413 here is a hard failure, so keep shrinking these caps as the system
# prompt grows rather than let the margin erode to nothing. Re-measure both
# numbers if MODEL ever changes - ITPM limits are set per-model, not
# account-wide.
MAX_PREFETCH_FILES = 40
MAX_PREFETCH_DEPTH = 6
MAX_FILE_CHARS = 9000
MAX_TOTAL_TEXT_CHARS = 9000

# Below this many total retrieved words, a folder is deterministically
# treated as too sparse for any Case Study Draft section (the "relevant but
# too short" case in agent.md's insufficient-information rule) - see
# generate_case_study. This only catches the volume problem; "unreadable"
# (nothing extracted at all) is a special case of it (0 words), but
# "off-topic" and "many words but low-substance" content can't be detected
# by word count alone - those are left to the model's own judgment, guided
# explicitly by agent.md's Step 8.
MIN_CONTENT_WORDS_FOR_DRAFT = 300

# A revision round (see continue_case_study) is a separate, stateless Groq call per round
# - not threaded through the growing turn-one message history, so its cost stays roughly
# flat regardless of round number rather than compounding. Starting point, not yet tuned;
# re-measure response.usage.prompt_tokens on a few rounds once this is in use and raise if
# the account's ITPM limit on MODEL leaves headroom (same re-measurement practice as
# MAX_FILE_CHARS/MAX_TOTAL_TEXT_CHARS above).
MAX_FOLLOWUP_TURNS = 3

# How long a ConversationState (see below) is kept in the web layer's in-memory store
# before being purged, so an abandoned session doesn't grow that store forever.
CONVERSATION_TTL_S = 1800.0

# Delimiter the model is instructed to place between top-level sections of its response
# (see generate_case_study/continue_case_study) so the output can be split into
# individually addressable sections. Deliberately "===", not "---": strip_markdown's
# horizontal-rule regex below matches repeated -/*/_ on their own line and would silently
# delete a "---" delimiter before parsing ever saw it - the same reasoning already applied
# to INSUFFICIENT_INFO_MARKER further down.
SECTION_DELIMITER = "===SECTION==="


class LiveLog(list):
    """A log list that also prints each line as it's appended, with a
    timestamp - so a generation's progress (Drive retrieval, tool calls,
    retries) shows up in real time in `railway logs` / gunicorn's stdout,
    not just in the JSON response after the request finishes (or never
    finishes, if it's stuck)."""

    def append(self, item: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {item}", flush=True)
        super().append(item)


def strip_markdown(text: str) -> str:
    """Convert markdown formatting to plain text. The system prompt asks the
    model not to use markdown, but this is a deterministic backstop - the
    web UI displays output_text verbatim, so literal `#`/`**` would
    otherwise show up on the page instead of being rendered."""
    # Bullets: normalize "* item" / "+ item" to "- item" before touching
    # asterisks generally, so bullet markers aren't mistaken for italics.
    text = re.sub(r"(?m)^([ \t]*)[*+][ \t]+", r"\1- ", text)
    # Fenced code blocks: drop the ``` fence lines, keep the content.
    text = re.sub(r"(?m)^[ \t]*```[^\n]*\n?", "", text)
    # Inline code, bold, italics.
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+?)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+?)__", r"\1", text)
    text = re.sub(r"\*([^*\n]+?)\*", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"\1", text)
    # Headings: drop the leading #'s, keep the title text.
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+", "", text)
    # Links: [text](url) -> text (url).
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    # Horizontal rules on their own line.
    text = re.sub(r"(?m)^[ \t]*([-*_])\1{2,}[ \t]*$", "", text)
    # Tidy up blank lines left behind by removed headings/rules.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_section_title(title: str) -> str:
    """Lowercase + collapse whitespace, so a section title can be matched for equality
    regardless of incidental capitalization/spacing - used to line up a revision round's
    update with the original section it's editing (see ConversationState)."""
    return re.sub(r"\s+", " ", title).strip().lower()


def parse_sections(raw: str) -> list[dict]:
    """Split raw model output on SECTION_DELIMITER into an ordered list of
    {"title", "body"} dicts - the first non-blank line of each block is the title, the
    rest is the body. Falls back to a single section (rather than an empty list) if the
    model didn't include any delimiters, so a working draft is never lost to a parsing
    miss.

    Splits on the delimiter wherever it occurs, not just when it's alone on its own
    line - the model sometimes runs it directly onto the end of the preceding text (e.g.
    "[Suggested image: ...]===SECTION===Context") with no line break, which a
    line-anchored regex would miss entirely and leave in the visible output."""
    raw = (raw or "").strip()
    if not raw:
        return []

    blocks = raw.split(SECTION_DELIMITER)
    sections = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n", 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if title:
            sections.append({"title": title, "body": body})

    return sections or [{"title": "Case Study", "body": raw}]


def join_sections(sections: list[dict]) -> str:
    """Inverse of parse_sections for display/finalize output - title, blank line, body,
    blank line, next title... Used everywhere sections are turned back into the text
    shown to the user (turn one's draft, a revision round's output, finalize's merged
    result), so a final safety-net strip of any leftover literal SECTION_DELIMITER lives
    here too - parse_sections should already consume every occurrence, but this is a
    single, cheap backstop against one slipping through into visible output."""
    text = "\n\n".join(f"{s['title']}\n\n{s['body']}".strip() for s in sections if s.get("body") or s.get("title"))
    return text.replace(SECTION_DELIMITER, "").strip()


def merge_sections(original_sections: list[dict], section_updates: dict[str, dict]) -> list[dict]:
    """Apply accumulated per-round section_updates onto original_sections: replace a
    matched section's body in place, and append any leftover updates (genuinely new
    sections that matched nothing original) in the order they were introduced, just
    before the first utility section (Missing Information / Questions for the User) if
    one exists, else at the end. Used by both continue_case_study (to build each round's
    current-state context) and finalize_case_study (to build the final output)."""
    remaining = dict(section_updates)
    merged = []
    for section in original_sections:
        key = normalize_section_title(section["title"])
        merged.append(remaining.pop(key, section))

    new_sections = list(remaining.values())
    if not new_sections:
        return merged

    utility_titles = {"missing information", "questions for the user"}
    insert_at = next(
        (i for i, s in enumerate(merged) if normalize_section_title(s["title"]) in utility_titles),
        len(merged),
    )
    return merged[:insert_at] + new_sections + merged[insert_at:]


@dataclass
class ConversationState:
    """Server-side state for one case study's follow-up revision rounds (see
    continue_case_study/finalize_case_study). Owned and stored by the web layer
    (agent/web.py), keyed by an opaque generation id - not by this agent module, which
    only builds and mutates it.

    Revision rounds are stateless, lean Groq calls, not a growing conversation - see
    continue_case_study - so this holds the current section state, not a message log.
    """

    folder_name: str
    original_sections: list[dict]
    section_updates: dict[str, dict] = field(default_factory=dict)
    turns_used: int = 0
    created_at: float = field(default_factory=time.monotonic)


# Emitted by the model (see agent.md Step 8) as the very first line of its
# response when the retrieved material is too sparse for any Case Study
# Draft section - lets the web UI render a distinct, prominent notice
# instead of just a plain-text "Missing Information" list. Not affected by
# strip_markdown's horizontal-rule regex (that only matches -/*/_, not =).
INSUFFICIENT_INFO_MARKER = "===INSUFFICIENT==="


def extract_insufficient_info_marker(raw_output: str) -> tuple[str, bool]:
    """Strip a leading INSUFFICIENT_INFO_MARKER line, if present. Returns
    (remaining_text, was_insufficient). Never raises - if the marker isn't
    there, the text is returned unchanged."""
    stripped = raw_output.lstrip()
    if not stripped.startswith(INSUFFICIENT_INFO_MARKER):
        return raw_output, False
    return stripped[len(INSUFFICIENT_INFO_MARKER):].lstrip("\n").lstrip(), True


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Honor a Retry-After header when Groq sends one, otherwise fall back
    to exponential backoff."""
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after:
        try:
            return float(retry_after) + 1.0
        except ValueError:
            pass
    return DEFAULT_RETRY_DELAY * (2**attempt)


# Groq's own suggested backoff (from a 429's Retry-After header) can be very
# long - minutes, not seconds - when a real account-level rate limit has
# been hit (e.g. from heavy same-day testing). Blocking a web request for
# that long is bad UX (the browser just hangs) and risks exceeding
# gunicorn's own worker timeout anyway if a second retry is also
# rate-limited. Past this threshold, fail fast with a clear message instead
# of actually waiting it out.
MAX_RATE_LIMIT_WAIT_S = 20.0


class RateLimitTooLong(Exception):
    """Raised instead of waiting when Groq's suggested backoff exceeds
    MAX_RATE_LIMIT_WAIT_S - see create_completion."""


_CALL_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def create_completion(client: Groq, log: list | None = None, **kwargs):
    """client.chat.completions.create with retry on transient rate limits
    (groq.RateLimitError) and a hard REQUEST_TIMEOUT_S deadline enforced in a
    worker thread. Non-rate-limit errors raise immediately.

    The deadline is enforced at the Python level (not via the SDK's own
    `timeout` kwarg) so it's reliable regardless of what the underlying
    HTTP client does - this mirrors a real issue hit against a different
    provider's SDK, where a client-level timeout silently failed to bound a
    request. A still-running thread past the deadline is abandoned, not
    killed - Python can't forcibly stop a thread blocked on a socket call,
    but the caller gets a clear, bounded failure instead of hanging
    indefinitely.
    """
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        future = _CALL_EXECUTOR.submit(client.chat.completions.create, **kwargs)
        try:
            return future.result(timeout=REQUEST_TIMEOUT_S)
        except FuturesTimeoutError:
            raise TimeoutError(f"Groq did not respond within {REQUEST_TIMEOUT_S:.0f}s.") from None
        except groq.RateLimitError as exc:
            delay = _retry_delay(exc, attempt)
            if delay > MAX_RATE_LIMIT_WAIT_S:
                raise RateLimitTooLong(
                    f"Groq's rate limit was hit, and it's asking for a {delay:.0f}s wait before "
                    "trying again - please wait a few minutes and try again."
                ) from exc
            if attempt == MAX_RATE_LIMIT_RETRIES:
                raise
            if log is not None:
                log.append(f"Rate limited by Groq - retrying in {delay:.0f}s ({attempt + 1}/{MAX_RATE_LIMIT_RETRIES})...")
            time.sleep(delay)


def build_system_prompt() -> str:
    agent_md = (REPO_ROOT / "agent" / "agent.md").read_text()
    portfolio_guide_md = (REPO_ROOT / "skills" / "portfolio_guide.md").read_text()
    image_placeholder_md = (REPO_ROOT / "skills" / "image_placeholder.md").read_text()
    return (
        f"{agent_md}\n\n"
        "---\n\n"
        "The Portfolio Guide Skill referenced above follows. Apply it whenever "
        "the agent instructions call for the Skill.\n\n"
        f"{portfolio_guide_md}\n\n"
        "---\n\n"
        "The Image Placeholder Skill referenced above follows. Apply it to every "
        "case study you write.\n\n"
        f"{image_placeholder_md}"
    )


TOOL_DECLARATIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_drive",
            "description": "Search Google Drive for files or folders whose name contains the query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to match against file/folder names."},
                    "folder_id": {
                        "type": "string",
                        "description": "Optional Drive folder ID to restrict the search to.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_folder",
            "description": "List the immediate files and subfolders inside a Google Drive folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_id": {"type": "string", "description": "The Drive folder ID to list."},
                },
                "required": ["folder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a Google Drive file. Google Docs, Sheets, and Slides are exported as "
                "text/CSV. Plain text, markdown, and JSON files are read directly. PDFs and "
                ".docx files are read as text (the legacy .doc format is not supported). "
                "Images and other unsupported file types return a message noting the file "
                "was found but not read - this agent extracts text project content only, "
                "not visual content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "The Drive file ID to read."},
                },
                "required": ["file_id"],
            },
        },
    },
]


class DriveScope:
    """Confines retrieval to one target folder and whatever subfolders/files
    are actually discovered inside it - a search or list call is only allowed
    to touch folders already known to be in this subtree. Never widens to a
    parent or an unrelated part of Drive."""

    def __init__(self, root_folder_id: str):
        self.allowed_folder_ids: set[str] = {root_folder_id}
        self.known_ids: set[str] = {root_folder_id}

    def register(self, items: list[dict]) -> None:
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            self.known_ids.add(item_id)
            if item.get("mimeType") == drive_client.FOLDER_MIME_TYPE:
                self.allowed_folder_ids.add(item_id)
            for nested in item.get("contents", []) or []:
                self.register([nested])

    def is_folder_allowed(self, folder_id: str) -> bool:
        return folder_id in self.allowed_folder_ids

    def is_file_known(self, file_id: str) -> bool:
        return file_id in self.known_ids


def _expand_folders(items: list[dict]) -> list[dict]:
    """Attach each folder match's immediate contents, so the model (and the
    user) see what's inside without a separate list_folder round trip."""
    for item in items:
        if item.get("mimeType") == drive_client.FOLDER_MIME_TYPE:
            item["contents"] = drive_client.list_folder(item["id"])
    return items


def call_tool(name: str, arguments: dict, scope: DriveScope | None = None) -> tuple[str, bool]:
    """Execute a Drive tool by name. Returns (result_text, is_error).

    When `scope` is given, retrieval is confined to it: list_folder/read_file
    reject ids outside the known subtree, and search_drive only searches
    folders already inside the subtree, instead of falling back to a wider
    Drive search.
    """
    try:
        if name == "search_drive":
            query = arguments["query"]
            requested_folder_id = arguments.get("folder_id") or None

            if scope is not None:
                if requested_folder_id and not scope.is_folder_allowed(requested_folder_id):
                    return (
                        f"Folder '{requested_folder_id}' is outside the target folder's scope. "
                        "Retrieval is confined to the target folder and its subfolders.",
                        True,
                    )
                search_ids = [requested_folder_id] if requested_folder_id else list(scope.allowed_folder_ids)
                merged: dict = {}
                for fid in search_ids:
                    for item in drive_client.search_items(query, parent_id=fid):
                        merged[item["id"]] = item
                items = list(merged.values())
            else:
                items = drive_client.search_items(query, parent_id=requested_folder_id)

            if not items:
                scope_note = " within the target folder's scope" if scope is not None else ""
                return f"No Drive items found matching '{query}'{scope_note}.", False

            items = _expand_folders(items)
            if scope is not None:
                scope.register(items)
            return json.dumps(items, indent=2), False

        elif name == "list_folder":
            folder_id = arguments["folder_id"]
            if scope is not None and not scope.is_folder_allowed(folder_id):
                return (
                    f"Folder '{folder_id}' is outside the target folder's scope. Retrieval is "
                    "confined to the target folder and its subfolders - not the rest of Drive.",
                    True,
                )
            items = drive_client.list_folder(folder_id)
            if not items:
                return f"Folder '{folder_id}' is empty or not accessible.", False
            if scope is not None:
                scope.register(items)
            return json.dumps(items, indent=2), False

        elif name == "read_file":
            file_id = arguments["file_id"]
            if scope is not None and not scope.is_file_known(file_id):
                return (
                    f"File '{file_id}' hasn't been seen via search_drive/list_folder within the "
                    "target folder's scope, so it can't be read.",
                    True,
                )
            result = drive_client.read_file(file_id)
            if result["kind"] == "text":
                return result["text"], False
            return result["message"], False

        else:
            return f"Unknown tool: {name}", True
    except Exception as exc:  # noqa: BLE001 - surfaced to the user and the model
        return f"Error running {name}({arguments}): {exc}", True


def prefetch_folder(root_folder_id: str, scope: DriveScope, log: list, deadline: float) -> dict:
    """Recursively read every file in root_folder_id's subtree up front, in
    plain Python - no model calls involved. Registers everything found into
    `scope` so a later tool call (if the model still wants one) stays
    confined to this same subtree. Bounded by MAX_PREFETCH_FILES/_DEPTH, and
    by `deadline` (a time.monotonic() cutoff - see MAX_GENERATION_WALL_CLOCK_S)
    so a huge or slow-to-read Drive tree can't blow up the request.
    """
    text_sections: list[str] = []
    unreadable: list[str] = []
    files_seen = 0
    total_text_chars = 0
    hit_deadline = False

    queue: list[tuple[str, int]] = [(root_folder_id, 0)]
    while queue and files_seen < MAX_PREFETCH_FILES:
        if time.monotonic() >= deadline:
            hit_deadline = True
            log.append("  -> stopping prefetch early: overall time budget exceeded")
            break
        folder_id, depth = queue.pop(0)
        try:
            items = drive_client.list_folder(folder_id)
        except Exception as exc:  # noqa: BLE001
            log.append(f"  -> could not list folder {folder_id}: {exc}")
            continue
        scope.register(items)

        for item in items:
            if files_seen >= MAX_PREFETCH_FILES:
                break
            if item.get("mimeType") == drive_client.FOLDER_MIME_TYPE:
                if depth < MAX_PREFETCH_DEPTH:
                    queue.append((item["id"], depth + 1))
                continue

            name = item.get("name", item["id"])

            # Skip images without even fetching metadata/content - this agent
            # extracts text/PDF project content only, not visual content.
            if item.get("mimeType", "").startswith(drive_client.IMAGE_MIME_PREFIX):
                unreadable.append(f"{name}: image - not extracted")
                continue

            if total_text_chars >= MAX_TOTAL_TEXT_CHARS:
                unreadable.append(f"{name}: skipped - already gathered enough material from this folder")
                continue

            files_seen += 1
            try:
                result = drive_client.read_file(item["id"])
            except Exception as exc:  # noqa: BLE001
                unreadable.append(f"{name}: {exc}")
                continue

            if result["kind"] == "text":
                text = result["text"]
                note = ""
                if len(text) > MAX_FILE_CHARS:
                    text = text[:MAX_FILE_CHARS]
                    note = " (truncated)"
                text_sections.append(f"### {name}{note}\n\n{text}")
                total_text_chars += len(text)
                log.append(f"  -> read '{name}' ({len(text)} chars)")
            else:
                unreadable.append(f"{name}: {result['message']}")
                log.append(f"  -> could not read '{name}': {result['message']}")

    return {
        "text_sections": text_sections,
        "unreadable": unreadable,
        "files_seen": files_seen,
        "hit_limit": files_seen >= MAX_PREFETCH_FILES or total_text_chars >= MAX_TOTAL_TEXT_CHARS or hit_deadline,
    }


def describe_tool_call(name: str, arguments: dict) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in arguments.items() if v)
    return f"[using {name}({args})]"


def describe_tool_result(result_text: str, is_error: bool) -> str:
    label = "ERROR" if is_error else "result"
    truncated = result_text if len(result_text) <= 400 else result_text[:400] + f"... ({len(result_text)} chars total)"
    return f"  -> {label}: {truncated}"


def load_env() -> None:
    load_dotenv(REPO_ROOT / ".env")


def get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set (check .env).")
    return Groq(api_key=api_key)


def resolve_tool_calls(
    client: Groq, messages: list, log: list, scope: DriveScope | None = None, deadline: float | None = None
) -> tuple[str | None, list]:
    """Run the tool-call round trip to completion, mutating `messages` in
    place with each assistant/tool message so the caller's message list
    reflects the full exchange. Returns (final_text, tool_errors); final_text
    is None if MAX_TOOL_ITERATIONS was exhausted without a plain-text reply.

    `deadline` (a time.monotonic() cutoff - see MAX_GENERATION_WALL_CLOCK_S)
    stops the loop with a TimeoutError before starting another iteration past
    it, on top of the per-call REQUEST_TIMEOUT_S each iteration already has.
    """
    tool_errors: list[str] = []
    for _ in range(MAX_TOOL_ITERATIONS):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"Generation exceeded the {MAX_GENERATION_WALL_CLOCK_S:.0f}s overall time budget.")
        response = create_completion(
            client,
            log=log,
            model=MODEL,
            messages=messages,
            tools=TOOL_DECLARATIONS,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            return message.content, tool_errors

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": call.type,
                        "function": {"name": call.function.name, "arguments": call.function.arguments},
                    }
                    for call in message.tool_calls
                ],
            }
        )

        for call in message.tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            log.append(describe_tool_call(call.function.name, arguments))
            result_text, is_error = call_tool(call.function.name, arguments, scope=scope)
            log.append(describe_tool_result(result_text, is_error))
            if is_error:
                tool_errors.append(result_text)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result_text})

    return None, tool_errors


def run_turn(client: Groq, messages: list, user_input: str) -> None:
    """Run one CLI turn to completion (including any tool-call round trips).
    Mutates `messages` in place so the next turn continues the conversation.
    """
    messages.append({"role": "user", "content": user_input})
    log: list = []
    output_text, _ = resolve_tool_calls(client, messages, log)
    for line in log:
        print(line)

    if output_text:
        messages.append({"role": "assistant", "content": output_text})
        print(f"\nAgent: {strip_markdown(output_text)}\n")
    else:
        print("\n[No text response]\n")


def generate_case_study(folder_name: str) -> dict:
    """Locate a Drive folder by name and run the agent to draft a case study
    from its contents. Used by the web UI (agent/web.py) - single-shot, no
    conversation state.

    Returns a dict: {"ok": bool, "output": str, "error": str, "log": [str, ...],
    "insufficient": bool}. "log" always contains the tool-call trace, even on
    failure, so the caller can show what was actually tried. "insufficient"
    (only present when ok=True) is True when the retrieved material was too
    sparse for any Case Study Draft section (see agent.md Step 8 and
    INSUFFICIENT_INFO_MARKER) - the caller can use this to render a
    prominent notice instead of treating it like a normal draft.
    """
    log: list = LiveLog()
    folder_name = folder_name.strip()
    deadline = time.monotonic() + MAX_GENERATION_WALL_CLOCK_S

    try:
        matches = drive_client.search_items(folder_name)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": "", "error": f"Could not search Google Drive: {exc}", "log": log}

    folders = [m for m in matches if m.get("mimeType") == drive_client.FOLDER_MIME_TYPE]

    if not folders:
        return {
            "ok": False,
            "output": "",
            "error": (
                f"No Drive folder found matching '{folder_name}'. Check the spelling, "
                "or make sure the folder is shared with the account you authorized."
            ),
            "log": log,
        }

    if len(folders) > 1:
        names = "; ".join(f"'{f['name']}' (id: {f['id']})" for f in folders)
        return {
            "ok": False,
            "output": "",
            "error": f"Multiple folders match '{folder_name}': {names}. Use a more specific name.",
            "log": log,
        }

    folder = folders[0]
    log.append(f"Found folder '{folder['name']}' (id: {folder['id']}).")
    scope = DriveScope(folder["id"])

    # Read everything in the folder's subtree in Python first - this is what
    # keeps a generation to ~1 model request instead of one per file/subfolder
    # the model would otherwise discover turn-by-turn.
    prefetch = prefetch_folder(folder["id"], scope, log, deadline)

    try:
        client = get_client()
    except RuntimeError as exc:
        return {"ok": False, "output": "", "error": str(exc), "log": log}

    try:
        system_prompt = build_system_prompt()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": "", "error": f"Could not load agent instructions: {exc}", "log": log}

    included_count = len(prefetch["text_sections"])
    total_retrieved_words = sum(len(s.split()) for s in prefetch["text_sections"])
    content_is_sparse = total_retrieved_words < MIN_CONTENT_WORDS_FOR_DRAFT

    context_text = (
        f"Project folder: '{folder['name']}' (id: {folder['id']}).\n\n"
        f"{included_count} readable file(s) found in this folder and its subfolders "
        "are included below"
        + (" (stopped early - too much content to include all of it)" if prefetch["hit_limit"] else "")
        + ".\n\n"
    )
    if prefetch["unreadable"]:
        context_text += "Files found but not extracted (images, or not readable as text):\n"
        context_text += "\n".join(f"- {u}" for u in prefetch["unreadable"]) + "\n\n"
    if prefetch["text_sections"]:
        context_text += "\n\n---\n\n".join(prefetch["text_sections"])
    else:
        context_text += "No text content could be extracted from any file directly in this folder."

    if content_is_sparse:
        # Deterministic, not left to the model's judgment call - relying on
        # the model to decide for itself whether content was "too sparse"
        # produced inconsistent results (a full low-quality draft on one
        # run, correct refusal on the next, for the same folder). Since we
        # can measure the retrieved word count in Python, tell the model
        # what to do instead of asking it to judge. This only catches the
        # "relevant but too short" / "unreadable" cases (see
        # MIN_CONTENT_WORDS_FOR_DRAFT) - "off-topic" content can be any
        # length, so that's still the model's call, made explicit below too.
        context_text += (
            f"\n\nOnly {total_retrieved_words} word(s) of usable text were retrieved "
            f"across {included_count} file(s) - far too little to responsibly draft any "
            "Case Study Draft section. If any files above were listed as not extracted, "
            "that's likely why (corrupted, unsupported format, or blank) - name the "
            "specific file(s) in your response. Do not attempt a Case Study Draft. "
            "Instead, begin your entire response with the exact line ===INSUFFICIENT=== "
            "(nothing before it), then provide only the Missing Information and Questions "
            "for the User sections, tailored to what little was found. Answer directly from "
            "the material above - it's already everything readable in this folder and its "
            "subfolders, so there's nothing left to gain from additional tool calls."
        )
    else:
        context_text += (
            "\n\nUsing only the material above, build the case study - but first confirm "
            "it's actually usable: is it genuinely about a design/product project (not "
            "off-topic content that happens to be text), and does it have real substantive "
            "detail rather than being vague or padded despite its length? If either check "
            "fails, treat this as insufficient information per agent.md's Step 8, and begin "
            "your entire response with ===INSUFFICIENT=== instead of drafting. Otherwise, "
            "if this isn't enough usable content for some other reason, say so plainly "
            "instead of searching elsewhere in Drive. The search_drive/list_folder/read_file "
            "tools are available if you need to double-check something, but are confined to "
            "this folder and its subfolders."
        )

    context_text += (
        f"\n\nFormatting requirement for parsing (applies regardless of the above): "
        f"separate each top-level section of your response - every narrative section, "
        f"and Missing Information / Questions for the User if present - with a line "
        f"containing exactly {SECTION_DELIMITER} between sections (not before the first "
        f"section or after the last). This is only a parsing seam; it doesn't change the "
        f"section titles, content, or style requirements above."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context_text},
    ]

    try:
        raw_output, tool_errors = resolve_tool_calls(client, messages, log, scope=scope, deadline=deadline)
    except TimeoutError as exc:
        return {"ok": False, "output": "", "error": str(exc), "log": log}
    except RateLimitTooLong as exc:
        return {"ok": False, "output": "", "error": str(exc), "log": log}
    except groq.RateLimitError:
        return {
            "ok": False,
            "output": "",
            "error": "Groq's rate limit was hit repeatedly and retries were exhausted. Wait a minute and try again.",
            "log": log,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": "", "error": f"Case study generation failed: {exc}", "log": log}

    raw_output, insufficient = extract_insufficient_info_marker(raw_output or "")

    # Parse into sections before strip_markdown - see SECTION_DELIMITER's docstring for
    # why the delimiter has to survive that step. Each section's title/body is then
    # stripped individually so the reconstructed `output` below still reads the same as
    # before this parsing was added.
    sections = parse_sections(raw_output) if raw_output else []
    for section in sections:
        section["title"] = strip_markdown(section["title"])
        section["body"] = strip_markdown(section["body"])
    output = join_sections(sections)

    if content_is_sparse and not insufficient:
        # Safety net: we already told the model this folder was too sparse
        # (see content_is_sparse above); if it didn't include the marker
        # anyway, still flag it deterministically so the UI shows the
        # notice rather than silently trusting a model that didn't comply.
        log.append("Content was measured as too sparse, but the agent didn't include the marker - flagging anyway.")
        insufficient = True
    elif insufficient:
        log.append("Agent flagged insufficient information for a case study draft.")

    if not output and tool_errors:
        return {"ok": False, "output": "", "error": "Retrieval failed: " + "; ".join(tool_errors), "log": log}
    if not output:
        return {
            "ok": False,
            "output": "",
            "error": "The agent produced no response.",
            "log": log,
        }

    return {
        "ok": True,
        "output": output,
        "error": "",
        "log": log,
        "insufficient": insufficient,
        "folder_name": folder["name"],
        "sections": sections,
    }


def continue_case_study(conversation: ConversationState, user_answer: str) -> dict:
    """Run one follow-up revision round: a single, stateless Groq call (no Drive tools)
    that updates only the section(s) `user_answer` affects. Built fresh each call from
    the current merged section state (see merge_sections) rather than resending the raw
    retrieved Drive content or threading a growing message history forward - see
    SECTION_DELIMITER/ConversationState for why. Mutates `conversation` in place
    (`section_updates`, `turns_used`) on success.

    Returns a dict in the same shape as generate_case_study's ({"ok", "output", "error",
    "log"}) - "output" here is just the section(s) this round touched, not the whole
    draft.
    """
    log: list = LiveLog()

    try:
        client = get_client()
    except RuntimeError as exc:
        return {"ok": False, "output": "", "error": str(exc), "log": log}

    try:
        system_prompt = build_system_prompt()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": "", "error": f"Could not load agent instructions: {exc}", "log": log}

    current_sections = merge_sections(conversation.original_sections, conversation.section_updates)
    section_titles = "\n".join(f"- {s['title']}" for s in current_sections)

    user_message = (
        f"This is a revision round for the case study for the project "
        f"'{conversation.folder_name}'. Its current sections are:\n\n{section_titles}\n\n"
        f"{join_sections(current_sections)}\n\n---\n\n"
        f"The user has answered one of your questions or added information:\n\n{user_answer}\n\n"
        "Update the case study to incorporate this. Identify which section(s) above it "
        "affects and reuse that section's title exactly as written (verbatim, not "
        "paraphrased) if you're updating it - matching is done by exact title text, so a "
        "reworded title is treated as a brand-new section instead of an edit. If the new "
        "information doesn't fit any existing section, write a new one with an "
        "appropriate narrative headline. If it resolves an item in Missing Information or "
        "Questions for the User, also output an updated version of that section with the "
        "resolved item removed. Output ONLY the affected section(s) - not the rest of the "
        "case study - each formatted as a title line, a blank line, then the section's "
        "full text, exactly like the sections above. If you output more than one section, "
        f"separate them with a line containing exactly {SECTION_DELIMITER}."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        response = create_completion(client, log=log, model=MODEL, messages=messages)
    except TimeoutError as exc:
        return {"ok": False, "output": "", "error": str(exc), "log": log}
    except RateLimitTooLong as exc:
        return {"ok": False, "output": "", "error": str(exc), "log": log}
    except groq.RateLimitError:
        return {
            "ok": False,
            "output": "",
            "error": "Groq's rate limit was hit repeatedly and retries were exhausted. Wait a minute and try again.",
            "log": log,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": "", "error": f"Revision failed: {exc}", "log": log}

    raw_output = response.choices[0].message.content or ""
    sections = parse_sections(raw_output)
    for section in sections:
        section["title"] = strip_markdown(section["title"])
        section["body"] = strip_markdown(section["body"])

    if not sections:
        return {"ok": False, "output": "", "error": "The agent produced no response.", "log": log}

    for section in sections:
        conversation.section_updates[normalize_section_title(section["title"])] = section
    conversation.turns_used += 1

    return {"ok": True, "output": join_sections(sections), "error": "", "log": log}


def finalize_case_study(conversation: ConversationState) -> dict:
    """Mechanically splice every accumulated section_updates entry into
    original_sections (see merge_sections) - no model call, pure string assembly."""
    merged = merge_sections(conversation.original_sections, conversation.section_updates)
    return {"ok": True, "output": join_sections(merged), "error": ""}


def main() -> None:
    load_env()

    try:
        client = get_client()
    except RuntimeError as exc:
        print(exc)
        return

    system_prompt = build_system_prompt()
    messages: list = [{"role": "system", "content": system_prompt}]

    print("Portfolio Agent - describe the project you'd like to turn into a case study.")
    print("Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        try:
            run_turn(client, messages, user_input)
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}")


if __name__ == "__main__":
    main()
