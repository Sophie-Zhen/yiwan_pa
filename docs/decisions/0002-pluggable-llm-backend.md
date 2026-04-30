# 0002. Pluggable LLM backend abstraction

- **Status**: Decided
- **Date**: 2026-04-26 (decided), 2026-04-29 (recorded retrospectively)
- **Tags**: #v0.1 #architecture #llm

> Backfilled ADR. The decision was made on 2026-04-26 during initial project scaffolding when `llm/base.py` and `llm/claude_code.py` were first written; this record is being authored retrospectively as part of portfolio polish so the rationale is preserved alongside the code.

## Problem

The bot needs to call an LLM to understand natural-language messages. Two practical paths exist for getting Claude into the bot:

1. **Shell out to the local `claude` CLI** (`claude -p`) — reuses my Claude Code subscription, no API key, no per-token cost. But spawns a Node subprocess every call, leaks behavior from the user's global `CLAUDE.md` and harness defaults, and adds 1-2 seconds of cold-start latency.
2. **Use the `anthropic` Python SDK directly** — pay-per-token, predictable behavior, programmatic control over tools and the agent loop. Best for production but costs money during dev iteration.

Each path is right for a different phase (dev vs prod), and there are likely future backends I'll want — a local LLM (Ollama on a home PC) for privacy, or another provider for comparison. The decision is whether to commit early to one path or design for plurality.

## Context

- Single user (me) but explicit goal of being able to swap LLMs without changing the bot's business logic. The motivation is partly engineering hygiene, partly portfolio narrative.
- The bot has exactly one place where it actually calls the LLM — the message handler in `bot.py`. Clean insertion point for an abstraction layer.
- I have a Claude Code subscription, so I can dev for free; production should be the Anthropic API for predictability and uptime.
- I am building this project as both a personal tool and a learning vehicle. The act of writing the abstraction (and later, the SDK-based agent loop on top of it) is itself learning content.

## Options considered

| | Approach | Pros | Cons |
|---|---|---|---|
| **A** | Hardcode `anthropic` SDK calls in `bot.py` | Simplest. Fewer files, fewer concepts. | Locked to one provider. Pay-per-token even during heavy dev iteration. No path to leverage the existing CC subscription. |
| **B** | Hardcode shell-out to `claude -p` in `bot.py` | Free during dev. No API key dance. | Locked to CC. Production-fragile (Node subprocess cost, behavior leaks from user CLAUDE.md, no fine control). Hard to add a non-CC backend later. |
| **C** | Define an `LLMBackend` abstract class, implement multiple backends, select via env var | Dev/prod separation. Future backends (local LLM, OpenAI, etc.) slot in as one new file each. Demonstrates dependency inversion explicitly. | More code (3 files vs 1). Interface design choices made upfront constrain future backends. |

### Rejected outright

- **Third-party agent frameworks (LangChain, LlamaIndex, etc.)** — these provide LLM provider abstractions and would solve the same problem more comprehensively. Not deeply evaluated for this project: the abstraction need was small (one method, two implementations), the dependency surface was big, and a primary goal of the project is to understand the underlying mechanics rather than configure someone else's framework. A future, more ambitious project would warrant a real evaluation.

## Decision

**Option C** — abstract `LLMBackend` interface with two concrete implementations (`ClaudeCodeBackend` and `AnthropicBackend`), selected at startup by the `LLM_BACKEND` env var.

## Rationale

- **Resolves the dev-vs-prod tension**: same `bot.py`, different env var. Iterate for free during dev with `LLM_BACKEND=claude_code`, run production on Pi with `LLM_BACKEND=anthropic`.
- **Future-proofs**: a third backend (local LLM, OpenAI, whatever) is one new file plus a line in `get_backend()`. Bot logic doesn't change.
- **Strong portfolio signal**: dependency inversion is a classic interview topic; having actually shipped it (with two real implementations, not just a single-impl interface) is a credible answer to "tell me about an architectural decision you made."
- **Keeps optionality cheap**: the cost is ~50 LoC of abstract class + factory + two backends, vs ~20 LoC for hardcoding one. Acceptable for the flexibility it buys.

## Consequences

**Easier:**
- Switching LLM backend = one env var change. Tested in practice — the same bot ran on `claude_code` for dev, then `anthropic` for production with zero code changes.
- New backends (per the TODOS "Local LLM backend" item) are mechanically simple to add: subclass `LLMBackend`, register in `get_backend()`, set the env var.
- Future testing — a mock `LLMBackend` would be trivial to write for unit tests.

**Harder:**
- Three files (`base.py`, `claude_code.py`, `__init__.py` factory, plus eventually `anthropic_api.py`) where one would do.
- The interface shape (`chat(user_message, system_prompt, ...) -> str`) is now a contract — adding capabilities like streaming or multi-modal input means updating both the interface and every implementation.
- Slightly more cognitive load for new readers of the repo: "where does the LLM call actually happen?" is one indirection deeper.

**To revisit:**
- The interface evolved once already to support conversation history (see ADR 0001 — `history` parameter added to `chat()`). Future evolutions are likely:
  - **Streaming** if response latency becomes user-visible.
  - **Multi-modal** if voice or image messages are supported (Telegram supports both).
  - **Token budgets** if cost control becomes a concern.
- Should the factory `get_backend()` return a per-call instance or a singleton? Currently per-call (singletons of cheap objects). If a backend grows expensive state (a connection pool, a warmed cache), revisit.

## References

- `llm/base.py` — `LLMBackend` abstract class
- `llm/__init__.py` — `get_backend()` factory, dispatches by `LLM_BACKEND` env var
- `llm/claude_code.py` — `ClaudeCodeBackend` (subprocess-based)
- `llm/anthropic_api.py` — `AnthropicBackend` (see [ADR 0003](0003-self-written-agent-loop.md) for its agent loop, when written)
- ADR [0001](0001-conversation-history.md) — how the interface was extended for conversation history
- TODOS.md — "Local LLM backend" item (planned third backend)
