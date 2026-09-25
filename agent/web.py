"""Web UI for the Portfolio Agent.

One page: a Drive folder name field and a submit button. On submit, it runs
the same retrieval + case-study generation logic in portfolio_agent.py and
displays the result on the same page.

Each visitor signs in with their own Google account (see /auth/login,
/auth/callback) - their Drive credentials live in their own signed session
cookie, never in a shared file, so concurrent users each see only their own
Drive (see drive_client.py's get_service()).

Local dev: python -m agent.web
Production (e.g. Railway): gunicorn agent.web:app --bind 0.0.0.0:$PORT (see
Procfile) - main() below is only used for local dev.
"""

import os
import time
import uuid
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for
from googleapiclient.errors import HttpError
from werkzeug.middleware.proxy_fix import ProxyFix

from . import drive_client
from . import portfolio_agent as agent

agent.load_env()

app = Flask(__name__)

# Railway (and most PaaS hosts) terminate TLS at a reverse proxy in front of
# the app, forwarding the original scheme/host via X-Forwarded-* headers.
# Without this, url_for(..., _external=True) can build an http:// callback
# URL even though the real public URL is https://, which Google's OAuth
# will reject as a redirect_uri mismatch.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set (check .env / Railway variables) - required to "
        "sign session cookies. Generate one with: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )
app.secret_key = SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# In-memory store of follow-up revision conversations (see agent.ConversationState),
# keyed by an opaque generation id handed to the browser. Only correct with exactly one
# gunicorn worker process (Procfile pins --workers 1, using threads for concurrency, for this reason - previously had 2, which
# caused real, observed "This draft is no longer available" failures: a later request
# for the same generation_id landing on the other worker process has no idea it exists,
# since each worker has its own disjoint copy of this dict). If this app is ever scaled
# to multiple workers/processes/instances again, this needs to move to Redis/a DB first.
CONVERSATIONS: dict[str, agent.ConversationState] = {}


