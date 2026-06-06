# TODOS

_Last updated: 2026-06-06_

## Current Focus
Transhipment parcels feature complete on `transhipment-parcels`: record / find / update (with weight→已入库拍照 auto-coupling) / multi-SKU equal-split via tracking / settle_shipping / apply_exchange_rate (including summary row) / parcel_summary. First real batch (6月有易, 1811.06 RMB / 228.96 EUR) settled live. Merging branch to main.

## Open Questions / Blockers
- Bug 1a recurrence: dispatch-layer dedup on `append_to_inbox` shipped in `ee5bd6d` (lowercase exact-title match). Keep watching — if duplicates still appear via a different path (update_inbox_item misuse, LLM avoiding append entirely), need deeper fix.
- Late-alert "skip" end-to-end: the push → user reply → LLM calls `skip_remaining_alerts` → confirm chain hasn't been exercised live. Verify LLM correctly identifies intent and doesn't accidentally call `set_status` as a side effect.

## Todo
- [ ] (post-v0.1) Handle reversal scenarios: when the user reverses a recent decision (e.g. cancelled "Model Y curtain" then "I bought it" — meaning Model Y), AnthropicBackend currently does not infer the reference (Opus 4.7 follows the prompt literally; Claude Code explores files more broadly). Prefer fixing via a combined `find_item` tool that searches both inbox and archive — keeps the system prompt lean instead of growing it with edge cases.
- [ ] (post-v0.1) Cross-device data sync: inbox.md / archive.md / history/ currently live only on whichever device the bot runs on. Simplest scheme — periodic git commit/push to a separate private data repo from Pi, optional pull from Mac for read-only viewing. Adds version history as a side benefit.
- [ ] (post-v0.1) Local LLM backend: add `LocalBackend` (Ollama/llama.cpp) targeting the home PC's local Gemma, switchable via env var alongside AnthropicBackend. Privacy story for portfolio — sensitive data never leaves home network. Spike first on capture-only flow to validate Chinese + tool-use reliability before expanding. Constraints: Pi→PC must be on LAN, PC must stay on 24/7.
- [ ] (post-v0.1) Event log: capture past events (e.g. "今天洗车了") into an `events.md` alongside inbox/archive — timestamp + description — so the user can later ask "上次洗车是啥时候". Reuses the capture pipeline; needs intent disambiguation in the prompt/tools (past event vs. future todo) and a query tool that searches by keyword and returns the most recent match with elapsed time. Naturally pairs with the markdown parser work from per-item reminders.
- [ ] (post-v0.1) History file rotation: when `data/history/<chat_id>.jsonl` grows past ~10 MB, truncate to last 800 lines on bot startup. Currently unnecessary at expected message rate (years of headroom), but trivially added if scale changes. See ADR 0001.
- [ ] (portfolio) Backfill remaining ADRs: 0004 markdown files as storage (vs SQLite/DB) and 0005 Docker for Pi deployment (vs venv+systemd). ADRs 0002 (LLM backend abstraction) and 0003 (self-written agent loop) already shipped.
