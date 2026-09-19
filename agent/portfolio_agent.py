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

# Prefetch bounds: a folder's whole subtree is read in plain Python before
# the model is ever called (see prefetch_folder), so a normal generation
# costs one model request instead of one per file discovered. These caps
# stop a pathologically large/deep folder from blowing up that one request.
#
# MAX_FILE_CHARS/MAX_TOTAL_TEXT_CHARS are sized against this account's
# measured tokens-per-minute (ITPM) limit on MODEL - 8000 for
# openai/gpt-oss-120b, measured directly via response headers
# (x-ratelimit-limit-tokens). The system prompt + tool schema alone cost
# ~3900 tokens as of the last measurement (via response.usage.prompt_tokens
# - it keeps growing as skills/*.md gain guidance, so re-measure whenever
# they change), leaving ~4100 tokens of headroom; these caps target well
# under that (~3000 tokens of text, ~12000 chars) to leave room for
# tool-call round trips within the same rolling minute. If the margin gets
# much tighter, shrink these caps rather than let a generation risk a 413.
# Re-measure both numbers if MODEL ever changes - ITPM limits are set
# per-model, not account-wide.
MAX_PREFETCH_FILES = 40
MAX_PREFETCH_DEPTH = 6
MAX_FILE_CHARS = 12000
MAX_TOTAL_TEXT_CHARS = 12000

# Below this many total retrieved words, a folder is deterministically
# treated as too sparse for any Case Study Draft section (the "relevant but
# too short" case in agent.md's insufficient-information rule) - see
# generate_case_study. This only catches the volume problem; "unreadable"
# (nothing extracted at all) is a special case of it (0 words), but
# "off-topic" and "many words but low-substance" content can't be detected
# by word count alone - those are left to the model's own judgment, guided
# explicitly by agent.md's Step 8.
MIN_CONTENT_WORDS_FOR_DRAFT = 300


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
            if attempt == MAX_RATE_LIMIT_RETRIES:
                raise
            delay = _retry_delay(exc, attempt)
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
                "text/CSV. Plain text, markdown, and JSON files are read directly. PDFs are "
                "read as text. Images and other unsupported file types return a message "
                "noting the file was found but not read - this agent extracts text/PDF "
                "project content only, not visual content."
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


def prefetch_folder(root_folder_id: str, scope: DriveScope, log: list) -> dict:
    """Recursively read every file in root_folder_id's subtree up front, in
    plain Python - no model calls involved. Registers everything found into
    `scope` so a later tool call (if the model still wants one) stays
    confined to this same subtree. Bounded by MAX_PREFETCH_FILES/_DEPTH so a
    huge Drive tree can't blow up the request.
    """
    text_sections: list[str] = []
    unreadable: list[str] = []
    files_seen = 0
    total_text_chars = 0

    queue: list[tuple[str, int]] = [(root_folder_id, 0)]
    while queue and files_seen < MAX_PREFETCH_FILES:
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
        "hit_limit": files_seen >= MAX_PREFETCH_FILES or total_text_chars >= MAX_TOTAL_TEXT_CHARS,
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


def resolve_tool_calls(client: Groq, messages: list, log: list, scope: DriveScope | None = None) -> tuple[str | None, list]:
    """Run the tool-call round trip to completion, mutating `messages` in
    place with each assistant/tool message so the caller's message list
    reflects the full exchange. Returns (final_text, tool_errors); final_text
    is None if MAX_TOOL_ITERATIONS was exhausted without a plain-text reply.
    """
    tool_errors: list[str] = []
    for _ in range(MAX_TOOL_ITERATIONS):
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
    log: list = []
    folder_name = folder_name.strip()

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
    prefetch = prefetch_folder(folder["id"], scope, log)

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
            "for the User sections, tailored to what little was found. The "
            "search_drive/list_folder/read_file tools are available if you want to "
            "double-check this folder for anything missed, but are confined to it and its "
            "subfolders."
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

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context_text},
    ]

    try:
        raw_output, tool_errors = resolve_tool_calls(client, messages, log, scope=scope)
    except TimeoutError as exc:
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
    output = strip_markdown(raw_output) if raw_output else ""

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

    return {"ok": True, "output": output, "error": "", "log": log, "insufficient": insufficient}


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