def _cleanup_conversations() -> None:
    """Purge conversations unused for CONVERSATION_TTL_S so an abandoned session doesn't
    grow this store forever on a long-lived worker."""
    cutoff = time.monotonic() - agent.CONVERSATION_TTL_S
    expired = [gid for gid, conv in list(CONVERSATIONS.items()) if conv.last_used_at < cutoff]
    for gid in expired:
        CONVERSATIONS.pop(gid, None)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Agent</title>
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 720px;
    margin: 40px auto;
    padding: 0 20px;
    line-height: 1.5;
  }
  h1 { font-size: 1.4rem; margin-bottom: 4px; }
  p.sub { color: #666; margin-top: 0; }
  .hidden { display: none; }
  .account-line { color: #666; font-size: 0.9rem; margin-bottom: 8px; }
  .signin-button {
    display: inline-block;
    margin-top: 12px;
    padding: 10px 20px;
    font-size: 1rem;
    border-radius: 6px;
    background: #2563eb;
    color: white;
    text-decoration: none;
  }
  form { display: flex; gap: 8px; margin: 24px 0; }
  input[type=text] {
    flex: 1;
    padding: 10px 12px;
    font-size: 1rem;
    border: 1px solid #999;
    border-radius: 6px;
  }
  button {
    padding: 10px 20px;
    font-size: 1rem;
    border: none;
    border-radius: 6px;
    background: #2563eb;
    color: white;
    cursor: pointer;
  }
  button:disabled { background: #93b4f0; cursor: not-allowed; }
  .directions {
    color: #666;
    font-size: 0.9rem;
    margin: -8px 0 24px 0;
  }
  .directions p { margin: 6px 0; }
  #status { display: none; color: #555; margin-bottom: 16px; }
  #status.visible { display: block; }
  .spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid #ccc;
    border-top-color: #2563eb;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: middle;
    margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  #error {
    display: none;
    background: #fdecea;
    color: #611a15;
    border: 1px solid #f5c6c0;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 16px;
    white-space: pre-wrap;
  }
  #error.visible { display: block; }
  #result {
    display: none;
    white-space: pre-wrap;
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 20px;
  }
  #result.visible { display: block; }
  .insufficient-notice {
    font-weight: 700;
    font-size: 1.3rem;
    margin-bottom: 14px;
  }
  .section-title {
    font-weight: 700;
    font-size: 1.15rem;
    margin: 0 0 8px;
  }
  .section-title:not(:first-child) { margin-top: 24px; }
  .section-body {
    font-weight: 400;
    font-size: 1rem;
    white-space: pre-wrap;
  }
  .image-placeholder {
    display: block;
    margin: 16px 0;
    padding: 16px;
    border: 2px dashed #b8b8b8;
    border-radius: 8px;
    background: #f5f5f7;
    text-align: center;
    white-space: normal;
    /* Box background is fixed light regardless of OS/browser color scheme (this page
       declares "color-scheme: light dark" at :root), so text inside it needs an
       explicit dark color rather than inheriting the default, which flips to a light
       color under a dark color scheme and becomes unreadable against this background. */
    color: #333;
  }
  .image-placeholder-label {
    font-weight: 700;
    margin-bottom: 4px;
    color: #222;
  }
  .image-placeholder-caption {
    color: #555;
  }
  #revise-section { margin-top: 16px; }
  #revise-section.hidden { display: none; }
  textarea {
    width: 100%;
    box-sizing: border-box;
    padding: 10px 12px;
    font-size: 1rem;
    font-family: inherit;
    border: 1px solid #999;
    border-radius: 6px;
    resize: vertical;
    margin-bottom: 8px;
  }
  #answer-form.hidden { display: none; }
  #rounds-indicator { font-size: 0.85rem; }
  .revision-item {
    white-space: pre-wrap;
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 16px 20px;
    margin-top: 16px;
  }
  .revision-label {
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    color: #666;
    margin-bottom: 8px;
  }
  #rounds-used-note.hidden { display: none; }
  #finalize-btn { margin-top: 16px; }
  #finalize-btn.hidden { display: none; }
  #final-result {
    display: none;
    margin-top: 16px;
  }
  #final-result.visible { display: block; }
  .final-label {
    font-weight: 700;
    font-size: 1.1rem;
    margin-bottom: 16px;
  }
  .final-actions {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
  }
  #export-doc-status {
    font-size: 0.9rem;
    color: #555;
    margin: -8px 0 16px;
  }
  /* "Paper" for the finalized case study only. Background/text color are fixed
     regardless of OS/browser color scheme (:root declares "color-scheme: light
     dark" above) so the page stays a white page even when the surrounding app
     chrome renders in dark mode - same reasoning as .image-placeholder above,
     applied to the whole final container instead of just the image boxes. */
  #final-result .document-page {
    background: #ffffff;
    color: #1a1a1a;
    line-height: 1.7;
    white-space: pre-wrap;
    box-sizing: border-box;
    border: 1px solid #e2e2e2;
    border-radius: 3px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06), 0 6px 20px rgba(0, 0, 0, 0.08);
    padding: 56px 64px;
    width: 816px; /* ~8.5in @ 96dpi - Google Docs/letter-page width */
    max-width: calc(100vw - 40px);
    position: relative;
    left: 50%;
    right: 50%;
    margin-left: -408px; /* half of 816px, centers the fixed width on the viewport */
    margin-right: -408px;
  }
  /* Section headings read as document headings inside the page: a touch more
     top spacing, and a hairline rule to separate them. */
  #final-result .document-page .section-title {
    padding-bottom: 6px;
    border-bottom: 1px solid #ececec;
  }
  #final-result .document-page .section-title:not(:first-child) {
    margin-top: 32px;
  }
  @media (max-width: 900px) {
    #final-result .document-page {
      width: calc(100vw - 40px);
      left: 0;
      right: 0;
      margin-left: 0;
      margin-right: 0;
      padding: 28px 20px;
    }
  }
  @media print {
    body * {
      visibility: hidden;
    }
    #final-result,
    #final-result .document-page,
    #final-result .document-page * {
      visibility: visible;
    }
    /* Suggested-image placeholders are a working aid for the in-app view (where
       images haven't been added yet), not something that belongs in a finished,
       downloaded document - hide them entirely in the PDF rather than printing
       an empty dashed box. The Google Docs export keeps them (see
       build_case_study_docx) since that's still an editable working copy. */
    #final-result .image-placeholder {
      display: none;
    }
    body {
      margin: 0;
      padding: 0;
      max-width: none;
    }
    #final-result {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      margin: 0;
    }
    /* The screen version breaks .document-page out of the app's narrow 720px
       column via position:relative + negative margins (see the un-prefixed
       rule above) - print has no such column to escape, and that trick could
       clip or misalign the page under print layout, so reset it to a plain
       static, full-width block instead. */
    #final-result .document-page {
      position: static;
      left: auto;
      right: auto;
      margin: 0;
      width: auto;
      max-width: none;
      padding: 0;
      border: none;
      box-shadow: none;
    }
    @page {
      margin: 0.75in;
    }
  }
