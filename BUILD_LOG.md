
11/09/2026 (commit 1d0b3e1 "Initial project setup")
Time spent: ~10-15 min (estimated - no prior commit this session to measure from)
Tokens: ~1-2K (rough estimate)
Shipped: Created plan/planning.md with the initial one-paragraph project pitch - a portfolio agent that turns scattered project materials (presentations, research, Figma exports, images, notes) into a polished case study.

11/09/2026 (commit c3cd274 "Updated skill breakdown")
Time spent: ~4 min (measured - time since previous commit)
Tokens: ~2-3K (rough estimate)
Shipped: Added the skill breakdown to the plan: Portfolio guide, Intake, Analyst, Copywriter, Visual curator, Layout, Final Assembly, Critic, and the overall Agent Role.

11/09/2026 (commit fb1f4fa "updated mvp and final scope")
Time spent: ~26 min (measured - time since previous commit)
Tokens: ~3-5K (rough estimate)
Shipped: Defined MVP (text/photo upload + portfolio guide + generated layout for approval) and a broader "Final scope" vision (Figma/Drive/Notion/audio sources, near-autonomous publish-ready output).

[Merged via PR #1 "Updated plan" on 12/09/2026]

17/09/2026 (commit afe8d7f "Updated MVP versus Final distinction")
Time spent: ~15-20 min (estimated - session resumed after a gap, no same-session anchor)
Tokens: ~4-6K (rough estimate)
Shipped: Reworked the plan to mark each skill (Intake, Analyst, Copywriter, Visual curator, Layout, Critic) with explicit MVP vs. Final scope, added a Framer MCP integration item, renamed plan/planning.md to plan.md.

17/09/2026 (commits a15f11b/51fa61c/27f3876 - stash/branch-reset housekeeping)
Time spent: ~29 min (measured - time since previous commit)
Tokens: ~0-1K (git operations, not authored content)
Shipped: Stashed and recovered WIP while resetting the assessment-2-trisha branch. Only real content change: added a placeholder .env (CLIENT_ID/CLIENT_SECRET) ahead of the Drive OAuth setup. Two of the three commits have no file changes (pure stash bookkeeping).

17/09/2026 (commit 5a6f9db "Save work before Assessment 2")
Time spent: ~16 min (measured - time since previous commit)
Tokens: ~1K (rough estimate)
Shipped: Added the BUILD_LOG.md template.

17/09/2026 (commit 7dc6178 "Add portfolio guide skill")
Time spent: ~14 min (measured - time since previous commit)
Tokens: ~8-12K (rough estimate)
Shipped: Drafted the first version of skills/portfolio_guide.md (case study structure, writing style, authenticity checks, missing-information handling). This file was later deleted from the working tree and recreated/expanded during the 18/09/2026 session.

18/09/2026
Time spent: ~5-6 hrs (approx. - not tracked precisely)
Tokens: ~800K-1.2M (rough estimate, not a measured count)
Shipped: Built a local Portfolio Agent (Python/Flask) that turns a named Google Drive folder into a design case study draft. Retrieves the folder's contents (Google Docs/Sheets/Slides, plain text, PDFs) via OAuth-authenticated Drive API, feeds it to Gemini in a single request per generation, and returns a plain-text case study following the agent's role/skill instructions. Includes: strict retrieval scoping (never escalates beyond the target folder), rate-limit-aware retries, a one-page web UI with loading/error states, and content caps to keep each request within free-tier limits.

19/09/2026 (commit 6c784a0 "Add agent, skill, Procfile, requirements, and config files")
Time spent: ~6-8 hrs (approx. - spans several untracked local iterations folded into one commit; not tracked precisely)
Tokens: ~1-1.5M (rough estimate, not a measured count)
Shipped: Rebuilt the agent end-to-end on top of the 18/09 prototype. Fixed Gemini's Interactions API hanging indefinitely (client-level timeouts weren't reliable, so added a ThreadPoolExecutor-based hard deadline), then switched the LLM provider to Groq (openai/gpt-oss-120b) for reliability. Tried a Gemini(images)+Groq(text) hybrid, abandoned it once Gemini's daily quota was exhausted, and ultimately removed image processing entirely in favor of a text-only Image Placeholder Skill that suggests where images belong without ever reading them. Rewrote agent.md and portfolio_guide.md for narrative/storytelling quality, incorporating the user's own case-study-writing notes (kept in the `reference` file). Added a deterministic "Insufficient Information to Draft Study" check covering unreadable, off-topic, and relevant-but-too-short content. Added the web-based OAuth flow (per-user session credentials, replacing the single shared local token) plus the Procfile/requirements/config groundwork needed to deploy to Railway with multi-user Google sign-in restricted to test users.

