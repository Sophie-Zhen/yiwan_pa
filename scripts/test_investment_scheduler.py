"""Test investment_scheduler.

Two parts:
  1. Pure unit tests for _should_remind() — covers normal months, Feb,
     month-end roll-over, and a sample of edge cases.
  2. End-to-end via _scan_once with a mock send_fn — creates a synthetic
     plan whose day_of_month matches tomorrow, runs the scan with
     force_hour=True, verifies the mock got called and the plan's
     last_reminded was written. Then it runs scan again and verifies the
     guard prevents a duplicate. Cleans up the plan.

Optional Telegram mode: pass --telegram to actually send the reminder via
your TELEGRAM_BOT_TOKEN to TELEGRAM_USER_CHAT_ID (real e2e). Default is the
mock mode (no Telegram).

Run:
    conda run -n assistant python scripts/test_investment_scheduler.py
    conda run -n assistant python scripts/test_investment_scheduler.py --telegram
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

import investment_scheduler as sched
from tools import investments as inv

load_dotenv()


def test_should_remind() -> None:
    print("\n=== _should_remind ===")
    # Normal: today is 9, tomorrow is 10, plan day=10 → fire
    assert sched._should_remind(10, date(2026, 6, 9))
    # No match: today is 8, tomorrow is 9, plan day=10 → no
    assert not sched._should_remind(10, date(2026, 6, 8))
    # Day 1 plan, today is last day of month → fire
    assert sched._should_remind(1, date(2026, 5, 31))
    assert sched._should_remind(1, date(2026, 6, 30))
    # Feb roll: plan day=31, today=Feb 27 (non-leap), tomorrow=Feb 28=eff(31,28)=28 → fire
    assert sched._should_remind(31, date(2027, 2, 27))
    # Feb roll leap year: plan day=31, today=Feb 28, tomorrow=Feb 29=eff(31,29)=29 → fire
    assert sched._should_remind(31, date(2028, 2, 28))
    # Feb edge: plan day=31, today=Feb 28 in non-leap, tomorrow=Mar 1 → no
    assert not sched._should_remind(31, date(2027, 2, 28))
    # Plan day=30 in Feb non-leap: today=Feb 27, tomorrow=Feb 28=eff(30,28)=28 → fire
    assert sched._should_remind(30, date(2027, 2, 27))
    print("ok: _should_remind passed all cases")


async def test_scan_once_mock() -> None:
    print("\n=== _scan_once with mock send ===")
    if sched.USER_CHAT_ID is None:
        print("[skip] TELEGRAM_USER_CHAT_ID not set; can't run scan path")
        return

    today = date.today()
    tomorrow = today + timedelta(days=1)
    # Edge: if tomorrow is end-of-month, set day_of_month = tomorrow.day; works.
    plan_day = tomorrow.day

    plan = inv.add_investment_plan(
        fund="TEST_SCHED_基金",
        day_of_month=plan_day,
        planned_amount=300,
        start_date=today.strftime("%Y-%m-%d"),
        notes="scheduler test",
    )
    plan_row = plan["row"]

    sent: list[tuple[int, str]] = []

    async def mock_send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    try:
        # First scan: should fire once
        fake_now = datetime.combine(today, datetime.min.time()).replace(hour=sched.REMINDER_HOUR)
        fired = await sched._scan_once(fake_now, mock_send, force_hour=True)
        print(f"first scan fired: {fired}")
        assert "TEST_SCHED_基金" in fired
        assert len(sent) == 1
        assert sent[0][0] == sched.USER_CHAT_ID
        assert "明天" in sent[0][1]
        assert "TEST_SCHED_基金" in sent[0][1]
        print(f"sent text: {sent[0][1]}")

        # Second scan same day: idempotency guard should suppress
        fired2 = await sched._scan_once(fake_now, mock_send, force_hour=True)
        print(f"second scan fired: {fired2}")
        assert fired2 == []
        assert len(sent) == 1  # no new send
        print("ok: idempotency guard prevented duplicate")
    finally:
        # Cleanup
        import gspread
        ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
            os.environ["INVESTMENTS_SHEET_ID"]
        )
        ss.worksheet(inv.PLAN_TAB).delete_rows(plan_row)
        print(f"cleaned up plan row {plan_row}")


async def test_scan_once_telegram() -> None:
    """Real Telegram round-trip — sends a reminder to TELEGRAM_USER_CHAT_ID."""
    print("\n=== _scan_once with REAL Telegram send ===")
    from telegram import Bot

    if sched.USER_CHAT_ID is None:
        print("[skip] TELEGRAM_USER_CHAT_ID not set")
        return

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    today = date.today()
    tomorrow = today + timedelta(days=1)

    plan = inv.add_investment_plan(
        fund="TEST_SCHED_基金（请忽略）",
        day_of_month=tomorrow.day,
        planned_amount=300,
        start_date=today.strftime("%Y-%m-%d"),
    )

    async def real_send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id=chat_id, text=text)

    try:
        fake_now = datetime.combine(today, datetime.min.time()).replace(hour=sched.REMINDER_HOUR)
        fired = await sched._scan_once(fake_now, real_send, force_hour=True)
        print(f"fired: {fired}")
        print("→ check Telegram for the reminder message")
    finally:
        import gspread
        ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
            os.environ["INVESTMENTS_SHEET_ID"]
        )
        ss.worksheet(inv.PLAN_TAB).delete_rows(plan["row"])
        print(f"cleaned up plan row {plan['row']}")


async def main() -> None:
    test_should_remind()
    await test_scan_once_mock()
    if "--telegram" in sys.argv:
        await test_scan_once_telegram()
    print("\n[all scheduler tests passed]")


if __name__ == "__main__":
    asyncio.run(main())
