# 0001. Conversation history mechanism

- **Status**: Decided
- **Date**: 2026-04-28
- **Tags**: #v0.1.1 #ux #backend

## Problem

The bot loses conversational context between messages. Two concrete sub-problems:

1. **Pronoun / short-reference resolution** — replies like "29 号", "那条", "改成 18:00" can only be resolved if the bot remembers what was just discussed.
2. **Multi-turn flows** — when the bot asks a clarifying question and the user replies briefly, the next call has no record of the question.

Surfaced in real use within hours of v0.1 ship:

```
bot: 已标记 Tralee 营地电话完成,并新建了"在Tralee附近找替代营地或住宿"待办。要不要设个截止日期?
me:  29号
bot: 29 号要做什么?是新增任务、修改某项的截止日期,还是查询那天的安排?
```

This is a daily-driver UX problem, not an edge case — natural conversation hits it constantly.

## Context

- Production runs `AnthropicBackend` on Raspberry Pi via Docker. Each `chat()` is a fresh `client.messages.create` call; the Anthropic API itself is stateless, so the client must send the full conversation each call.
- Dev sometimes runs `ClaudeCodeBackend` on Mac (shell out to `claude -p`). Each invocation spawns a new Node subprocess with no inherent memory unless `--resume <session-id>` is passed.
- Existing `LLMBackend.chat(user_message, system_prompt)` is stateless by design — no history parameter.
- The bot uses `python-telegram-bot` (PTB), which provides per-chat `chat_data` dict and optional `PicklePersistence` for free.
- Storage layer (`storage/markdown.py`) handles inbox/archive but is not designed for conversation logs.
- v0.1 deliberately punted this for simplicity — turned out to be a wrong call (the cost surfaced repeatedly within hours of v0.1 ship).

## Options considered

| | History storage | Survives restart | Audit | New deps |
|---|---|---|---|---|
| **A. In-process dict** | bot.py memory | ❌ | hard | none |
| **B. JSONL files** | `data/history-{chat_id}.jsonl` | ✅ | `cat`-able | none |
| **C. Append to markdown** | `data/history.md` | ✅ | readable | none |
| **D. `claude -p --resume`** | Claude Code's session file | ✅ (CC's) | CC internal | none |
| **E. PTB `chat_data` + `PicklePersistence`** | bot state pickle file | ✅ | binary | none |
| **F. SQLite** | `data/bot.db` | ✅ | sqlite3 cli | sqlite3 (stdlib) |

### Rejected outright

- **Anthropic server-side session** — does not exist; the API is stateless. Prompt caching helps with cost but not memory.
- **Compaction beta** (`compact-2026-01-12`) — solves long-context problems we don't have yet; overkill for v0.1.1.
- **Vector / RAG retrieval** — wrong tool for short-reference resolution; designed for retrieval over knowledge bases, not last-N-message recall.

### Top three deep dive

**A. In-process dict** — simplest, smallest surface, no external dependencies. Lost on container restart. On Pi the bot rarely crashes but `docker compose up -d --build` (every code update) loses history. Acceptable if updates are infrequent; awkward during active dev.

**B. JSONL files** — most transparent (`tail -f data/history/<id>.jsonl` to debug), conceptually consistent with the existing markdown-on-disk pattern. Has to manage file rotation as history grows long.

**E. PTB `chat_data` + `PicklePersistence`** — uses python-telegram-bot's built-in per-chat state mechanism. Persistence "free" via PTB's `PicklePersistence`. Standard PTB idiom. Pickle files are opaque (binary, Python-specific, fragile across Python upgrades).

## Decision

**Option B — JSONL files at `data/history/<chat_id>.jsonl`**, with a sliding window combining turn count and elapsed time (drop messages older than 30 minutes; cap at 12 messages = 6 turns regardless), each entry tagged with a Unix timestamp.

Initial proposal was option E (PTB `chat_data` + `PicklePersistence`). I pushed back on the platform-coupling cost: E binds history storage to python-telegram-bot's data model. B keeps storage as plain Python + filesystem, decoupled from the messaging platform.

## Rationale (for chosen B)

Ordered by priority weighting:

1. **Platform-independent** — pure Python + filesystem. A future `messaging/whatsapp.py` or `messaging/web.py` reuses the same storage layer with no migration; only the chat-id key changes.
2. **Survives restart** — beats A. Active conversations keep context across `docker compose up -d --build` (which happens often during dev iteration).
3. **Auditable** — `tail data/history/<chat_id>.jsonl` is the highest-leverage debug tool we have. Pickle (E) requires a Python script to inspect.
4. **Append-only is simple** — each write is one line appended, no race conditions, no risk of corrupting old data.
5. **File size is not a real problem** — at the expected rate (~30 turns/day), the file grows ~3 MB/year. Five years = 16 MB. Rotation is unnecessary for the foreseeable scale and trivially added later if needed.

Trade-off accepted: more code than E. Each line does an explicit, comprehensible thing.

## Consequences

**Easier:**
- Multi-turn conversation works — the whole point.
- Debugging history bugs: `tail data/history/<chat_id>.jsonl`, no script needed.
- Cross-platform extension: any future `messaging/<platform>.py` calls the same `read_history(chat_id)` / `append_turn(chat_id, ...)` API.
- Manual edits / inspection: you can hand-edit a JSONL file if some message went sideways.

**Harder / new concerns:**
- File I/O code to maintain (compared to "free" via PTB persistence).
- Token cost per `chat()` call grows with the history window. Not measured at decision time — re-check if usage scales up.
- File-system concurrency: if we ever run multiple bot instances against the same data dir, append might interleave. Single-instance for the foreseeable future, so non-issue, but document it.

**Decisions deferred (recorded as TODO, not done in v0.1.1):**
- File rotation when `data/history/<chat_id>.jsonl` grows past ~10 MB. Currently unnecessary at the expected rate (years of headroom). Add a rotate-on-startup helper when actually needed.
- ClaudeCodeBackend history support (via `claude -p --resume <session-id>`). Skipped because Pi runs anthropic backend and CC's own exploratory behavior partially compensates for missing history.
- Compaction (Anthropic beta `compact-2026-01-12`) — only relevant if conversations grow long enough to approach context window limits. Not the case at current scale.

## Decisions on the open questions

1. **Window strategy**: **Both — turn count (12 messages = 6 turns) AND elapsed time (30 minutes), whichever is shorter.** Real conversations often resume after long pauses; the time bound avoids attaching a stale "yes" to a question asked hours ago.
2. **What to store**: **Only `user_message` + final `assistant_text`.** Tool-use trajectory bloats history and doesn't match the user's mental model of "what was said."
3. **ClaudeCodeBackend**: **Skip.** Stays stateless. CC's exploratory tool-use partially compensates (it discovers archive items the user references implicitly).
4. **Time decay precision**: **Store a Unix `ts` per entry.** Filter on read by `now - ts < 1800`. The maxlen=12 hard cap is a fallback for when many messages happen within 30 minutes.

## References

- TODOS.md — "Conversation context across messages" item (this decision when decided supersedes that line)
- `llm/anthropic_api.py` — agent loop where history will be injected as the messages prefix
- `bot.py` — `handle_message` where history will be read/written via `storage/history.py` (`read_history` / `append_turn`)
- python-telegram-bot docs — `PicklePersistence`, `chat_data`
