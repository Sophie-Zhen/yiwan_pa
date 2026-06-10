"""End-to-end smoke test for tools/expenses.py (Phase 1).

Exercises record_purchase / find_purchase / price_history / top_items against
the real EXPENSES_SHEET_ID sheet, then deletes any rows it created. Safe to run
repeatedly.

Run:
    conda run -n assistant python scripts/test_expenses.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from dotenv import load_dotenv

from tools import expenses as exp

load_dotenv()


def _delete_rows(row_indices: list[int]) -> None:
    if not row_indices:
        return
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    ss = client.open_by_key(os.environ["EXPENSES_SHEET_ID"])
    tab = ss.worksheet(exp.LEDGER_TAB)
    for r in sorted(row_indices, reverse=True):
        tab.delete_rows(r)


def main() -> None:
    created_rows: list[int] = []

    try:
        print("\n=== record_purchase (multi-line receipt, mixed price/subtotal) ===")
        p1 = exp.record_purchase(
            date="2026-06-01",
            store="TEST_Lidl",
            items=[
                {"item": "TEST_Coffee Beans 1kg", "quantity": 1, "unit": "pack", "unit_price": 8.99, "category": "食品"},
                {"item": "TEST_Bananas", "quantity": 0.62, "unit": "kg", "subtotal": 0.67, "category": "食品"},
                {"item": "TEST_Dish Soap", "quantity": 2, "unit": "each", "unit_price": 1.49, "category": "日用"},
            ],
            notes="周末囤货",
        )
        print(p1)
        created_rows.extend(range(p1["rows"][0], p1["rows"][1] + 1))
        assert p1["count"] == 3

        print("\n=== record_purchase (second trip, coffee cheaper) ===")
        p2 = exp.record_purchase(
            date="2026-06-08",
            store="TEST_Aldi",
            items=[
                {"item": "TEST_Coffee Beans 1kg", "quantity": 1, "unit": "pack", "unit_price": 7.49, "category": "食品"},
            ],
        )
        print(p2)
        created_rows.extend(range(p2["rows"][0], p2["rows"][1] + 1))

        print("\n=== validation errors ===")
        try:
            exp.record_purchase(date="2026-06-01", store="x", items=[])
            assert False, "empty items should raise"
        except ValueError as e:
            print(f"ok: empty items → {e}")
        try:
            exp.record_purchase(
                date="2026-06-01", store="x",
                items=[{"item": "no price", "quantity": 1}],
            )
            assert False, "missing price should raise"
        except ValueError as e:
            print(f"ok: no unit_price/subtotal → {e}")

        print("\n=== find_purchase (item substring) ===")
        coffee = exp.find_purchase(item="coffee")
        coffee_test = [r for r in coffee if r["item"].startswith("TEST_")]
        for r in coffee_test:
            print(r)
        assert len(coffee_test) == 2

        print("\n=== find_purchase (store + date range) ===")
        lidl = exp.find_purchase(store="TEST_Lidl", since="2026-06-01", until="2026-06-01")
        assert len([r for r in lidl if r["item"].startswith("TEST_")]) == 3
        print(f"ok: 3 rows for TEST_Lidl on 2026-06-01")

        print("\n=== find_purchase: subtotal formula computed (Bananas) ===")
        banana = [r for r in exp.find_purchase(item="banana") if r["item"].startswith("TEST_")]
        assert len(banana) == 1
        # unit_price was a formula =G/D → 0.67/0.62 ≈ 1.08
        up = float(banana[0]["unit_price"])
        assert abs(up - (0.67 / 0.62)) < 0.01, f"computed unit_price off: {up}"
        print(f"ok: banana computed unit_price = {up:.4f}")

        print("\n=== price_history (coffee: 8.99 → 7.49) ===")
        hist = [r for r in exp.price_history(item="coffee") if r["item"].startswith("TEST_")]
        for r in hist:
            print(r)
        assert len(hist) == 2
        assert hist[0]["date"] == "2026-06-01" and hist[1]["date"] == "2026-06-08"
        assert float(hist[0]["unit_price"]) == 8.99
        assert float(hist[1]["unit_price"]) == 7.49
        print("ok: history sorted ascending, price drop visible")

        print("\n=== top_items (by spend) ===")
        top = exp.top_items(since="2026-06-01", until="2026-06-30", by="spend")
        top_test = [t for t in top if t["item"].startswith("TEST_")]
        for t in top_test:
            print(t)
        coffee_agg = next(t for t in top_test if "Coffee" in t["item"])
        assert coffee_agg["times"] == 2
        assert abs(coffee_agg["spend"] - (8.99 + 7.49)) < 0.001

        print("\n[all assertions passed]")
    finally:
        print(f"\ncleaning up rows {created_rows}")
        _delete_rows(created_rows)
        print("cleanup done")


if __name__ == "__main__":
    main()
