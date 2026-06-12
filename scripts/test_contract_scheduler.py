"""Test contract_scheduler.

Redirects CONTRACTS_PATH to a temp file. Creates a contract due today and one
due later, then via _scan_once with a mock send + force_hour verifies: the due
one fires in one batched message (with price shown), the later one doesn't, a
second scan is suppressed, and a renewal re-arms the reminder.

Optional --telegram sends one real reminder to TELEGRAM_USER_CHAT_ID.

Run:
    conda run -n assistant python scripts/test_contract_scheduler.py
    conda run -n assistant python scripts/test_contract_scheduler.py --telegram
"""

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

import contract_scheduler as sched
from tools import contracts as ct

load_dotenv()


async def test_scan() -> None:
    print("\n=== contract_scheduler._scan_once ===")
    if sched.USER_CHAT_ID is None:
        print("[skip] TELEGRAM_USER_CHAT_ID not set; can't run scan path")
        return

    ct.CONTRACTS_PATH = Path(tempfile.mkdtemp()) / "contracts.md"
    today = date.today()

    ct.add_contract("TEST_energy 电费", "energy",
                    expiry=(today + timedelta(days=3)).isoformat(),
                    remind_on=today.isoformat(),
                    current_price="0.42/kWh")
    ct.add_contract("TEST_broadband", "broadband",
                    expiry=(today + timedelta(days=300)).isoformat(),
                    current_price="€45/month")  # remind_on far in the future

    sent: list[tuple[int, str]] = []

    async def mock_send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    now = datetime.combine(today, datetime.min.time()).replace(hour=sched.REMINDER_HOUR)

    fired = await sched._scan_once(now, mock_send, force_hour=True)
    print(f"first scan fired: {fired}")
    assert fired == ["TEST_energy 电费"], fired
    assert len(sent) == 1, "should be one batched message"
    msg = sent[0][1]
    print(f"message:\n{msg}")
    assert "TEST_energy 电费" in msg and "0.42/kWh" in msg
    assert "TEST_broadband" not in msg

    fired2 = await sched._scan_once(now, mock_send, force_hour=True)
    print(f"second scan fired: {fired2}")
    assert fired2 == []
    assert len(sent) == 1
    print("ok: idempotency suppressed the repeat")

    # renew with remind_on=today → should re-arm and fire again
    ct.renew_contract("TEST_energy", new_expiry=(today + timedelta(days=368)).isoformat(),
                      new_current_price="0.39/kWh", new_remind_on=today.isoformat())
    fired3 = await sched._scan_once(now, mock_send, force_hour=True)
    print(f"post-renew scan fired: {fired3}")
    assert fired3 == ["TEST_energy 电费"], fired3
    assert "去年 0.42/kWh" in sent[-1][1], "renewed reminder should show last year's price"
    print("ok: renewal re-armed the reminder and shows year-over-year price")

    print("\n[contract scheduler test passed]")


async def test_telegram() -> None:
    print("\n=== real Telegram contract reminder ===")
    from telegram import Bot

    if sched.USER_CHAT_ID is None:
        print("[skip] TELEGRAM_USER_CHAT_ID not set")
        return

    ct.CONTRACTS_PATH = Path(tempfile.mkdtemp()) / "contracts.md"
    today = date.today()
    ct.add_contract("TEST_合同（请忽略）", "energy",
                    expiry=(today + timedelta(days=2)).isoformat(),
                    remind_on=today.isoformat(), current_price="€100/year")

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])

    async def real_send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id=chat_id, text=text)

    now = datetime.combine(today, datetime.min.time()).replace(hour=sched.REMINDER_HOUR)
    fired = await sched._scan_once(now, real_send, force_hour=True)
    print(f"fired: {fired} → check Telegram")


async def main() -> None:
    await test_scan()
    if "--telegram" in sys.argv:
        await test_telegram()
    print("\n[done]")


if __name__ == "__main__":
    asyncio.run(main())
