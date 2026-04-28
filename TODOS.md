# TODOS

_Last updated: 2026-04-29_

## Current Focus
v0.1.1 shipped — bot now keeps a 6-turn / 30-minute conversation history (JSONL per chat). Entering a 2-week dogfooding period: use the bot daily, capture friction in FIELDNOTES.md (gitignored). The 2026-05-12 retro agent will surface what to build next based on actual usage rather than speculation.

## Open Questions / Blockers
- (none currently)

## Todo
- [ ] (post-v0.1) Per-item reminders: scheduler scans inbox for items whose due times are approaching and pushes per-item alerts (e.g. T-3h, T-2h before a flight). Needs a Python markdown parser in `storage/markdown.py` (already in place).
- [ ] (post-v0.1) Handle reversal scenarios: when the user reverses a recent decision (e.g. cancelled "Model Y curtain" then "I bought it" — meaning Model Y), AnthropicBackend currently does not infer the reference (Opus 4.7 follows the prompt literally; Claude Code explores files more broadly). Prefer fixing via a combined `find_item` tool that searches both inbox and archive — keeps the system prompt lean instead of growing it with edge cases.
- [ ] (post-v0.1) Cross-device data sync: inbox.md / archive.md / history/ currently live only on whichever device the bot runs on. Simplest scheme — periodic git commit/push to a separate private data repo from Pi, optional pull from Mac for read-only viewing. Adds version history as a side benefit.
- [ ] (post-v0.1) Event log: capture past events (e.g. "今天洗车了") into an `events.md` alongside inbox/archive — timestamp + description — so the user can later ask "上次洗车是啥时候". Reuses the capture pipeline; needs intent disambiguation in the prompt/tools (past event vs. future todo) and a query tool that searches by keyword and returns the most recent match with elapsed time. Naturally pairs with the markdown parser work from per-item reminders.
- [ ] (post-v0.1) History file rotation: when `data/history/<chat_id>.jsonl` grows past ~10 MB, truncate to last 800 lines on bot startup. Currently unnecessary at expected message rate (years of headroom), but trivially added if scale changes. See ADR 0001.
- [ ] (portfolio) Rewrite README for an outside reader: motivation, architecture diagram, how to run, current status. Current README assumes the reader is me.
- [ ] (portfolio) Add LICENSE file (MIT) — GitHub repo → Add file → Choose template → MIT.
- [ ] (portfolio) Add CHANGELOG.md, record what shipped in v0.1 / v0.1.1, keep updating per version. Strong signal for hiring review alongside commit history.
- [ ] (portfolio) Backfill ADRs for earlier non-trivial decisions (LLM backend abstraction, AnthropicBackend agent loop, markdown storage, Docker for Pi) — see docs/decisions/README.md format.
