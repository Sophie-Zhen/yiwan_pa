"""Unit test for cwi_scheduler — reminder fires only at REMINDER_HOUR and only
when there are pending entries. Runs against a temp store.

Run:
    conda run -n assistant python scripts/test_cwi_scheduler.py
"""
import asyncio
import pathlib
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import cwi_scheduler
from tools import cwi_log


def main() -> None:
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".md")[1])
    tmp.unlink(missing_ok=True)
    cwi_log.CWI_LOG_PATH = tmp
    cwi_scheduler.USER_CHAT_ID = 999  # pretend the chat id is configured

    sent: list[tuple[int, str]] = []

    async def send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    try:
        print("\n=== no pending entries → no reminder, even at the hour ===")
        ids = asyncio.run(
            cwi_scheduler._scan_once(datetime(2026, 6, 12, 19, 0), send, force_hour=True)
        )
        assert ids == [] and not sent

        cwi_log.log_instructed_session(
            date="2026-06-12", venue="Awesome Walls Dublin",
            detail="top-rope taster", large_public_facility=True,
        )

        print("\n=== pending but wrong hour → no reminder ===")
        ids = asyncio.run(cwi_scheduler._scan_once(datetime(2026, 6, 12, 10, 0), send))
        assert ids == [] and not sent

        print("\n=== pending at the hour → one batched reminder ===")
        ids = asyncio.run(
            cwi_scheduler._scan_once(datetime(2026, 6, 12, 19, 0), send, force_hour=True)
        )
        assert ids == [1], ids
        assert len(sent) == 1
        chat_id, text = sent[0]
        assert chat_id == 999
        assert "DLOG" in text and "top-rope taster" in text
        print(text)

        print("\n=== after recording, reminder goes quiet ===")
        cwi_log.mark_recorded()
        sent.clear()
        ids = asyncio.run(
            cwi_scheduler._scan_once(datetime(2026, 6, 12, 19, 0), send, force_hour=True)
        )
        assert ids == [] and not sent

        print("\n[all assertions passed]")
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
