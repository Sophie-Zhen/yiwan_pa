"""Byte-identity + cross-registry guard for the system-prompt modularization.

The dark-ship / zero-regression guarantee is: with all domains active, the
modular render and tool list are BYTE-IDENTICAL to the pre-routing monolith.
This test pins that, plus the loss-free split and the agreement between the
domain registries that must not drift (prompts._BLOCKS, prompts.ALL_DOMAINS,
router._TRIGGERS, tooldefs.CANONICAL_ORDER).

Run: conda run -n assistant python scripts/test_prompt_blocks.py
"""
import sys
import pathlib
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import router
from prompts import (
    ALL_DOMAINS,
    PERSONAL_ASSISTANT_TEMPLATE,
    _BLOCKS,
    render_personal_assistant,
)
from llm.tooldefs import CANONICAL_ORDER, TOOLS, build_tools


def main():
    passed = True

    def expect(desc, cond):
        nonlocal passed
        print(f"  {'ok ' if cond else 'FAIL'} {desc}")
        passed &= cond

    # 1. The split is loss-free: concatenating every block reproduces the source.
    expect("blocks reassemble template", "".join(t for _, t in _BLOCKS) == PERSONAL_ASSISTANT_TEMPLATE)

    # 2. Full render == the pre-routing monolith (the dark-ship/fallback path).
    today = datetime.now().strftime("%Y-%m-%d")
    full = PERSONAL_ASSISTANT_TEMPLATE.format(user_name="Sophie", today=today)
    expect("render(None) == full", render_personal_assistant("Sophie") == full)
    expect("render(ALL)  == full", render_personal_assistant("Sophie", set(ALL_DOMAINS)) == full)

    # 3. Full tool list == the pre-routing TOOLS.
    expect("build_tools(None) == TOOLS", build_tools() == TOOLS)
    expect("build_tools(ALL)  == TOOLS", build_tools(set(ALL_DOMAINS)) == TOOLS)

    # 4. Cross-registry agreement — drift here would silently misroute.
    block_domains = {k for k, _ in _BLOCKS if k != "core"}
    expect("router triggers ⊆ ALL_DOMAINS", set(router._TRIGGERS) <= ALL_DOMAINS)
    expect("tool domains ⊆ ALL_DOMAINS", set(CANONICAL_ORDER) <= ALL_DOMAINS)
    expect("block domains ⊆ ALL_DOMAINS", block_domains <= ALL_DOMAINS)

    # 5. Every prompt-section domain is reachable by the router: it has a trigger,
    # or is set by an input-type rule, or is web_search (always added on a hit).
    reachable = set(router._TRIGGERS) | {"documents", "parcels", "expenses", "web_search"}
    unreachable = block_domains - reachable
    expect(f"all prompt sections reachable (orphans: {unreachable or 'none'})", not unreachable)

    print("PASS" if passed else "SOME CHECKS FAILED")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
