"""Unit test for tools/contracts.py.

Points CONTRACTS_PATH at a temp file so it never touches a real data/contracts.md.
Covers add / list / renew (price rotation + remind roll + re-arm) / update /
contracts_needing_reminder.

Run:
    conda run -n assistant python scripts/test_contracts.py
"""

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import contracts as ct


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "contracts.md"
    ct.CONTRACTS_PATH = tmp  # redirect storage
    today = date.today()

    print("\n=== add_contract ===")
    soon = (today + timedelta(days=5)).isoformat()
    later = (today + timedelta(days=200)).isoformat()
    a = ct.add_contract("Electric Ireland 电费", "energy", expiry=soon,
                        remind_on=today.isoformat(), current_price="0.42/kWh + €260 standing")
    print(a)
    b = ct.add_contract("AXA car insurance", "car_insurance", expiry=later,
                        current_price="€540/year")  # remind_on defaults to expiry
    print(b)
    assert b["remind_on"] == later, "remind_on should default to expiry"

    print("\n=== validation ===")
    try:
        ct.add_contract("dup energy", "energy", expiry=soon)
        # different name, fine; now a real dup:
        ct.add_contract("Electric Ireland 电费", "energy", expiry=soon)
        assert False
    except ValueError as e:
        print(f"ok: dup → {e}")
    try:
        ct.add_contract("bad", "spaceship", expiry=soon)
        assert False
    except ValueError as e:
        print(f"ok: bad type → {e}")
    try:
        ct.add_contract("bad2", "energy", expiry="07/02/2026")
        assert False
    except ValueError as e:
        print(f"ok: bad date → {e}")

    print("\n=== list_contracts (days_until_expiry) ===")
    actives = ct.list_contracts()
    for c in actives:
        print(f"  {c['name']}: expiry {c['expiry']}, in {c['days_until_expiry']}d")
    energy = next(c for c in actives if "电费" in c["name"])
    assert energy["days_until_expiry"] == 5

    print("\n=== contracts_needing_reminder ===")
    due = ct.contracts_needing_reminder(today)
    due_names = {c["name"] for c in due}
    print(f"  due today: {due_names}")
    assert "Electric Ireland 电费" in due_names, "remind_on=today should be due"
    assert "AXA car insurance" not in due_names, "future remind_on should not be due"

    # mark reminded → no longer due
    ct.mark_contract_reminded("Electric Ireland 电费", today.isoformat())
    due_after = {c["name"] for c in ct.contracts_needing_reminder(today)}
    assert "Electric Ireland 电费" not in due_after
    print("  ok: reminding suppresses the repeat")

    print("\n=== renew_contract (rotate price, roll dates, re-arm) ===")
    next_expiry = (today + timedelta(days=370)).isoformat()
    r = ct.renew_contract("电费", new_expiry=next_expiry, new_current_price="0.39/kWh + €240 standing")
    print(r)
    assert r["prev_price"] == "0.42/kWh + €260 standing"
    assert r["current_price"] == "0.39/kWh + €240 standing"
    # old gap was expiry(+5) - remind_on(today) = 5 days; new remind_on = next_expiry - 5
    expected_remind = (date.fromisoformat(next_expiry) - timedelta(days=5)).isoformat()
    assert r["new_remind_on"] == expected_remind, f"{r['new_remind_on']} != {expected_remind}"
    renewed = next(c for c in ct.list_contracts() if "电费" in c["name"])
    assert renewed["prev_price"] == "0.42/kWh + €260 standing"
    assert renewed["last_reminded"] is None, "renew must clear last_reminded (re-arm)"
    print("  ok: price rotated, dates rolled, reminder re-armed")

    print("\n=== update_contract (archive) ===")
    ct.update_contract("AXA", field="status", value="archived")
    active_names = {c["name"] for c in ct.list_contracts()}
    assert "AXA car insurance" not in active_names
    all_names = {c["name"] for c in ct.list_contracts(status_filter=None)}
    assert "AXA car insurance" in all_names
    print("  ok: archived contract drops from active, stays in full list")

    print("\n=== file round-trips (hand-editable markdown) ===")
    print(tmp.read_text())

    print("[all contract assertions passed]")


if __name__ == "__main__":
    main()
