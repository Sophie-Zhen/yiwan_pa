# TODOS

_Last updated: 2026-05-20_

## Current Focus
Push strategy Phase 1 shipped 2026-05-20 (commit `44a0ed3`): per-item T-N alerts with normal + late-fire paths, evening digest at 21:00 with empty-state suppression, morning digest stale section with mark-on-send-success throttling. Now dogfeed period — observe how the three channels feel in real use before picking the next post-v0.1 item.

## Open Questions / Blockers
- Bug 1a recurrence: continue dogfeed observation. If dedup recurs in inbox.md, backup fix is dispatch-layer same-chat() dedup on `append_to_inbox` (lowercase-substring title match).
- Alert message language: scheduler.py currently emits English alert strings. I chat in Chinese — observe during dogfeed whether English feels jarring; if so, swap to Chinese or move strings to a template module.
- Late-alert "skip" end-to-end: the push → user reply → LLM calls `skip_remaining_alerts` → confirm chain hasn't been exercised live. Verify LLM correctly identifies intent and doesn't accidentally call `set_status` as a side effect.

## Todo
- [ ] (post-v0.1) List grouping: capture multi-item lists (购物清单 / 行李清单) as a single inbox item with checkable sub-items, not N separate entries. Needs storage schema extension (e.g. `children` field on `Item`, or a new `ListItem` type), prompt instruction to recognise list intent during capture, and a sub-item-completion tool. Surfaced from dogfeed — 10 件购物 = 10 inbox lines was unmanageable.
- [ ] (post-v0.1) `/todos` command + digest interactivity: on-demand display of all pending items including no-due-date ones (current `/digest` only renders today's morning-digest format and misses no-due items). After completing items during the day, user should be able to re-render the remaining list without restating the request. Surfaced from dogfeed — observed needing to "不停 trigger digest" to track remaining work.
- [ ] (post-v0.1) Handle reversal scenarios: when the user reverses a recent decision (e.g. cancelled "Model Y curtain" then "I bought it" — meaning Model Y), AnthropicBackend currently does not infer the reference (Opus 4.7 follows the prompt literally; Claude Code explores files more broadly). Prefer fixing via a combined `find_item` tool that searches both inbox and archive — keeps the system prompt lean instead of growing it with edge cases.
- [ ] (post-v0.1) Cross-device data sync: inbox.md / archive.md / history/ currently live only on whichever device the bot runs on. Simplest scheme — periodic git commit/push to a separate private data repo from Pi, optional pull from Mac for read-only viewing. Adds version history as a side benefit.
- [ ] (post-v0.1) Local LLM backend: add `LocalBackend` (Ollama/llama.cpp) targeting the home PC's local Gemma, switchable via env var alongside AnthropicBackend. Privacy story for portfolio — sensitive data never leaves home network. Spike first on capture-only flow to validate Chinese + tool-use reliability before expanding. Constraints: Pi→PC must be on LAN, PC must stay on 24/7.
- [ ] (post-v0.1) Event log: capture past events (e.g. "今天洗车了") into an `events.md` alongside inbox/archive — timestamp + description — so the user can later ask "上次洗车是啥时候". Reuses the capture pipeline; needs intent disambiguation in the prompt/tools (past event vs. future todo) and a query tool that searches by keyword and returns the most recent match with elapsed time. Naturally pairs with the markdown parser work from per-item reminders.
- [ ] (post-v0.1) History file rotation: when `data/history/<chat_id>.jsonl` grows past ~10 MB, truncate to last 800 lines on bot startup. Currently unnecessary at expected message rate (years of headroom), but trivially added if scale changes. See ADR 0001.
- [ ] (portfolio) Backfill remaining ADRs: 0004 markdown files as storage (vs SQLite/DB) and 0005 Docker for Pi deployment (vs venv+systemd). ADRs 0002 (LLM backend abstraction) and 0003 (self-written agent loop) already shipped.
- [ ] (portfolio) ADR 0006 — per-item scheduler state schema (alerts / alerted / alerted_stale on Item, vs. sidecar file). Cited as TODO in storage/markdown.py comment; document the choice + reasoning while it's fresh.
