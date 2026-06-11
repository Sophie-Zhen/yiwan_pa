"""End-to-end (real LLM) test of the 库存 conversational flow (Phase 2).

Runs a multi-turn text conversation through the real system prompt + Anthropic
API + tool loop, then asserts the resulting 库存 sheet state. The key thing it
guards: buying a tracked item auto-restocks at the data layer, and the model
must NOT also call adjust_inventory (which would double-count). It checks that
by asserting the final quantity equals start + bought, not start + 2×bought.

Also covers: track_item from natural language, consumption mapping
("用了 N 袋" → delta), and the low-stock query.

Cleans up rows it creates. Run:
    conda run -n assistant python scripts/e2e_inventory_text.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from dotenv import load_dotenv

from llm import get_backend
from prompts import render_personal_assistant
from tools import expenses as exp

load_dotenv()


def _now(msg: str) -> str:
    return f"[Now: {datetime.now().strftime('%Y-%m-%d %H:%M')}]\n{msg}"


def _get(item: str) -> dict | None:
    return next((r for r in exp.list_inventory(status_filter=None) if r["item"] == item), None)


def main() -> None:
    backend = get_backend()
    system = render_personal_assistant(os.getenv("USER_NAME", "Sophie"))
    history: list[dict] = []

    def turn(msg: str) -> str:
        nonlocal history
        reply = backend.chat(_now(msg), system, history, None)
        history = history + [
            {"role": "user", "content": msg},
            {"role": "assistant", "content": reply},
        ]
        print(f"\n>>> {msg}\n{reply}")
        return reply

    created_inv: list[int] = []
    created_ledger_before = len(exp.find_purchase())

    try:
        turn("开始跟踪库存：水泥 TEST_e2e_cement，现在有 3 袋，少于 1 袋就提醒我该买了")
        cement = _get("TEST_e2e_cement")
        assert cement is not None, "track_item was not called / wrong name"
        assert cement["strategy"] == "threshold", cement
        assert cement["quantity"] == 3, cement
        created_inv.append(cement["row"])
        print("  [check] cement tracked: threshold, qty 3")

        turn("今天在 TEST_e2e_店 买了 TEST_e2e_cement 水泥 2 袋，每袋 6.5 欧")
        cement = _get("TEST_e2e_cement")
        # start 3 + bought 2 = 5. If the model ALSO called adjust_inventory it
        # would be 7 — that's the double-count we're guarding against.
        assert cement["quantity"] == 5, f"expected 5 (3+2), got {cement['quantity']} — double count?"
        print("  [check] auto-restock 3→5, no double count")

        turn("TEST_e2e_cement 水泥用了 2 袋")
        cement = _get("TEST_e2e_cement")
        assert cement["quantity"] == 3, f"expected 3 (5-2), got {cement['quantity']}"
        print("  [check] consumption 5→3")

        turn("TEST_e2e_cement 又用了 2 袋")
        cement = _get("TEST_e2e_cement")
        assert cement["quantity"] == 1, f"expected 1, got {cement['quantity']}"
        assert cement["low"] is True, "at threshold 1, should be low"
        print("  [check] consumption 3→1, now low")

        reply = turn("现在有什么该买了吗")
        assert "cement" in reply.lower() or "水泥" in reply, "low-stock query should surface cement"
        print("  [check] low-stock query surfaced cement")

        print("\n[inventory e2e passed]")
    finally:
        # collect any ledger rows the purchase created
        ledger_now = exp.find_purchase()
        new_ledger = [r["row"] for r in ledger_now if r["row"] > created_ledger_before + 1
                      and "e2e" in r["store"].lower()]
        ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
            os.environ["EXPENSES_SHEET_ID"]
        )
        print(f"\ncleaning up inventory rows {created_inv}, ledger rows {new_ledger}")
        inv_tab = ss.worksheet(exp.INVENTORY_TAB)
        for r in sorted(set(created_inv), reverse=True):
            inv_tab.delete_rows(r)
        led_tab = ss.worksheet(exp.LEDGER_TAB)
        for r in sorted(set(new_ledger), reverse=True):
            led_tab.delete_rows(r)
        print("cleanup done")


if __name__ == "__main__":
    main()
