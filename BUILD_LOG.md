
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

22/09/2026 (commit 2def867 "Add follow-up revision rounds; cap generation wall-clock time")
Time spent: ~2-3 hrs (approx. - spans an earlier untracked local fix folded into this commit; not tracked precisely)
Tokens: ~150-200K (rough estimate)
Shipped: Two pieces. First, enforced an overall wall-clock budget (MAX_GENERATION_WALL_CLOCK_S) across prefetch and tool-call iterations, so a slow folder can't run past gunicorn's own --timeout and get killed mid-request. Second, and the larger piece: let users answer the agent's own "Missing Information"/"Questions for the User" follow-ups instead of dead-ending there. The draft is parsed into title/body sections (split on a deterministic "===SECTION===" delimiter, chosen because strip_markdown's horizontal-rule regex would silently eat a "---" one); each follow-up round is a separate, stateless Groq call - no Drive tools, no growing message history - that returns only the section a user's answer affects, capped at MAX_FOLLOWUP_TURNS rounds; a non-LLM finalize step then splices the accumulated per-round updates onto the original draft to produce the complete case study. New endpoints (POST /api/answer, POST /api/finalize) and web UI (answer field, round counter, per-round update list, Finish button). Verified via unit-level checks of the section parse/join/merge helpers and a full generate/answer/answer/over-limit/finalize/unowned-id flow through Flask's test client with stubbed model calls; not yet exercised against live Groq/Drive.

22/09/2026 (commit 3188933 "Fix leaking ===SECTION=== markers and bold image placeholder labels")
Time spent: ~20-30 min (measured - time since previous commit, includes user-reported bug triage)
Tokens: ~30-40K (rough estimate)
Shipped: Fixed a bug found against the live Railway deployment: parse_sections only split on the "===SECTION===" delimiter when it was alone on its own line, so a delimiter the model ran directly onto the end of the preceding text (e.g. "[Suggested image: ...]===SECTION===Context", no line break) was missed and leaked into visible output. Switched to a plain literal split wherever the delimiter occurs, and added a safety-net strip of any leftover literal delimiter in join_sections (covers every place sections become displayed text). Also bolded the "Suggested image:" label in image placeholders for readability - rendering still never uses innerHTML on model output; the web UI builds the label as a <strong> DOM node via createElement/textContent while the placeholder's own description still goes through createTextNode like the rest of the draft.

22/09/2026 (commit 2c4dad9 "Drop Missing Information/Questions from the finalized case study")
Time spent: ~10 min (measured - time since previous commit)
Tokens: ~10-15K (rough estimate)
Shipped: Fixed another user-reported issue: clicking Finish & Build Final Case Study was shipping any never-answered question or still-open gap straight into the "final" case study, since finalize_case_study just merged section_updates onto original_sections and joined the result as-is. Now strips both utility sections (Missing Information, Questions for the User) from finalize's output - the finished case study is meant to read as the polished, presentable piece, not the working draft with its open-gaps scaffolding still attached. Revision rounds are unaffected, since continue_case_study builds its context from merge_sections directly and still needs to see the current Missing Information/Questions state.

22/09/2026 (commit 9230ab4 "Render real section headings instead of one flat text block")
Time spent: ~25-30 min (measured - time since previous commit, includes a plan-and-report pass before implementing)
Tokens: ~40-50K (rough estimate)
Shipped: User feedback: section titles read as a dense, unstyled block with no visual hierarchy against the body text. Proposed a fuller markdown-derived h1/h2/h3 plan first (per the user's request to plan before executing) but the user scoped it down to one heading level - bold, slightly larger than body text - plus consistent spacing between sections. The backend already parsed every draft into {title, body} sections but discarded that structure before it reached the browser, sending one flattened string instead; kept "sections" in generate's response and added it to continue_case_study's and finalize_case_study's return values too, then switched the web UI to render real DOM per section (a bold .section-title div, a normal-weight .section-body div, consistent CSS margin between sections) instead of one flat text block, with a fallback to the old rendering if "sections" is ever missing. The existing "Suggested image:" bolding was refactored into a shared helper reused by both render paths.

22/09/2026 (commit 90ae7fc "Render image placeholders as rectangle boxes, not inline text")
Time spent: ~15 min (measured - time since previous commit, includes a plan-and-report pass first)
Tokens: ~15-20K (rough estimate)
Shipped: User feedback: "[Suggested image: ...]" placeholders still read as part of the surrounding prose, just bolded - asked whether they could render as rectangle boxes instead. Planned first (per user request), confirmed a plain bordered box with no icon over an icon+label version, then changed appendInlineText so each placeholder match becomes its own block-level .image-placeholder div (bold label, description caption, dashed border, light background) appended as a sibling of the surrounding text nodes, instead of inline bolded text - a block element amid inline text naturally breaks the flow on both sides, so no other rendering code needed to change, and the fix applies everywhere a draft renders (initial draft, a revision round's section, the finalized case study) since they all share this one function. Verified via a simulated DOM build that surrounding prose text is preserved intact around the new block.
