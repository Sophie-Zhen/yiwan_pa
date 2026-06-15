"""Test inventory_scheduler (Phase 3 restock reminders).

Creates a threshold item below its minimum (low), a cycle item past its
interval (low), and a healthy item (not low). Then via _scan_once with a mock
send + force_hour:
  1. Verifies the batched message lists exactly the two low items.
  2. Verifies 上次提醒日期 was written.
  3. Verifies a second scan is suppressed (reminded once per episode).
  4. Simulates a re-buy on the threshold item (advance 上次购买日 past the
     reminder date while still low) and verifies it re-arms.
Cleans up its rows.

Optional --telegram sends one real reminder to TELEGRAM_USER_CHAT_ID.

Run:
    conda run -n assistant python scripts/test_inventory_scheduler.py
    conda run -n assistant python scripts/test_inventory_scheduler.py --telegram
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from dotenv import load_dotenv

import inventory_scheduler as sched
from tools import inventory as inv

load_dotenv()


def _delete(rows: list[int]) -> None:
    if not rows:
        return
    ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
        os.environ["EXPENSES_SHEET_ID"]
    )
    tab = ss.worksheet(inv.INVENTORY_TAB)
    for r in sorted(set(rows), reverse=True):
        tab.delete_rows(r)


async def test_scan() -> None:
    print("\n=== inventory_scheduler._scan_once ===")
    if sched.USER_CHAT_ID is None:
        print("[skip] TELEGRAM_USER_CHAT_ID not set; can't run scan path")
        return

    today = date.today()
    far_past = (today - timedelta(days=30)).isoformat()
    rows: list[int] = []

    # threshold item, qty at minimum → low
    t = inv.track_item(item="TEST_S_cement", unit="bag", strategy="threshold",
                       threshold=1, current_quantity=1)
    rows.append(t["row"])
    # cycle item, last bought 30d ago, interval 14 → low
    c = inv.track_item(item="TEST_S_coffee", unit="bag", strategy="cycle",
                       threshold=14, current_quantity=1, last_purchase_date=far_past)
    rows.append(c["row"])
    # healthy threshold item, qty above minimum → not low
    h = inv.track_item(item="TEST_S_rice", unit="bag", strategy="threshold",
                       threshold=1, current_quantity=5)
    rows.append(h["row"])

    sent: list[tuple[int, str]] = []

    async def mock_send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    try:
        now = datetime.combine(today, datetime.min.time()).replace(hour=sched.REMINDER_HOUR)

        fired = await sched._scan_once(now, mock_send, force_hour=True)
        print(f"first scan fired: {fired}")
        assert "TEST_S_cement" in fired
        assert "TEST_S_coffee" in fired
        assert "TEST_S_rice" not in fired, "healthy item must not be reminded"
        assert len(sent) == 1, "should be ONE batched message"
        msg = sent[0][1]
        print(f"message:\n{msg}")
        assert "TEST_S_cement" in msg and "TEST_S_coffee" in msg
        assert "TEST_S_rice" not in msg

        # second scan same day → suppressed (reminded once per episode)
        fired2 = await sched._scan_once(now, mock_send, force_hour=True)
        print(f"second scan fired: {fired2}")
        assert fired2 == []
        assert len(sent) == 1
        print("ok: idempotency suppressed the repeat")

        # re-arm: simulate buying the cement (advance 上次购买日 past today's
        # reminder date) while it is still low → should become due again.
        future_buy = (today + timedelta(days=1)).isoformat()
        ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
            os.environ["EXPENSES_SHEET_ID"]
        )
        tab = ss.worksheet(inv.INVENTORY_TAB)
        tab.update_cell(t["row"], inv.INV_COL_LAST_PURCHASE, future_buy)
        due_items = {d["item"] for d in inv.items_needing_restock_reminder()}
        assert "TEST_S_cement" in due_items, "re-buy after reminder should re-arm"
        print("ok: re-buy re-armed the cement reminder")

        print("\n[inventory scheduler test passed]")
    finally:
        print(f"cleaning up rows {rows}")
        _delete(rows)


async def test_telegram() -> None:
    print("\n=== real Telegram restock reminder ===")
    from telegram import Bot

    if sched.USER_CHAT_ID is None:
        print("[skip] TELEGRAM_USER_CHAT_ID not set")
        return

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    today = date.today()
    t = inv.track_item(item="TEST_S_发我（请忽略）", unit="袋", strategy="threshold",
                       threshold=1, current_quantity=0)

    async def real_send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id=chat_id, text=text)

    try:
        now = datetime.combine(today, datetime.min.time()).replace(hour=sched.REMINDER_HOUR)
        fired = await sched._scan_once(now, real_send, force_hour=True)
        print(f"fired: {fired} → check Telegram")
    finally:
        _delete([t["row"]])


async def main() -> None:
    await test_scan()
    if "--telegram" in sys.argv:
        await test_telegram()
    print("\n[done]")


if __name__ == "__main__":
    asyncio.run(main())