</style>
</head>
<body>
  <h1>Portfolio Agent</h1>

  <div id="signin-prompt" class="{{ 'hidden' if signed_in else '' }}">
    <p class="sub">Sign in with Google to connect your Drive and generate a case study.</p>
    <a class="signin-button" href="/auth/login">Sign in with Google</a>
  </div>

  <div id="app-ui" class="{{ 'hidden' if not signed_in else '' }}">
    <p class="account-line">Signed in. <a href="/auth/logout">Sign out</a></p>
    <p class="sub">Enter a Google Drive folder name to generate a case study draft from it.</p>

    <form id="form">
      <input type="text" id="folder_name" placeholder="e.g. Rallie Project" autocomplete="off" required>
      <button type="submit" id="submit-btn">Generate</button>
    </form>

    <div class="directions">
      <p><strong>How to use this:</strong> enter the name of a project folder from your connected Google Drive. The agent reads the files inside it - Google Docs, Slides, PDFs, and .docx files - and drafts a case study from what it finds.</p>
      <p>This works best when the folder's content is genuinely about your project and there's enough of it. If the content is too sparse, unrelated, or unreadable, the agent will say so rather than guessing. Alongside the draft, you may also get a few missing-information questions if something important isn't documented, and suggested spots to add your own images.</p>
    </div>

    <div id="status"><span class="spinner"></span>Retrieving files and drafting the case study...</div>
    <div id="error"></div>
    <div id="result"></div>

    <div id="revise-section" class="hidden">
      <div id="revisions"></div>

      <form id="answer-form" class="hidden">
        <p class="sub" id="rounds-indicator"></p>
        <textarea id="answer-input" rows="3" placeholder="Answer a question, or add information the draft is missing..."></textarea>
        <button type="submit" id="answer-btn">Send</button>
      </form>
      <p class="directions hidden" id="rounds-used-note">You've used all your follow-up rounds for this draft. Click below to build the final case study.</p>

      <button id="finalize-btn" class="hidden">Finish &amp; Build Final Case Study</button>
      <div id="final-result"></div>
    </div>
  </div>

  <script>
    const form = document.getElementById("form");
    const input = document.getElementById("folder_name");
    const button = document.getElementById("submit-btn");
    const statusEl = document.getElementById("status");
    const errorEl = document.getElementById("error");
    const resultEl = document.getElementById("result");

    const reviseSection = document.getElementById("revise-section");
    const revisionsEl = document.getElementById("revisions");
    const answerForm = document.getElementById("answer-form");
    const answerInput = document.getElementById("answer-input");
    const answerBtn = document.getElementById("answer-btn");
    const roundsIndicator = document.getElementById("rounds-indicator");
    const roundsUsedNote = document.getElementById("rounds-used-note");
    const finalizeBtn = document.getElementById("finalize-btn");
    const finalResultEl = document.getElementById("final-result");

    let generationId = null;
    let turnsRemaining = 0;

    function showError(message) {
      errorEl.textContent = message;
      errorEl.classList.add("visible");
    }

    // Renders draft text into `container` as plain text (never innerHTML - this is
    // model output, so it's never trusted as markup), except that a
    // "[Suggested image: ...]" placeholder gets its "Suggested image:" label bolded.
    // Every other character, including the placeholder's own description, still goes
    // through createTextNode/textContent.
    // Appends `text` to `parent` as plain text nodes (never innerHTML - this is model
    // output, so it's never trusted as markup), except that a "[Suggested image: ...]"
    // placeholder gets its "Suggested image:" label bolded. Every other character,
    // including the placeholder's own description, still goes through createTextNode.
    function appendInlineText(parent, text) {
      const pattern = /\\[Suggested image:([^\\]]*)\\]/g;
      let lastIndex = 0;
      let match;
      while ((match = pattern.exec(text)) !== null) {
        if (match.index > lastIndex) {
          parent.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
        }
        // A block-level div amid inline text nodes naturally breaks the surrounding flow
        // on both sides, so this renders as its own rectangle, not inline bracketed text.
        const box = document.createElement("div");
        box.className = "image-placeholder";
        const label = document.createElement("div");
        label.className = "image-placeholder-label";
        label.textContent = "Suggested image";
        const caption = document.createElement("div");
        caption.className = "image-placeholder-caption";
        caption.textContent = match[1].trim();
        box.appendChild(label);
        box.appendChild(caption);
        parent.appendChild(box);
        lastIndex = pattern.lastIndex;
      }
      if (lastIndex < text.length) {
        parent.appendChild(document.createTextNode(text.slice(lastIndex)));
      }
    }

    // Fallback for when structured `sections` aren't available - renders `text` as one
    // flat block (still with "Suggested image:" bolded via appendInlineText).
    function renderOutputText(container, text) {
      container.innerHTML = "";
      appendInlineText(container, text);
    }

    // Preferred rendering: one bold, slightly-larger heading per section title, its body
    // in normal text below, with consistent spacing between sections (see .section-title
    // /.section-body CSS) - used for the initial draft, each revision round's returned
    // section(s), and the finalized case study alike.
    function renderSections(container, sections) {
      container.innerHTML = "";
      for (const section of sections) {
        const heading = document.createElement("div");
        heading.className = "section-title";
        heading.textContent = section.title;
        container.appendChild(heading);

        const body = document.createElement("div");
        body.className = "section-body";
        appendInlineText(body, section.body);
        container.appendChild(body);
      }
    }

    // Renders `sections` if present and non-empty, else falls back to the flat `text`.
    function renderDraft(container, sections, text) {
      if (sections && sections.length) {
        renderSections(container, sections);
      } else {
        renderOutputText(container, text);
      }
    }

    function updateRoundsUI() {
      if (turnsRemaining > 0) {
        answerForm.classList.remove("hidden");
        roundsUsedNote.classList.add("hidden");
        roundsIndicator.textContent = `${turnsRemaining} follow-up round(s) left.`;
      } else {
        answerForm.classList.add("hidden");
        roundsUsedNote.classList.remove("hidden");
      }
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const folderName = input.value.trim();
      if (!folderName) return;

      button.disabled = true;
      input.disabled = true;
      statusEl.classList.add("visible");
      errorEl.classList.remove("visible");
      resultEl.classList.remove("visible");
      errorEl.textContent = "";
      resultEl.textContent = "";
      reviseSection.classList.add("hidden");
      revisionsEl.innerHTML = "";
      finalResultEl.classList.remove("visible");
      finalResultEl.textContent = "";
      generationId = null;
      turnsRemaining = 0;

      try {
        const response = await fetch("/api/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder_name: folderName }),
        });

        if (response.status === 401) {
          window.location.href = "/auth/login";
          return;
        }

        const data = await response.json();

        if (data.ok) {
          resultEl.innerHTML = "";
          if (data.insufficient) {
            const notice = document.createElement("div");
            notice.className = "insufficient-notice";
            notice.textContent = "Insufficient Information to Draft Study";
            resultEl.appendChild(notice);
          }
          const body = document.createElement("div");
          renderDraft(body, data.sections, data.output);
          resultEl.appendChild(body);
          resultEl.classList.add("visible");

          if (data.generation_id) {
            generationId = data.generation_id;
            turnsRemaining = data.turns_remaining || 0;
            reviseSection.classList.remove("hidden");
            finalizeBtn.classList.remove("hidden");
            updateRoundsUI();
          }
        } else {
          showError(data.error || "Something went wrong.");
        }
      } catch (err) {
        showError("Request failed - the connection was lost, possibly because generation took too long. Please try again.");
      } finally {
        button.disabled = false;
        input.disabled = false;
        statusEl.classList.remove("visible");
      }
    });

    answerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const answer = answerInput.value.trim();
      if (!answer || !generationId) return;

      answerBtn.disabled = true;
      answerInput.disabled = true;
      errorEl.classList.remove("visible");

      try {
        const response = await fetch("/api/answer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ generation_id: generationId, answer }),
        });

        if (response.status === 401) {
          window.location.href = "/auth/login";
          return;
        }

        const data = await response.json();

        if (data.ok) {
          const item = document.createElement("div");
          item.className = "revision-item";
          const label = document.createElement("div");
          label.className = "revision-label";
          label.textContent = "Update";
          const body = document.createElement("div");
          renderDraft(body, data.sections, data.output);
          item.appendChild(label);
          item.appendChild(body);
          revisionsEl.appendChild(item);
          answerInput.value = "";
          turnsRemaining = data.turns_remaining ?? turnsRemaining;
          updateRoundsUI();
        } else {
          showError(data.error || "Something went wrong.");
        }
      } catch (err) {
        showError("Request failed - the connection was lost. Please try again.");
      } finally {
        answerBtn.disabled = false;
        answerInput.disabled = false;
      }
    });

    finalizeBtn.addEventListener("click", async () => {
      if (!generationId) return;

      finalizeBtn.disabled = true;
      errorEl.classList.remove("visible");

      try {
        const response = await fetch("/api/finalize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ generation_id: generationId }),
        });

        if (response.status === 401) {
          window.location.href = "/auth/login";
          return;
        }

        const data = await response.json();

        if (data.ok) {
          finalResultEl.innerHTML = "";
          const label = document.createElement("div");
          label.className = "final-label";
          label.textContent = "Final Case Study";
          finalResultEl.appendChild(label);

          const actions = document.createElement("div");
          actions.className = "final-actions";

          const pdfBtn = document.createElement("button");
          pdfBtn.type = "button";
          pdfBtn.textContent = "Download as PDF";
          pdfBtn.addEventListener("click", () => window.print());
          actions.appendChild(pdfBtn);

          const exportBtn = document.createElement("button");
          exportBtn.type = "button";
          exportBtn.textContent = "Export to Google Docs";
          const exportStatus = document.createElement("div");
          exportStatus.id = "export-doc-status";
          exportBtn.addEventListener("click", () => exportToGoogleDocs(exportBtn, exportStatus));
          actions.appendChild(exportBtn);

          finalResultEl.appendChild(actions);
          finalResultEl.appendChild(exportStatus);

          const page = document.createElement("div");
          page.className = "document-page";
          const body = document.createElement("div");
          renderDraft(body, data.sections, data.output);
          page.appendChild(body);
          finalResultEl.appendChild(page);

          finalResultEl.classList.add("visible");
        } else {
          showError(data.error || "Something went wrong.");
        }
      } catch (err) {
        showError("Request failed - the connection was lost. Please try again.");
      } finally {
        finalizeBtn.disabled = false;
      }
    });

    async function exportToGoogleDocs(button, statusEl) {
      if (!generationId) return;

      button.disabled = true;
      statusEl.textContent = "Creating your Google Doc...";

      try {
        const response = await fetch("/api/export_doc", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ generation_id: generationId }),
        });

        if (response.status === 401) {
          window.location.href = "/auth/login";
          return;
        }

        const data = await response.json();

        if (data.ok) {
          statusEl.innerHTML = "";
          const link = document.createElement("a");
          link.href = data.url;
          link.target = "_blank";
          link.rel = "noopener";
          link.textContent = "Open your Google Doc";
          statusEl.appendChild(link);
          window.open(data.url, "_blank", "noopener");
        } else {
          statusEl.textContent = data.error || "Something went wrong creating the Google Doc.";
        }
      } catch (err) {
        statusEl.textContent = "Request failed - the connection was lost. Please try again.";
      } finally {
        button.disabled = false;
      }
    }
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE, signed_in=bool(session.get("credentials")))


