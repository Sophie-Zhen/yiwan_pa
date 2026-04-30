# 0003. Self-written agent loop in AnthropicBackend

- **Status**: Decided
- **Date**: 2026-04-27 (decided), 2026-04-30 (recorded retrospectively)
- **Tags**: #v0.1 #architecture #llm #agent

> Backfilled ADR. Decision was made on 2026-04-27 while implementing `llm/anthropic_api.py`; recorded as an ADR on 2026-04-30 as part of portfolio polish.

## Problem

`AnthropicBackend` (the production LLM backend chosen in [ADR 0002](0002-pluggable-llm-backend.md)) needs to support **tool use**: the model can ask to call a function (`read_inbox`, `append_to_inbox`, etc.), the harness executes that function, the result feeds back to the model, and the cycle repeats until the model is done. This is the **agent loop** — the inner cycle every modern LLM agent runs on.

How should this loop be implemented? Multiple layers of abstraction are available, from "I write the loop by hand" to "the SDK runs the entire loop for me, I just decorate a function with `@tool`."

## Context

- Five tools to expose: `read_inbox`, `read_archive`, `append_to_inbox`, `update_inbox_item`, `move_to_archive`. All synchronous, all local file I/O via `storage/markdown.py`.
- The Anthropic API supports tool use natively: requests include a `tools` array (JSON schemas); responses contain `tool_use` blocks; the caller sends results back as `tool_result` blocks in a follow-up message.
- The project's primary justification is learning, not deliverable speed. Understanding *how* an agent loop works is the point, not just shipping one.
- ADR 0002 has already established the `LLMBackend` abstraction. Whatever's chosen here lives inside `AnthropicBackend.chat()`; the rest of the bot doesn't see it.

## Options considered

| | Approach | Pros | Cons |
|---|---|---|---|
| **A** | Manual agent loop: `while True` over `client.messages.create`, parse `tool_use` blocks, dispatch to a Python function, append `tool_result`, repeat until `stop_reason != "tool_use"` | Full control. Hooks for logging, instrumentation, edge-case branches anywhere in the loop. Forces understanding of the protocol. | ~200 LoC including tool definitions, dispatch, error handling. Every edge case (`refusal`, `max_tokens`, runaway loops) is explicit code. |
| **B** | Anthropic SDK's `@beta_tool` decorator + `client.beta.messages.tool_runner()` — the SDK auto-generates schemas from Python type hints and runs the loop | Far less code (~30 LoC). Schemas derived from function signatures. Future-aligned with Anthropic's direction. | Beta — API may change. The loop is a black box, defeating the learning goal. |

### Rejected outright

- **LangChain `AgentExecutor`** — same dependency-weight reasoning as the framework rejection in ADR 0002, now applied at the agent-loop layer rather than the LLM-provider layer.
- **Claude Agent SDK (`claude-agent-sdk`)** — Anthropic's higher-level agent SDK that packages the agent loop *and* a set of built-in Read/Write/Edit/Bash tools together. A reasonable choice for a more ambitious project. For v0.1 it's the wrong layer of abstraction. With `anthropic` SDK + a hand-written loop, every intermediate step (each `tool_use`, each retry, each thinking pass, each `tool_result`) is in code I wrote — fully traceable and modifiable. Adopting the Agent SDK means the loop is the SDK's, and intervention is only possible at whatever hook points the SDK exposes; everything else is opaque. The whole reason for self-writing the loop is to *not* have that opacity. (Secondary: the SDK's generic file tools also overlap awkwardly with the markdown-specific operations this project actually wants — `append_to_inbox` is not just "write a file" — so adopting the SDK would force a less precise tool surface as well.)

## Decision

**Option A** — write the agent loop manually in `llm/anthropic_api.py`.

## Rationale

- **Every step of the loop must be traceable, not opaque.** This is the dominant reason. Writing the loop by hand means each `tool_use` parse, each `tool_result` round-trip, each retry, each `stop_reason` branch is in *my* code. With a higher-level SDK (Agent SDK, AgentExecutor, etc.) the loop is the SDK's; I can only intervene at the hook points the SDK chose to expose — the rest is opaque. For a project whose main justification is understanding how agents work, opacity is a non-starter.
- **Learning value is the project's primary justification, not deliverable speed.** Hand-writing the loop builds understanding of the tool-use protocol that reading documentation alone doesn't deliver: how `tool_use` and `tool_result` blocks round-trip, why the assistant message must be appended verbatim before the user-side result, how `stop_reason` drives control flow, why `MAX_LOOP_TURNS` exists. With a framework, every one of these is hidden behind a method call.
- **Strong portfolio signal.** "I wrote the agent loop myself" backed by ~200 lines of working code is a specific, defensible answer to interview questions about LLM agents. It's evidence, not hand-waving.
- **Custom behaviour lands directly.** Top-level prompt caching, adaptive thinking, per-turn `usage` logging — all are simple modifications to the hand-written loop. With a framework, each is a fight against the abstraction at one of its few sanctioned hook points.
- **The scope is small enough that the cost is bounded.** Five tools, one user, simple data flow. ~200 LoC is a one-time write, not a maintenance burden — especially since each tool is two short blocks (a JSON schema and a dispatch case) right next to each other.

## Consequences

**Easier:**
- Reading the code: the loop is one function, top to bottom, no external concepts.
- Adding instrumentation: log tokens, log tool calls, count retries — print statements anywhere in the loop body.
- Handling new edge cases as they appear: add a branch on `stop_reason`.
- Changing model parameters per-turn (effort, max_tokens, thinking config) — they're direct kwargs on `messages.create`.

**Harder:**
- More edge cases handled by hand: `refusal`, `max_tokens`, runaway loops, network errors. Each is explicit code that needs to keep working.
- Tool schemas are hand-written JSON dicts rather than auto-generated from Python signatures. If a schema and its dispatch function drift, runtime errors only surface when that tool is exercised. (Mitigated by keeping schema and dispatch case in the same file, separated by ~30 lines.)
- More LoC to maintain. Tolerable at five tools; would become painful at fifty. The growth signal is real.

**To revisit:**
- **Tool-count threshold for migrating to `@beta_tool`**: if the tool surface grows past ~10-15 tools, schema-vs-function drift becomes a real maintenance burden. By then, the SDK's `@beta_tool` is likely GA and the trade-off flips.
- **Streaming**: the loop currently is non-streaming (we wait for full response, then act). If a future feature needs to stream output back to Telegram while the model is still generating, refactor to use streaming primitives.
- **Multi-agent / sub-agent orchestration**: if the project expands to "an agent that spawns sub-agents for sub-tasks", Claude Agent SDK becomes a much more credible choice. Don't pre-build for this; revisit when the need is concrete.

## References

- `llm/anthropic_api.py` — the actual implementation: tool schemas (top), `_execute_tool` dispatch (middle), `chat()` loop (bottom), ~200 lines total
- ADR [0002](0002-pluggable-llm-backend.md) — earlier decision about the `LLMBackend` abstraction in which this loop lives
- ADR [0001](0001-conversation-history.md) — the conversation-history layer that feeds prior turns into the loop's `messages` array
- Anthropic docs on tool use: <https://docs.anthropic.com/en/docs/build-with-claude/tool-use/overview>
- Anthropic Python SDK `tool_runner` (option B) reference: <https://github.com/anthropics/anthropic-sdk-python>
