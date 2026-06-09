"""End-to-end smoke test for tools/investments.py.

Exercises all 7 functions against the real INVESTMENTS_SHEET_ID sheet, then
cleans up any rows it created. Safe to run repeatedly.

Run:
    conda run -n assistant python scripts/test_investments.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from dotenv import load_dotenv

from tools import investments as inv

load_dotenv()


def _delete_rows(tab_name: str, row_indices: list[int]) -> None:
    """Delete rows in descending order to keep indices stable."""
    if not row_indices:
        return
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    ss = client.open_by_key(os.environ["INVESTMENTS_SHEET_ID"])
    tab = ss.worksheet(tab_name)
    for r in sorted(row_indices, reverse=True):
        tab.delete_rows(r)


def main() -> None:
    plan_rows: list[int] = []
    ledger_rows: list[int] = []

    try:
        print("\n=== add_investment_plan (3 frequencies) ===")
        p1 = inv.add_investment_plan(
            fund="TEST_易方达蓝筹混合",
            frequency="monthly",
            day_of_month=10,
            planned_amount=500,
            start_date="2026-06-01",
            notes="smoke test monthly",
        )
        print(p1)
        plan_rows.append(p1["row"])
        assert p1["frequency"] == "monthly"

        p2 = inv.add_investment_plan(
            fund="TEST_全球科技基金",
            frequency="weekly",
            day_of_week=4,
            planned_amount=1000,
            start_date="2026-06-01",
        )
        print(p2)
        plan_rows.append(p2["row"])
        assert p2["frequency"] == "weekly"

        p3 = inv.add_investment_plan(
            fund="TEST_思远不定期",
            frequency="irregular",
            planned_amount=2500,
            start_date="2026-06-01",
        )
        print(p3)
        plan_rows.append(p3["row"])
        assert p3["frequency"] == "irregular"

        print("\n=== validation errors ===")
        try:
            inv.add_investment_plan(
                fund="bad", frequency="monthly", planned_amount=100, start_date="2026-06-01"
            )
            assert False, "should have raised — monthly without day_of_month"
        except ValueError as e:
            print(f"ok: monthly w/o day_of_month → {e}")
        try:
            inv.add_investment_plan(
                fund="bad", frequency="weekly", planned_amount=100, start_date="2026-06-01"
            )
            assert False, "should have raised — weekly without day_of_week"
        except ValueError as e:
            print(f"ok: weekly w/o day_of_week → {e}")
        try:
            inv.add_investment_plan(
                fund="bad", frequency="weekly", day_of_week=8, planned_amount=100, start_date="2026-06-01"
            )
            assert False, "should have raised — day_of_week=8 out of range"
        except ValueError as e:
            print(f"ok: day_of_week=8 → {e}")

        print("\n=== list_investment_plans (active) ===")
        actives = inv.list_investment_plans()
        test_plans = [p for p in actives if p["fund"].startswith("TEST_")]
        for p in test_plans:
            print(p)
        assert len(test_plans) == 3
        monthly = next(p for p in test_plans if p["frequency"] == "monthly")
        weekly = next(p for p in test_plans if p["frequency"] == "weekly")
        irregular = next(p for p in test_plans if p["frequency"] == "irregular")
        assert monthly["day_of_month"] == 10 and monthly["day_of_week"] is None
        assert weekly["day_of_week"] == 4 and weekly["day_of_month"] is None
        assert irregular["day_of_month"] is None and irregular["day_of_week"] is None

        print("\n=== update_plan_status (pause one) ===")
        r = inv.update_plan_status(fund="TEST_易方达蓝筹混合", status="paused")
        print(r)

        actives_after = inv.list_investment_plans()
        assert len([p for p in actives_after if p["fund"] == "TEST_易方达蓝筹混合"]) == 0
        all_plans = inv.list_investment_plans(status_filter=None)
        assert len([p for p in all_plans if p["fund"] == "TEST_易方达蓝筹混合"]) == 1
        print("ok: paused plan disappears from active list, appears in unfiltered list")

        print("\n=== record_investment (debit only, pending shares) ===")
        l1 = inv.record_investment(
            debit_date="2026-06-04",
            fund="TEST_全球科技基金",
            planned_amount=1000,
            actual_amount=1000.00,
        )
        print(l1)
        ledger_rows.append(l1["row"])
        assert l1["status"] == "已扣款"
        assert l1["operation"] == "inserted"

        print("\n=== record_investment (full, with confirmation) ===")
        l2 = inv.record_investment(
            debit_date="2026-05-05",
            fund="TEST_全球科技基金",
            planned_amount=1000,
            actual_amount=999.50,
            confirm_date="2026-05-08",
            shares=164.12,
        )
        print(l2)
        ledger_rows.append(l2["row"])
        assert l2["status"] == "已确认"
        assert l2["operation"] == "inserted"

        print("\n=== find_investment (pending_only) ===")
        pending = inv.find_investment(pending_only=True)
        pending_test = [r for r in pending if r["fund"].startswith("TEST_")]
        for p in pending_test:
            print(p)
        assert len(pending_test) == 1
        assert pending_test[0]["debit_date"] == "2026-06-04"

        print("\n=== find_investment (fund substring) ===")
        by_fund = inv.find_investment(fund="科技")
        by_fund_test = [r for r in by_fund if r["fund"].startswith("TEST_")]
        assert len(by_fund_test) == 2
        print(f"found {len(by_fund_test)} rows for 科技 substring")

        print("\n=== update_investment_confirmation ===")
        r = inv.update_investment_confirmation(
            row=pending_test[0]["row"],
            confirm_date="2026-06-08",
            shares=166.28,
        )
        print(r)

        pending_after = inv.find_investment(pending_only=True)
        pending_test_after = [r for r in pending_after if r["fund"].startswith("TEST_")]
        assert len(pending_test_after) == 0
        print("ok: confirmation cleared pending state")

        print("\n=== investment_summary (all TEST data) ===")
        s_all = inv.investment_summary(fund="TEST_全球科技基金")
        print(s_all)
        assert s_all["rows_count"] == 2
        assert s_all["total_debited_rmb"] == 1999.50
        assert s_all["pending_confirmations_count"] == 0
        assert abs(s_all["total_shares_confirmed"] - (164.12 + 166.28)) < 0.001

        print("\n=== investment_summary (year filter) ===")
        s_2026_05 = inv.investment_summary(fund="TEST_全球科技基金", year=2026)
        print(s_2026_05)
        assert s_2026_05["rows_count"] == 2

        print("\n=== upsert: same (debit_date, fund) lands on same row ===")
        l3 = inv.record_investment(
            debit_date="2026-04-04",
            fund="TEST_全球科技基金",
            planned_amount=1000,
            actual_amount=1000,
        )
        print(l3)
        ledger_rows.append(l3["row"])
        assert l3["operation"] == "inserted"
        assert l3["status"] == "已扣款"

        l3b = inv.record_investment(
            debit_date="2026-04-04",
            fund="TEST_全球科技基金",
            planned_amount=1000,
            actual_amount=1000,
            confirm_date="2026-04-07",
            shares=170.05,
        )
        print(l3b)
        assert l3b["row"] == l3["row"], "upsert should reuse same row"
        assert l3b["operation"] == "updated"
        assert l3b["status"] == "已确认"
        # Verify the row really has both pre-existing + new fields
        post = inv.find_investment(debit_date="2026-04-04", fund="TEST_全球科技基金")
        assert len(post) == 1
        assert post[0]["confirm_date"] == "2026-04-07"
        assert abs(float(post[0]["shares"]) - 170.05) < 0.001
        print("ok: upsert merged confirm fields into existing row")

        print("\n[all assertions passed]")
    finally:
        print(f"\ncleaning up: plan rows {plan_rows}, ledger rows {ledger_rows}")
        _delete_rows(inv.PLAN_TAB, plan_rows)
        _delete_rows(inv.LEDGER_TAB, ledger_rows)
        print("cleanup done")


if __name__ == "__main__":
    main()
