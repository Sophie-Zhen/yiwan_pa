"""Guard test for the prompt-cache breakpoint placement in llm.anthropic_api.

Pins the fix for the cache-write blowup: the old top-level auto `cache_control`
put the ONLY breakpoint after the volatile last message, so the stable
tools+system prefix was never cached on its own and every call cold-wrote it
(cache_read=0 across messages, seen in the Pi logs). The fix places two explicit
breakpoints — one on the system block (cross-message reuse), one on the last
message block (within-loop reuse) — and must keep them to exactly 2 so it stays
under Anthropic's 4-breakpoint limit as the tool loop appends turns.

Run: conda run -n assistant python scripts/test_cache_breakpoints.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from llm.anthropic_api import _cached_system, _mark_last_block


def _count_msg_breakpoints(messages):
    n = 0
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    n += 1
    return n


def main():
    passed = True

    def expect(desc, cond):
        nonlocal passed
        print(f"  {'ok ' if cond else 'FAIL'} {desc}")
        passed &= bool(cond)

    # --- system block carries the cross-message breakpoint ---
    sys_blocks = _cached_system("you are a helpful assistant")
    expect("system is a one-block list", isinstance(sys_blocks, list) and len(sys_blocks) == 1)
    expect("system text preserved", sys_blocks[0]["text"] == "you are a helpful assistant")
    expect("system block has ephemeral cache_control",
           sys_blocks[0].get("cache_control") == {"type": "ephemeral"})

    # --- string content gets promoted to a block and marked ---
    messages = [{"role": "user", "content": "记一下明天交房租"}]
    _mark_last_block(messages)
    c = messages[0]["content"]
    expect("string content promoted to block list", isinstance(c, list) and c[0]["type"] == "text")
    expect("text survives promotion", c[0]["text"] == "记一下明天交房租")
    expect("last block marked", c[-1].get("cache_control") == {"type": "ephemeral"})
    expect("exactly 1 message breakpoint", _count_msg_breakpoints(messages) == 1)

    # --- simulate a tool-use loop: append assistant + tool_result, re-mark ---
    # Assistant content is SDK objects in reality; a non-dict stands in for one.
    class _FakeBlock:  # not a dict -> must be skipped by _mark_last_block
        type = "text"

    messages.append({"role": "assistant", "content": [_FakeBlock()]})
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "ok-1"},
        {"type": "tool_result", "tool_use_id": "b", "content": "ok-2"},
    ]})
    _mark_last_block(messages)

    expect("breakpoint moved off the first user message",
           "cache_control" not in messages[0]["content"][-1])
    expect("breakpoint on last tool_result block",
           messages[-1]["content"][-1].get("cache_control") == {"type": "ephemeral"})
    expect("still exactly 1 message breakpoint after a loop turn",
           _count_msg_breakpoints(messages) == 1)
    expect("assistant SDK-object content left untouched (no crash, no mark)",
           not hasattr(messages[1]["content"][0], "cache_control"))

    # --- total request breakpoints = system (1) + message (1) = 2, under the 4 cap ---
    total = 1 + _count_msg_breakpoints(messages)
    expect("total breakpoints == 2 (<= 4 limit)", total == 2)

    print("PASS" if passed else "SOME TESTS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
