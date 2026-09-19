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
from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for
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
      <p><strong>How to use this:</strong> enter the name of a project folder from your connected Google Drive. The agent reads the files inside it - Google Docs, Sheets, Slides, PDFs, and .docx files - and drafts a case study from what it finds.</p>
      <p>This works best when the folder's content is genuinely about your project and there's enough of it. If the content is too sparse, unrelated, or unreadable, the agent will say so rather than guessing. Alongside the draft, you may also get a few missing-information questions if something important isn't documented, and suggested spots to add your own images.</p>
    </div>

    <div id="status"><span class="spinner"></span>Retrieving files and drafting the case study...</div>
    <div id="error"></div>
    <div id="result"></div>
  </div>

  <script>
    const form = document.getElementById("form");
    const input = document.getElementById("folder_name");
    const button = document.getElementById("submit-btn");
    const statusEl = document.getElementById("status");
    const errorEl = document.getElementById("error");
    const resultEl = document.getElementById("result");

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
          body.textContent = data.output;
          resultEl.appendChild(body);
          resultEl.classList.add("visible");
        } else {
          errorEl.textContent = data.error || "Something went wrong.";
          errorEl.classList.add("visible");
        }
      } catch (err) {
        errorEl.textContent = "Request failed: " + err;
        errorEl.classList.add("visible");
      } finally {
        button.disabled = false;
        input.disabled = false;
        statusEl.classList.remove("visible");
      }
    });
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

    try:
        result = agent.generate_case_study(folder_name)
    except Exception as exc:  # noqa: BLE001 - last-resort guard so the page always gets JSON, never a raw 500 page
        return jsonify(ok=False, error=f"Unexpected error: {exc}", output="", log=[]), 500

    status_code = 200 if result["ok"] else 502
    return jsonify(result), status_code


def main() -> None:
    print("Portfolio Agent web UI - http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
