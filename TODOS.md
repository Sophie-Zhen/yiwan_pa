# TODOS

_Last updated: 2026-04-28_

## Current Focus
v0.1 shipped — bot is running 24/7 on the Raspberry Pi via Docker, talking to Telegram, using AnthropicBackend. Decide which post-v0.1 item to tackle next: per-item reminders (most-used pain point) or conversation context (better UX) or reversal handling (smaller polish).

## Open Questions / Blockers
- (none currently)

## Todo
- [ ] (pre-release) Register command menu in @BotFather via `/setcommands` so /id, /digest, etc. show up as autocomplete in Telegram
- [ ] (post-v0.1) Per-item reminders: scheduler scans inbox for items whose due times are approaching and pushes per-item alerts (e.g. T-3h, T-2h before a flight). Needs a Python markdown parser in `storage/markdown.py` (already in place).
- [ ] (post-v0.1) Conversation context across messages so follow-ups like "change it to 18:00" resolve to the previous item. Either via `claude -p --resume` per chat, or by maintaining a messages history with a sliding window in AnthropicBackend.
- [ ] (post-v0.1) Handle reversal scenarios: when the user reverses a recent decision (e.g. cancelled "Model Y curtain" then "I bought it" — meaning Model Y), AnthropicBackend currently does not infer the reference (Opus 4.7 follows the prompt literally; Claude Code explores files more broadly). Prefer fixing via a combined `find_item` tool that searches both inbox and archive — keeps the system prompt lean instead of growing it with edge cases.
- [ ] (post-v0.1) Cross-device data sync: inbox.md / archive.md / history/ currently live only on whichever device the bot runs on. Simplest scheme — periodic git commit/push to a separate private data repo from Pi, optional pull from Mac for read-only viewing. Adds version history as a side benefit.
- [ ] (post-v0.1) Local LLM backend: add `LocalBackend` (Ollama/llama.cpp) targeting the home PC's local Gemma, switchable via env var alongside AnthropicBackend. Privacy story for portfolio — sensitive data never leaves home network. Spike first on capture-only flow to validate Chinese + tool-use reliability before expanding. Constraints: Pi→PC must be on LAN, PC must stay on 24/7.
- [ ] (post-v0.1) Event log: capture past events (e.g. "今天洗车了") into an `events.md` alongside inbox/archive — timestamp + description — so the user can later ask "上次洗车是啥时候". Reuses the capture pipeline; needs intent disambiguation in the prompt/tools (past event vs. future todo) and a query tool that searches by keyword and returns the most recent match with elapsed time. Naturally pairs with the markdown parser work from per-item reminders.
- [ ] (portfolio) Rewrite README for an outside reader: motivation, architecture diagram, how to run, current status. Current README assumes the reader is me.
- [ ] (portfolio) Add LICENSE file (MIT) — GitHub repo → Add file → Choose template → MIT.
- [ ] (portfolio) Add CHANGELOG.md, record what shipped in v0.1, keep updating per version. Strong signal for hiring review alongside commit history.
