"""End-to-end: feed Sophie's real session description through the live backend
and confirm it (a) drafts a DLOG entry and (b) logs the brief metadata.

Hits the Anthropic API. Run:
    conda run -n assistant python scripts/e2e_cwi_log.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

import cwi_scheduler
from llm.anthropic_api import AnthropicBackend
from prompts import render_personal_assistant
from tools import cwi_log

load_dotenv()

MESSAGE = (
    "[Now: 2026-06-12 16:30]\n"
    "我今天在 Awesome Walls Dublin 带了两组 taster，每组 2 个人，顶绳攀岩一小时体验。"
    "我先了解他们的运动习惯，判断从什么难度开始，并把顶绳的工作原理和我如何确保安全讲清楚，"
    "攀爬过程中动态调整线路。今天还带了一组 bouldering induction，对象是爬过几次但经验不多的人，"
    "重点讲安全攀爬、中心主要设施、blind spots，还有一些简单攀爬建议。"
    "帮我各 draft 一个用于 CWI DLOG 的 description。"
)


def main() -> None:
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".md")[1])
    tmp.unlink(missing_ok=True)
    cwi_log.CWI_LOG_PATH = tmp

    try:
        backend = AnthropicBackend()
        reply = backend.chat(MESSAGE, system_prompt=render_personal_assistant("Sophie"))
        print("\n=== reply (draft) ===\n", reply)

        entries = cwi_log.list_entries()
        print("\n=== logged entries ===")
        for e in entries:
            print(e)

        instructed = [e for e in entries if e["kind"] == "instructed"]
        assert len(instructed) >= 2, f"expected >=2 instructed logged, got {len(instructed)}"
        assert all("awesome" in e["venue"].lower() for e in instructed), \
            "venue should be Awesome Walls Dublin"
        assert all(e["status"] == "pending" for e in instructed)
        assert reply.strip(), "expected a drafted reply"

        prog = cwi_log.progress()
        print("\n=== progress ===\n", prog)
        assert prog["instructed_sessions"]["done"] == len(instructed)

        # the evening reminder should now have something to nudge
        cwi_scheduler.USER_CHAT_ID = 999
        import asyncio
        from datetime import datetime
        sent: list = []
        asyncio.run(cwi_scheduler._scan_once(
            datetime(2026, 6, 12, 19, 0),
            lambda c, t: _collect(sent, c, t),
            force_hour=True,
        ))
        assert sent, "evening reminder should fire with pending entries"
        print("\n=== reminder ===\n", sent[0][1])

        print("\n[e2e passed]")
    finally:
        tmp.unlink(missing_ok=True)


async def _collect(sent, chat_id, text):
    sent.append((chat_id, text))


if __name__ == "__main__":
    main()
