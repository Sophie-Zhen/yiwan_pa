# 0007. Per-domain tool registry

- **Status**: Decided
- **Date**: 2026-06-13 (implemented), 2026-06-15 (recorded retrospectively)
- **Tags**: #architecture #llm #agent #refactor

> Backfilled ADR. The split was implemented on 2026-06-13 (commit `d06a796`); recorded as an ADR on 2026-06-15 while reconciling [ADR 0003](0003-self-written-agent-loop.md) and [ADR 0006](0006-scheduler-state-schema.md), both of which still pointed at the pre-split tool location.

## Problem

[ADR 0003](0003-self-written-agent-loop.md) put the whole tool layer — every tool's JSON schema, its dispatch branch, and the `chat()` agent loop — in one file, `llm/anthropic_api.py`. That ADR's own "Harder" consequence called it: "More LoC to maintain. Tolerable at five tools; would become painful at fifty. The growth signal is real."

The signal arrived. As scenarios were added (todos, parcels, investments, household spending + inventory, contracts, documents, CWI logbook), each new tool had to be declared in **three places** inside that one file — the import, the schema dict, and the dispatch branch — and the file grew well past its original ~200 lines. Adding a feature meant editing the same monolith three times and re-reading the whole thing to find the spots.

## Context

- Tools cluster cleanly by **domain**: the household-spending tools never touch the CWI-logbook tools. The monolith hid that structure — unrelated tools sat interleaved by accident of insertion order.
- The Anthropic prompt cache keys on a byte-stable prefix (system prompt + `tools` array). Any reshuffle of the tool list busts the cache, so whatever layout is chosen must keep `TOOLS` **order-stable** across runs.
- The agent loop itself ([ADR 0003](0003-self-written-agent-loop.md)) needs only two things from the tool layer: the schema list to send to the API, and a way to dispatch a `tool_use` block by name. It does not need to know how many tools there are or how they're grouped.
- A later goal — per-message domain routing, shipping only the active domain's tools per message to slim the cached prefix — would need tools addressable **by domain**, which the flat layout couldn't provide.

## Options considered

| | Approach | Pros | Cons |
|---|---|---|---|
| **A** | Keep the monolith — all schemas + dispatch in `anthropic_api.py` | No move; loop and tools in one file | The "three edits in one growing file" friction; no domain boundary; tools not addressable by domain |
| **B** | One module per domain under `llm/tooldefs/`, each owning its `SCHEMAS` list + `HANDLERS` map; a package `__init__.py` assembles `TOOLS` + a single `execute_tool()` | A scenario lives in exactly one file; `anthropic_api.py` stops changing when tools are added; domain grouping is explicit and addressable | One indirection layer (the registry); must guarantee order-stability + no duplicate handler names by construction |
| **C** | Auto-generate schemas from Python type hints (SDK `@beta_tool` / `tool_runner`) | No hand-written schema dicts | Rejected for the same reason as in [ADR 0003](0003-self-written-agent-loop.md): the loop goes opaque, defeating the learning goal. Orthogonal to *where* tools live anyway. |

## Decision

**Option B** — extract the tool schemas and dispatch out of `anthropic_api.py` into a `llm/tooldefs/` package, one module per domain.

- Each domain module (`todos`, `parcels`, `investments`, `expenses`, `contracts`, `documents`, `cwi`) exports a `SCHEMAS` list (its tool JSON schemas) and a `HANDLERS` dict (`name → callable`).
- `llm/tooldefs/__init__.py` is the registry: it flattens the per-domain `SCHEMAS` (in a fixed `_DOMAINS` order) into `TOOLS`, appends the Anthropic-hosted `web_search` server tool last, and merges the per-domain `HANDLERS` into one map behind `execute_tool(name, args)`.
- `anthropic_api.py` now imports only `from .tooldefs import TOOLS, execute_tool` and is back down to ~214 lines — just the agent loop. It never changes when a tool is added.
- Adding a scenario = add one `llm/tooldefs/<domain>.py` and list the module in `_DOMAINS`.

## Rationale

- **One scenario, one file.** The friction this solves is concrete: a new tool used to mean three edits in a monolith. Now its schema and handler are co-located in their own domain module, and nothing else moves.
- **The loop stops being a merge magnet.** `anthropic_api.py` was edited by every feature; now feature work touches `tooldefs/`, leaving the verified agent loop ([ADR 0003](0003-self-written-agent-loop.md)) stable.
- **Schema/dispatch drift is contained per domain.** ADR 0003 mitigated drift by "keeping schema and dispatch case in the same file, separated by ~30 lines." That property is now *stronger*: a domain's `SCHEMAS` and `HANDLERS` sit in the same small module, and the registry raises `RuntimeError` at import if two domains declare the same handler name — so a collision fails loud, not silently.
- **Cache-stable by construction.** Fixed `_DOMAINS` order + fixed per-module `SCHEMAS` order means `TOOLS` is byte-identical run to run, so the prompt prefix keeps cache-hitting.
- **Enables per-message routing.** Because tools are now addressable by domain (`TOOLS_BY_DOMAIN`, `build_tools(domains)`), the later routing feature can ship only the active domains' tools per message — impossible under the flat layout. (Routing itself is a separate change, gated behind `ROUTING_ENABLED`.)

## Consequences

**Easier**

- Adding / finding / removing a tool: it's all in one domain module.
- Reading the agent loop: `anthropic_api.py` is back to just the loop.
- Per-message tool routing is now possible (tools are addressable by domain).

**Harder / new concerns**

- One more indirection: a reader tracing a tool call goes loop → `execute_tool` → domain `HANDLERS`. The single dispatch hop is the cost of the decoupling.
- Two invariants must hold by construction, not convention: (1) `_DOMAINS` + per-module `SCHEMAS` order stays fixed (prompt-cache stability); (2) no two domains share a handler name (enforced — the registry raises at import).

**To revisit**

- If a single domain grows many tools, that module might itself want sub-splitting — but that's the same cohesion call one level down, not a new mechanism.

## Open questions

- None outstanding. The split is in production behind the unchanged agent loop.

## References

- `llm/tooldefs/__init__.py` — the registry: `_DOMAINS`, `TOOLS`, `execute_tool(name, args)`, `build_tools(domains)`, `TOOLS_BY_DOMAIN`, the duplicate-handler guard.
- `llm/tooldefs/<domain>.py` — per-domain `SCHEMAS` + `HANDLERS` (e.g. `todos.py`, `documents.py`).
- `llm/anthropic_api.py:39,191` — the loop's only contact points: `from .tooldefs import TOOLS, execute_tool`, and the `execute_tool(...)` dispatch call.
- Commit `d06a796` (2026-06-13) — "refactor: split tool schemas + dispatch into per-domain llm/tooldefs/".
- [ADR 0003](0003-self-written-agent-loop.md) — the self-written loop whose "growth signal" consequence this split answers; its References were updated to point here.
- [ADR 0006](0006-scheduler-state-schema.md) — `_item_payload` moved into `llm/tooldefs/todos.py` in this split (see its amendment note).
