"""Smoke test for the 库存 (inventory) layer of tools/expenses.py (Phase 2).

Covers track_item (both strategies + validation + upsert-preserves-quantity),
auto-restock via record_purchase, adjust_inventory (consumption + absolute),
and list_inventory low-stock detection for both strategies. Cleans up every
row it creates. Safe to re-run.

Run:
    conda run -n assistant python scripts/test_inventory.py
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from dotenv import load_dotenv

from tools import expenses as exp

load_dotenv()


def _delete(tab_name: str, rows: list[int]) -> None:
    if not rows:
        return
    ss = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"]).open_by_key(
        os.environ["EXPENSES_SHEET_ID"]
    )
    tab = ss.worksheet(tab_name)
    for r in sorted(set(rows), reverse=True):
        tab.delete_rows(r)


def _get(inv_list: list[dict], item: str) -> dict:
    return next(r for r in inv_list if r["item"] == item)


def main() -> None:
    inv_rows: list[int] = []
    ledger_rows: list[int] = []

    try:
        print("\n=== track_item (cycle + threshold) ===")
        c = exp.track_item(
            item="TEST_coffee", unit="bag", strategy="cycle",
            threshold=14, current_quantity=2, notes="周期约2周",
        )
        print(c)
        inv_rows.append(c["row"])
        assert c["operation"] == "inserted"

        t = exp.track_item(
            item="TEST_cement", unit="bag", strategy="threshold",
            threshold=1, current_quantity=3,
        )
        print(t)
        inv_rows.append(t["row"])

        print("\n=== validation: threshold strategy needs threshold ===")
        try:
            exp.track_item(item="TEST_bad", unit="x", strategy="threshold")
            assert False, "should raise"
        except ValueError as e:
            print(f"ok: {e}")
        try:
            exp.track_item(item="TEST_bad", unit="x", strategy="nonsense")
            assert False, "should raise"
        except ValueError as e:
            print(f"ok: {e}")

        print("\n=== track_item upsert preserves quantity ===")
        # Re-call to change threshold only; quantity must stay 2.
        c2 = exp.track_item(item="TEST_coffee", unit="bag", strategy="cycle", threshold=10)
        assert c2["operation"] == "updated"
        coffee = _get(exp.list_inventory(), "TEST_coffee")
        assert coffee["quantity"] == 2, f"quantity wiped on update: {coffee['quantity']}"
        assert float(coffee["threshold"]) == 10
        print(f"ok: threshold→10, quantity preserved at {coffee['quantity']}")

        print("\n=== auto-restock via record_purchase ===")
        # Two coffee lines in one trip → summed (+2); cement +1; an untracked
        # item must NOT create inventory.
        p = exp.record_purchase(
            date="2026-06-11",
            store="TEST_Aldi",
            items=[
                {"item": "TEST_coffee beans 1kg", "quantity": 1, "unit_price": 7.99},
                {"item": "TEST_coffee beans 1kg", "quantity": 1, "unit_price": 7.99},
                {"item": "TEST_cement 25kg", "quantity": 1, "unit_price": 6.50},
                {"item": "TEST_untracked chips", "quantity": 1, "unit_price": 2.00},
            ],
        )
        ledger_rows.extend(range(p["rows"][0], p["rows"][1] + 1))
        print("inventory_updates:", p["inventory_updates"])
        upd = {u["item"]: u for u in p["inventory_updates"]}
        assert upd["TEST_coffee"]["added"] == 2, upd
        assert upd["TEST_coffee"]["new_quantity"] == 4  # 2 + 2
        assert upd["TEST_cement"]["new_quantity"] == 4   # 3 + 1
        assert "TEST_untracked chips" not in upd
        # untracked item did not appear in inventory
        assert not any(r["item"] == "TEST_untracked chips" for r in exp.list_inventory())
        # last_purchase + last_price refreshed
        coffee = _get(exp.list_inventory(), "TEST_coffee")
        assert coffee["last_purchase_date"] == "2026-06-11"
        assert float(coffee["last_unit_price"]) == 7.99
        print(f"ok: coffee 2→4, cement 3→4, untracked stayed out, last_purchase refreshed")

        print("\n=== adjust_inventory (consumption delta + absolute) ===")
        a1 = exp.adjust_inventory(item="TEST_cement", delta=-2)
        print(a1)
        assert a1["new_quantity"] == 2  # 4 - 2
        a2 = exp.adjust_inventory(item="TEST_coffee", set_quantity=0.5)
        print(a2)
        assert a2["new_quantity"] == 0.5

        print("\n=== adjust_inventory errors ===")
        try:
            exp.adjust_inventory(item="TEST_nonexistent", delta=-1)
            assert False
        except ValueError as e:
            print(f"ok: not tracked → {e}")

        print("\n=== list_inventory low detection ===")
        # cement: threshold strategy, qty 2, min 1 → not low. Drop to 1 → low.
        exp.adjust_inventory(item="TEST_cement", set_quantity=1)
        cement = _get(exp.list_inventory(), "TEST_cement")
        assert cement["low"] is True, "cement at threshold should be low"
        # coffee: cycle, interval 10 days. Set last_purchase 20 days ago → low.
        old_date = (date.today() - timedelta(days=20)).isoformat()
        exp.track_item(item="TEST_coffee", unit="bag", strategy="cycle",
                       threshold=10, last_purchase_date=old_date)
        coffee = _get(exp.list_inventory(), "TEST_coffee")
        assert coffee["days_since_purchase"] == 20
        assert coffee["low"] is True, "coffee 20d > 10d interval should be low"

        low_list = exp.list_inventory(low_only=True)
        low_names = {r["item"] for r in low_list if r["item"].startswith("TEST_")}
        assert low_names == {"TEST_coffee", "TEST_cement"}, low_names
        print(f"ok: low_only returns {low_names}")

        print("\n[all inventory assertions passed]")
    finally:
        print(f"\ncleaning up inventory rows {inv_rows}, ledger rows {ledger_rows}")
        _delete(exp.INVENTORY_TAB, inv_rows)
        _delete(exp.LEDGER_TAB, ledger_rows)
        print("cleanup done")


if __name__ == "__main__":
    main()
