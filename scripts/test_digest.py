"""Hermetic unit test for the scheduled digest jobs.

No network, no real LLM, no Telegram. A fake context records send_message
calls; _ask_llm / get_stale_items / mark_stale_alerted are monkeypatched. Pins
the load-bearing, otherwise-untested invariants the jobs carry:
  - morning digest marks stale items ONLY when both the LLM call AND the send
    succeed (mark-on-send-success); a send failure leaves them unmarked so they
    re-surface tomorrow;
  - evening digest suppresses the push when the LLM returns a blank reply;
  - both skip entirely when USER_CHAT_ID is unset.

The module under test is selectable so the same assertions can run against
bot.py (baseline, before the split) and digest_scheduler.py (after):
    DIGEST_MODULE=bot conda run -n assistant python scripts/test_digest.py
    conda run -n assistant python scripts/test_digest.py        # digest_scheduler
"""

import asyncio
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

dg = importlib.import_module(os.getenv("DIGEST_MODULE", "digest_scheduler"))


class _RecordingBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class _FailingBot:
    async def send_message(self, chat_id, text):
        raise RuntimeError("telegram down")


def _ctx(bot):
    return SimpleNamespace(bot=bot)


def _stub_llm(reply: str):
    async def _llm(*args, **kwargs):
        return reply
    return _llm


def main() -> None:
    dg.USER_CHAT_ID = 999  # the gate; mutated like the other scheduler tests

    print("\n=== morning digest: sends, marks stale on success ===")
    marked: list[str] = []
    dg.get_stale_items = lambda days, now: [
        SimpleNamespace(title="买菜"), SimpleNamespace(title="交电费"),
    ]
    dg.mark_stale_alerted = lambda title, now_str: marked.append(title)
    dg._ask_llm = _stub_llm("早安，今天有 2 件 stale")
    bot = _RecordingBot()
    asyncio.run(dg.send_daily_digest(_ctx(bot)))
    assert bot.sent == [(999, "早安，今天有 2 件 stale")], bot.sent
    assert set(marked) == {"买菜", "交电费"}, marked
    print("  ok: digest pushed, both stale items marked")

    print("\n=== morning digest: send fails → stale NOT marked ===")
    marked.clear()
    dg._ask_llm = _stub_llm("hi")
    asyncio.run(dg.send_daily_digest(_ctx(_FailingBot())))
    assert marked == [], f"send failed but items were marked: {marked}"
    print("  ok: send failure left stale items unmarked (re-surface tomorrow)")

    print("\n=== morning digest: no stale → still sends, nothing to mark ===")
    marked.clear()
    dg.get_stale_items = lambda days, now: []
    dg._ask_llm = _stub_llm("早安，今天没有 stale")
    bot = _RecordingBot()
    asyncio.run(dg.send_daily_digest(_ctx(bot)))
    assert bot.sent == [(999, "早安，今天没有 stale")], bot.sent
    assert marked == []
    print("  ok")

    print("\n=== evening digest: blank reply suppressed ===")
    dg._ask_llm = _stub_llm("   ")
    bot = _RecordingBot()
    asyncio.run(dg.send_evening_digest(_ctx(bot)))
    assert bot.sent == [], f"blank reply should suppress, got {bot.sent}"
    print("  ok: silence when nothing to nag about")

    print("\n=== evening digest: non-empty reply sent ===")
    dg._ask_llm = _stub_llm("晚上好，还有 2 件事")
    bot = _RecordingBot()
    asyncio.run(dg.send_evening_digest(_ctx(bot)))
    assert bot.sent == [(999, "晚上好，还有 2 件事")], bot.sent
    print("  ok")

    print("\n=== chat-id gate: USER_CHAT_ID None → both skip ===")
    dg.USER_CHAT_ID = None
    bot = _RecordingBot()
    asyncio.run(dg.send_daily_digest(_ctx(bot)))
    asyncio.run(dg.send_evening_digest(_ctx(bot)))
    assert bot.sent == [], bot.sent
    print("  ok")

    print("\n[all digest assertions passed]")


if __name__ == "__main__":
    main()
