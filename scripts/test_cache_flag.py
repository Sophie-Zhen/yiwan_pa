"""Guard test for the cache on/off toggle on AnthropicBackend.chat.

cache=True marks two prompt-cache breakpoints (system block + last message
block); cache=False marks none — used for isolated cron calls (the daily
digest), whose cache write is never read before the 5-min TTL expires, so
caching only pays the 1.25x write premium over 1.0x uncached.

Uses a capturing fake client (no network). Run:
conda run -n assistant python scripts/test_cache_flag.py
"""
import os
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-never-used")

from llm.anthropic_api import AnthropicBackend


class _FakeMessages:
    def __init__(self):
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        usage = types.SimpleNamespace(
            input_tokens=0, output_tokens=0,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )
        text_block = types.SimpleNamespace(type="text", text="ok")
        return types.SimpleNamespace(
            stop_reason="end_turn", content=[text_block], usage=usage,
        )


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _msg_breakpoints(kwargs):
    n = 0
    for msg in kwargs["messages"]:
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

    backend = AnthropicBackend()
    backend.client = _FakeClient()

    # --- cache=True: system block carries a breakpoint; last message marked ---
    backend.chat("记一下明天交房租", "SYSTEM-PROMPT", cache=True)
    k = backend.client.messages.last_kwargs
    expect("cache=True: system is a cached block list",
           isinstance(k["system"], list) and k["system"][0].get("cache_control") == {"type": "ephemeral"})
    expect("cache=True: exactly 1 message breakpoint", _msg_breakpoints(k) == 1)

    # --- cache=False: plain string system, no breakpoints anywhere ---
    backend.chat("早安 digest", "SYSTEM-PROMPT", cache=False)
    k = backend.client.messages.last_kwargs
    expect("cache=False: system is a plain string", isinstance(k["system"], str))
    expect("cache=False: zero message breakpoints", _msg_breakpoints(k) == 0)
    expect("cache=False: no top-level cache_control", "cache_control" not in k)

    print("PASS" if passed else "SOME TESTS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