@app.route("/auth/login")
def auth_login():
    flow = drive_client.build_auth_flow(url_for("auth_callback", _external=True))
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = state
    # PKCE: Flow generates this when authorization_url() is called above, but
    # only keeps it on this Flow instance - /auth/callback builds a separate
    # Flow object, so the verifier has to be carried across via the session
    # or fetch_token() fails with "Missing code verifier".
    session["code_verifier"] = flow.code_verifier
    return redirect(authorization_url)


@app.route("/auth/callback")
def auth_callback():
    state = session.get("oauth_state")
    if not state or state != request.args.get("state"):
        return "Invalid or expired sign-in attempt - please try signing in again.", 400

    flow = drive_client.build_auth_flow(
        url_for("auth_callback", _external=True),
        code_verifier=session.get("code_verifier"),
    )
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:  # noqa: BLE001 - surfaced directly, this is an auth-time failure
        return f"Sign-in failed: {exc}", 400

    session.permanent = True
    session["credentials"] = drive_client.credentials_to_dict(flow.credentials)
    session.pop("oauth_state", None)
    session.pop("code_verifier", None)
    return redirect(url_for("index"))


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/generate", methods=["POST"])
def api_generate():
    if not session.get("credentials"):
        return jsonify(ok=False, error="Not signed in.", output="", log=[]), 401

    data = request.get_json(silent=True) or {}
    folder_name = (data.get("folder_name") or "").strip()
    if not folder_name:
        return jsonify(ok=False, error="Enter a Drive folder name.", output="", log=[]), 400

    _cleanup_conversations()

    try:
        result = agent.generate_case_study(folder_name)
    except Exception as exc:  # noqa: BLE001 - last-resort guard so the page always gets JSON, never a raw 500 page
        return jsonify(ok=False, error=f"Unexpected error: {exc}", output="", log=[]), 500

    if result["ok"] and result.get("sections"):
        generation_id = uuid.uuid4().hex
        CONVERSATIONS[generation_id] = agent.ConversationState(
            folder_name=result["folder_name"],
            original_sections=result["sections"],
        )
        session.permanent = True
        # Keep only ids still in the store: this list lives in the signed session cookie
        # alongside the OAuth credentials, and left to grow it eventually passes the
        # browser's ~4KB cookie limit, after which the browser silently stops saving it.
        live_ids = [gid for gid in session.get("generation_ids", []) if gid in CONVERSATIONS]
        session["generation_ids"] = live_ids + [generation_id]
        result = {**result, "generation_id": generation_id, "turns_remaining": agent.MAX_FOLLOWUP_TURNS}
    # "sections" (list of {title, body}) is kept in the response so the web UI can render
    # real headings per section instead of one flattened string - see renderSections.

    status_code = 200 if result["ok"] else 502
    return jsonify(result), status_code


