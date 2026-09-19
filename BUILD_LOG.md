
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