19/09/2026 (commit 38a77fe "Fix PKCE code verifier not persisting across OAuth login/callback")
Time spent: ~55 min (measured - time since previous commit)
Tokens: ~15-20K (rough estimate)
Shipped: Threaded the OAuth `code_verifier` through the Flask session between /auth/login and /auth/callback, fixing a "Missing code verifier" sign-in failure hit while testing the deployed multi-user web flow.

19/09/2026 (commit a13f68c "Add .docx support to Drive file reading")
Time spent: ~13 min (measured - time since previous commit)
Tokens: ~8-10K (rough estimate)
Shipped: Added python-docx-based text extraction (paragraphs and table cells) alongside the existing PDF path in drive_client.py, so Word documents in a project folder are read like any other source file.

19/09/2026 (commit 176c97f "Fix missing narrative headlines and Missing Info section on thorough docs")
Time spent: ~14 min (measured - time since previous commit)
Tokens: ~15-20K (rough estimate)
Shipped: Removed an escape hatch that let the model skip writing narrative section headlines, and restructured agent.md's Step 8 so producing a Case Study Draft no longer skips the Missing Information/Questions check. Verified against a real failing .docx test case (a thorough source doc that was still generating literal section labels and no missing-info section) pasted by the user.

19/09/2026 (commit 1beae8b "Remove retrieval log panel, add usage directions to the web UI")
Time spent: ~12 min (measured - time since previous commit)
Tokens: ~8-10K (rough estimate)
Shipped: Removed the debug retrieval-log panel from the web UI and added a plain-language "how to use this" directions block above the input field, describing supported file types and what to expect from the output.

19/09/2026 (commit 7e5a648 "Increase gunicorn timeout so slow generations don't get killed mid-request")
Time spent: ~12 min (measured - time since previous commit)
Tokens: ~3-5K (rough estimate)
Shipped: Raised gunicorn's worker timeout to 300s (from the 30s default) after slow generations were getting killed mid-request, which was surfacing to users as a broken "SyntaxError" JSON-parse failure in the browser.

19/09/2026 (commit a899f5f "Remove Google Sheets from the directions text")
Time spent: ~10 min (measured - time since previous commit)
Tokens: ~2-3K (rough estimate)
Shipped: Removed Google Sheets from the advertised supported file types in the UI directions text, per user request.

19/09/2026 (commit 4139f10 "Add gunicorn access logging and a real-time generation log")
Time spent: ~0-2 min (measured - time since previous commit)
Tokens: ~5-8K (rough estimate)
Shipped: Added `--access-logfile -` to gunicorn for request-level visibility in Railway's log stream, and a `LiveLog` class that prints each generation step as it happens for real-time debugging of in-flight requests.

20/09/2026 (commit 29350dd "Fail fast on long Groq rate-limit backoffs instead of hanging the request")
Time spent: ~1 hr 12 min (measured - time since previous commit)
Tokens: ~50-70K (rough estimate)
Shipped: Diagnosed a live production hang (a folder request blocking 2+ minutes, surfacing as the same broken "SyntaxError" JSON-parse failure) via real-time Railway log capture, and traced it to Groq's Retry-After header suggesting a 257s wait that the app was honoring literally with `time.sleep()`. Added a capped fail-fast path (MAX_RATE_LIMIT_WAIT_S) so any suggested wait over 20s raises immediately with a clear "try again shortly" message instead of blocking the request, verified with a synthetic test reproducing the exact 257s scenario, and confirmed deployed and running on Railway.