def _get_owned_conversation(generation_id: str) -> agent.ConversationState | None:
    """Look up `generation_id` in CONVERSATIONS, but only if it belongs to the current
    signed-in browser session (see session["generation_ids"]) - stops one visitor from
    reaching another's in-progress conversation even though the id itself is also
    unguessable."""
    if not generation_id or generation_id not in session.get("generation_ids", []):
        return None
    conversation = CONVERSATIONS.get(generation_id)
    if conversation is not None:
        conversation.last_used_at = time.monotonic()
    return conversation


@app.route("/api/answer", methods=["POST"])
def api_answer():
    if not session.get("credentials"):
        return jsonify(ok=False, error="Not signed in.", output="", log=[]), 401

    data = request.get_json(silent=True) or {}
    generation_id = (data.get("generation_id") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not answer:
        return jsonify(ok=False, error="Enter an answer.", output="", log=[]), 400

    _cleanup_conversations()

    conversation = _get_owned_conversation(generation_id)
    if conversation is None:
        return jsonify(ok=False, error="This draft is no longer available - generate a new one.", output="", log=[]), 404

    if conversation.turns_used >= agent.MAX_FOLLOWUP_TURNS:
        return jsonify(
            ok=False,
            error=f"You've used all {agent.MAX_FOLLOWUP_TURNS} follow-up rounds for this draft.",
            output="",
            log=[],
        ), 400

    try:
        result = agent.continue_case_study(conversation, answer)
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, error=f"Unexpected error: {exc}", output="", log=[]), 500

    result["generation_id"] = generation_id
    result["turns_remaining"] = max(0, agent.MAX_FOLLOWUP_TURNS - conversation.turns_used)
    status_code = 200 if result["ok"] else 502
    return jsonify(result), status_code


