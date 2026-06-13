"""Tool registry — assembles the TOOLS schema list and the name→handler
dispatch map from the per-domain modules in this package.

Why this exists: every tool used to be declared in three places inside
anthropic_api.py (import, schema, dispatch branch), so each new scenario edited
that one 1400-line file three times. Now a scenario lives entirely in one
`llm/tooldefs/<domain>.py` (its SCHEMAS list + HANDLERS map) and is wired in by
adding the module to `_DOMAINS` below; anthropic_api.py never changes.

ORDER MATTERS: TOOLS must stay byte-stable across runs so the cached prompt
prefix (tools + system) keeps hitting. Keep `_DOMAINS` order fixed, and within
each module keep SCHEMAS order fixed.
"""
from . import contracts, cwi, documents, expenses, investments, parcels, todos

_DOMAINS = [todos, parcels, investments, expenses, contracts, documents, cwi]

# Anthropic-hosted server tool — executed on Anthropic's infrastructure, not
# dispatched locally (a long search returns stop_reason="pause_turn", which the
# chat loop resumes). Always appended last so it can't shift custom-tool order.
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}

TOOLS = [schema for domain in _DOMAINS for schema in domain.SCHEMAS] + [WEB_SEARCH_TOOL]

_HANDLERS = {}
for _domain in _DOMAINS:
    _overlap = _HANDLERS.keys() & _domain.HANDLERS.keys()
    if _overlap:
        raise RuntimeError(f"duplicate tool handler(s) across domains: {sorted(_overlap)}")
    _HANDLERS.update(_domain.HANDLERS)


def execute_tool(name, args):
    """Dispatch a tool_use block to its domain handler. Raises ValueError for an
    unknown name (the server-side web_search has no local handler and never
    reaches here)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"unknown tool: {name!r}")
    return handler(args)
