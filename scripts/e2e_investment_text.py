"""End-to-end test: bank-text forward → LLM → 流水 row.

Runs three scenarios against the real Anthropic API + real INVESTMENTS_SHEET_ID
sheet, asserting the LLM picks the right tool sequence and writes the right
cells. Cleans up after itself.

Run:
    conda run -n assistant python scripts/e2e_investment_text.py

Cost: ~3 Opus calls ≈ $0.30 worth of API usage.
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from dotenv import load_dotenv

from llm.anthropic_api import AnthropicBackend
from prompts import render_personal_assistant
from tools import investments as inv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")

TEST_FUND_A = "TEST_全球科技基金"
TEST_FUND_B = "TEST_易方达蓝筹混合"


def _delete_rows(tab_name: str, row_indices: list[int]) -> None:
    if not row_indices:
        return
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    ss = client.open_by_key(os.environ["INVESTMENTS_SHEET_ID"])
    tab = ss.worksheet(tab_name)
    for r in sorted(row_indices, reverse=True):
        tab.delete_rows(r)


def main() -> None:
    backend = AnthropicBackend()
    system_prompt = render_personal_assistant("Sophie")

    plan_rows: list[int] = []
    ledger_rows: list[int] = []

    try:
        print("\n=== setup: add 2 test plans ===")
        p1 = inv.add_investment_plan(
            fund=TEST_FUND_A,
            frequency="monthly",
            day_of_month=4,
            planned_amount=1000,
            start_date="2026-05-01",
        )
        p2 = inv.add_investment_plan(
            fund=TEST_FUND_B,
            frequency="monthly",
            day_of_month=10,
            planned_amount=500,
            start_date="2026-05-01",
        )
        plan_rows = [p1["row"], p2["row"]]
        print(f"plans created: rows {plan_rows}")

        # ---------- Scenario A: consolidated text (debit + confirmation in one) ----------
        print("\n=== Scenario A: consolidated bank text ===")
        text_a = (
            "【富国基金】您2026-06-04通过建设银行成功申购"
            f"{TEST_FUND_A}1000.00元，2026-06-08确认份额166.28份。回QR退订"
        )
        print(f"sending: {text_a}")
        reply_a = backend.chat(user_message=text_a, system_prompt=system_prompt)
        print(f"reply: {reply_a}")

        rows_a = inv.find_investment(fund=TEST_FUND_A)
        assert len(rows_a) == 1, f"expected 1 ledger row, got {len(rows_a)}: {rows_a}"
        r = rows_a[0]
        ledger_rows.append(r["row"])
        assert r["debit_date"] == "2026-06-04", r
        assert r["confirm_date"] == "2026-06-08", r
        assert float(r["actual_amount"]) == 1000.0, r
        assert abs(float(r["shares"]) - 166.28) < 0.001, r
        assert r["status"] == "已确认", r
        print("[A passed] consolidated → '已确认' row with all 6 fields")

        # ---------- Scenario B: debit-only text ----------
        print("\n=== Scenario B: debit-only bank text ===")
        text_b = f"尾号1234 已扣 500.00 元购买{TEST_FUND_B} 2026-06-10"
        print(f"sending: {text_b}")
        reply_b = backend.chat(user_message=text_b, system_prompt=system_prompt)
        print(f"reply: {reply_b}")

        rows_b = inv.find_investment(fund=TEST_FUND_B)
        assert len(rows_b) == 1, f"expected 1 ledger row, got {len(rows_b)}: {rows_b}"
        r = rows_b[0]
        ledger_rows.append(r["row"])
        assert r["debit_date"] == "2026-06-10", r
        assert float(r["actual_amount"]) == 500.0, r
        assert r["confirm_date"] == "", r
        assert r["shares"] == "", r
        assert r["status"] == "已扣款", r
        print("[B passed] debit-only → '已扣款' row with empty confirm fields")

        # ---------- Scenario C: separate share confirmation arriving later ----------
        print("\n=== Scenario C: share-confirmation for the pending row ===")
        text_c = f"{TEST_FUND_B} 2026-06-10 申购 500 元已于 2026-06-13 确认份额 78.12 份"
        print(f"sending: {text_c}")
        reply_c = backend.chat(user_message=text_c, system_prompt=system_prompt)
        print(f"reply: {reply_c}")

        rows_after = inv.find_investment(fund=TEST_FUND_B)
        assert len(rows_after) == 1, f"expected 1 row, got {len(rows_after)}: {rows_after}"
        r = rows_after[0]
        assert r["confirm_date"] == "2026-06-13", r
        assert abs(float(r["shares"]) - 78.12) < 0.001, r
        assert r["status"] == "已确认", r
        print("[C passed] separate confirmation → row updated in place")

        print("\n[all 3 e2e scenarios passed]")
    finally:
        print(f"\ncleaning up: plan rows {plan_rows}, ledger rows {ledger_rows}")
        _delete_rows(inv.PLAN_TAB, plan_rows)
        _delete_rows(inv.LEDGER_TAB, ledger_rows)
        print("cleanup done")


if __name__ == "__main__":
    main()
