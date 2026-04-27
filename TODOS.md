# TODOS

_Last updated: 2026-04-27_

## Current Focus
Ship v0.1 — implement AnthropicBackend (task #11) with a self-written agent loop and tool definitions, then deploy to Raspberry Pi (task #12).

## Open Questions / Blockers
- (none currently)

## Todo
- [ ] Finish task #10 cleanup if not yet done: merge feat/digest into main, push main, delete feat/digest both locally and on remote
- [ ] Add AnthropicBackend with self-written agent loop using anthropic SDK (task #11)
- [ ] Deploy to Raspberry Pi with systemd unit for 24/7 uptime (task #12)
- [ ] (pre-release) Register command menu in @BotFather via `/setcommands` so /id, /digest, etc. show up as autocomplete in Telegram
- [ ] (post-v0.1) Per-item reminders: scheduler scans inbox for items whose due times are approaching and pushes per-item alerts (e.g. T-3h, T-2h before a flight). Needs a Python markdown parser in `storage/markdown.py`.
- [ ] (post-v0.1) Conversation context across messages so follow-ups like "change it to 18:00" resolve to the previous item. Either via `claude -p --resume` per chat, or by maintaining a messages history with a sliding window in AnthropicBackend.
- [ ] (post-v0.1) Handle reversal scenarios: when the user reverses a recent decision (e.g. cancelled "Model Y curtain" then "I bought it" — meaning Model Y), AnthropicBackend currently does not infer the reference (Opus 4.7 follows the prompt literally; Claude Code explores files more broadly). Prefer fixing via a combined `find_item` tool that searches both inbox and archive — keeps the system prompt lean instead of growing it with edge cases.
