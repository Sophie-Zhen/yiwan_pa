"""Guard test for record_purchase's receipt-total checksum (offline).

The line extraction is the error-prone step: a dropped or miscounted line
silently loses money, yet the total the user eyeballs is whatever the model read
off the receipt — so a clean-looking total can hide a corrupt ledger. This test
pins the reconcile block that catches it: the sum is computed in Python from the
item inputs (subtotal, else qty × unit_price), independent of the printed total.

The sheet + inventory layers are stubbed, so no network or Google Sheets access.

Run: conda run -n assistant python scripts/test_receipt_checksum.py
"""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools import expenses


class _FakeTab:
    def update(self, **kwargs):  # record_purchase writes rows here
        pass


def _install_stubs():
    """Replace the sheet + inventory seams so record_purchase runs offline."""
    expenses.sheets.first_empty_row = lambda *a, **k: 2
    expenses._ledger_tab = lambda: _FakeTab()
    expenses.inventory.apply_purchase = lambda date, items: []


def main():
    _install_stubs()
    passed = True

    def expect(desc, cond):
        nonlocal passed
        print(f"  {'ok ' if cond else 'FAIL'} {desc}")
        passed &= bool(cond)

    # Four lemons at 0.49, but only THREE made it into items — the exact bug
    # from the live receipt. The printed total still says 4 × 0.49 = 1.96.
    three_of_four_lemons = [
        {"item": "Lemon", "quantity": 1, "subtotal": 0.49},
        {"item": "Lemon", "quantity": 1, "subtotal": 0.49},
        {"item": "Lemon", "quantity": 1, "subtotal": 0.49},
    ]
    r = expenses.record_purchase("2026-06-30", "Lidl", three_of_four_lemons, receipt_total=1.96)
    rec = r.get("reconcile")
    expect("mismatch surfaces a reconcile block", rec is not None)
    expect("matched is False on a dropped line", rec and rec["matched"] is False)
    expect("lines_total reflects only what was stored (1.47)", rec and rec["lines_total"] == 1.47)
    expect("difference flags the missing 0.49", rec and rec["difference"] == -0.49)

    # All four present → totals agree.
    four_lemons = three_of_four_lemons + [{"item": "Lemon", "quantity": 1, "subtotal": 0.49}]
    r = expenses.record_purchase("2026-06-30", "Lidl", four_lemons, receipt_total=1.96)
    expect("matched is True when lines sum to the receipt", r["reconcile"]["matched"] is True)

    # qty × unit_price path (no subtotal) is summed too.
    by_unit_price = [{"item": "Apple", "quantity": 3, "unit_price": 0.50}]
    r = expenses.record_purchase("2026-06-30", "Tesco", by_unit_price, receipt_total=1.50)
    expect("qty × unit_price reconciles (1.50)", r["reconcile"]["matched"] is True)

    # No receipt_total → no reconcile block (unchanged behaviour).
    r = expenses.record_purchase("2026-06-30", "Tesco", by_unit_price)
    expect("no receipt_total -> no reconcile block", "reconcile" not in r)

    print("PASS" if passed else "SOME TESTS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