@app.route("/api/finalize", methods=["POST"])
def api_finalize():
    if not session.get("credentials"):
        return jsonify(ok=False, error="Not signed in.", output=""), 401

    data = request.get_json(silent=True) or {}
    generation_id = (data.get("generation_id") or "").strip()

    _cleanup_conversations()

    conversation = _get_owned_conversation(generation_id)
    if conversation is None:
        return jsonify(ok=False, error="This draft is no longer available - generate a new one.", output=""), 404

    result = agent.finalize_case_study(conversation)
    return jsonify(result), 200


@app.route("/api/export_doc", methods=["POST"])
def api_export_doc():
    if not session.get("credentials"):
        return jsonify(ok=False, error="Not signed in.", url=""), 401

    data = request.get_json(silent=True) or {}
    generation_id = (data.get("generation_id") or "").strip()

    _cleanup_conversations()

    conversation = _get_owned_conversation(generation_id)
    if conversation is None:
        return jsonify(ok=False, error="This draft is no longer available - generate a new one.", url=""), 404

    final = agent.finalize_case_study(conversation)
    docx_bytes = agent.build_case_study_docx(conversation.folder_name, final["sections"])
    doc_name = f"{conversation.folder_name} - Case Study"

    try:
        created = drive_client.upload_docx_as_google_doc(doc_name, docx_bytes)
    except HttpError as exc:
        if exc.resp.status == 403 and "insufficient" in (exc.reason or "").lower():
            return jsonify(
                ok=False,
                error="Your Google sign-in doesn't have permission to create files yet. Sign out and sign back in, then try again.",
                reauth_required=True,
                url="",
            ), 403
        return jsonify(ok=False, error=f"Google Drive error: {exc.reason or exc}", url=""), 502
    except Exception as exc:  # noqa: BLE001 - last-resort guard, matches other routes
        return jsonify(ok=False, error=f"Unexpected error: {exc}", url=""), 500

    url = created.get("webViewLink") or f"https://docs.google.com/document/d/{created.get('id')}/edit"
    return jsonify(ok=True, url=url, error=""), 200


def main() -> None:
    print("Portfolio Agent web UI - http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
