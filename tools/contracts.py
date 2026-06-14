"""Annual contract tracking + renewal reminders, stored in data/contracts.md.

Contracts (energy / broadband / insurance ...) are entities, not a ledger:
a handful of discrete records, each queried and updated on its own, with a
lifecycle (active → renewed → active...). So they live in a hand-editable
markdown file rather than a Google Sheet — the same entity→markdown choice as
the todo inbox. Prices are kept as free-form strings on purpose: an energy
contract is "0.42/kWh + €260 standing", an insurance one is "€540/year" —
heterogeneous text a number column couldn't hold.

File format (one entity per `## ` heading, `- key: value` lines), e.g.:

    ## Electric Ireland 电费
    - type: energy
    - expiry: 2026-07-02
    - remind_on: 2026-06-25
    - current_price: 0.42/kWh + €260 standing
    - prev_price:
    - status: active
    - last_reminded:
    - notes: switched from Bord Gáis last year

Reminder model: a daily scheduler (contract_scheduler.py) pings on `remind_on`.
`renew_contract` rolls expiry + remind_on forward and rotates current→prev
price, which re-arms next year's reminder.
"""
import pathlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
CONTRACTS_PATH = DATA_DIR / "contracts.md"

CONTRACTS_HEADER = (
    "# Contracts\n\nAnnual contracts to renew. Reminders fire on remind_on. "
    "Hand-editable.\n\n---\n"
)

VALID_TYPES = {"energy", "broadband", "home_insurance", "car_insurance", "other"}
VALID_STATUSES = {"active", "archived"}
UPDATABLE_FIELDS = {
    "type", "expiry", "remind_on", "current_price", "prev_price", "status", "notes",
}

_FIELD_LINE = re.compile(r"^- (\w+):\s*(.*)$")


@dataclass
class Contract:
    name: str
    type: str = "other"
    expiry: Optional[str] = None       # YYYY-MM-DD
    remind_on: Optional[str] = None    # YYYY-MM-DD
    current_price: Optional[str] = None
    prev_price: Optional[str] = None
    status: str = "active"
    last_reminded: Optional[str] = None  # YYYY-MM-DD; scheduler state
    notes: Optional[str] = None

    def to_markdown(self) -> str:
        lines = [f"## {self.name}", f"- type: {self.type}"]
        # Always emit the core fields (even when empty) so the file is a clear,
        # hand-editable template; optional notes only when present.
        lines.append(f"- expiry: {self.expiry or ''}")
        lines.append(f"- remind_on: {self.remind_on or ''}")
        lines.append(f"- current_price: {self.current_price or ''}")
        lines.append(f"- prev_price: {self.prev_price or ''}")
        lines.append(f"- status: {self.status}")
        lines.append(f"- last_reminded: {self.last_reminded or ''}")
        if self.notes:
            lines.append(f"- notes: {self.notes}")
        return "\n".join(lines)


def _ensure_file() -> None:
    if not CONTRACTS_PATH.exists():
        CONTRACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONTRACTS_PATH.write_text(CONTRACTS_HEADER + "\n", encoding="utf-8")


def _parse(content: str) -> list[Contract]:
    contracts: list[Contract] = []
    sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]
    for sec in sections:
        lines = sec.strip().split("\n")
        if not lines:
            continue
        name = lines[0].strip()
        fields: dict[str, str] = {}
        for line in lines[1:]:
            m = _FIELD_LINE.match(line.strip())
            if m:
                fields[m.group(1)] = m.group(2).strip()
        contracts.append(
            Contract(
                name=name,
                type=fields.get("type", "other") or "other",
                expiry=fields.get("expiry") or None,
                remind_on=fields.get("remind_on") or None,
                current_price=fields.get("current_price") or None,
                prev_price=fields.get("prev_price") or None,
                status=fields.get("status", "active") or "active",
                last_reminded=fields.get("last_reminded") or None,
                notes=fields.get("notes") or None,
            )
        )
    return contracts


def _read() -> list[Contract]:
    _ensure_file()
    return _parse(CONTRACTS_PATH.read_text(encoding="utf-8"))


def _write(contracts: list[Contract]) -> None:
    body = "\n\n".join(c.to_markdown() for c in contracts)
    sep = "\n\n" if body else ""
    CONTRACTS_PATH.write_text(CONTRACTS_HEADER + sep + body + "\n", encoding="utf-8")


def _validate_date(label: str, value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}") from exc


def _find(contracts: list[Contract], name: str) -> Optional[Contract]:
    needle = name.lower()
    for c in contracts:
        if needle in c.name.lower():
            return c
    return None


def add_contract(
    name: str,
    contract_type: str,
    expiry: str,
    remind_on: Optional[str] = None,
    current_price: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Add a contract. remind_on defaults to the expiry date (remind on the day
    it lapses); pass an earlier date to get lead time to shop around. Raises if
    a contract with the same name already exists — use renew_contract or
    update_contract instead.
    """
    if contract_type not in VALID_TYPES:
        raise ValueError(f"type must be one of {sorted(VALID_TYPES)}, got {contract_type!r}")
    _validate_date("expiry", expiry)
    if remind_on is None:
        remind_on = expiry
    else:
        _validate_date("remind_on", remind_on)

    contracts = _read()
    if _find(contracts, name) is not None:
        raise ValueError(f"a contract matching {name!r} already exists")

    contract = Contract(
        name=name,
        type=contract_type,
        expiry=expiry,
        remind_on=remind_on,
        current_price=current_price,
        status="active",
        notes=notes,
    )
    contracts.insert(0, contract)
    _write(contracts)
    return {"name": name, "type": contract_type, "expiry": expiry, "remind_on": remind_on}


def list_contracts(status_filter: Optional[str] = "active") -> list[dict]:
    """List contracts (default active). Each entry includes days_until_expiry
    so the caller can flag what's coming up.
    """
    today = date.today()
    out: list[dict] = []
    for c in _read():
        if status_filter is not None and c.status != status_filter:
            continue
        days_until_expiry: Optional[int] = None
        if c.expiry:
            try:
                days_until_expiry = (date.fromisoformat(c.expiry) - today).days
            except ValueError:
                pass
        out.append({
            "name": c.name,
            "type": c.type,
            "expiry": c.expiry,
            "remind_on": c.remind_on,
            "current_price": c.current_price,
            "prev_price": c.prev_price,
            "status": c.status,
            "last_reminded": c.last_reminded,
            "days_until_expiry": days_until_expiry,
            "notes": c.notes,
        })
    return out


def renew_contract(
    name: str,
    new_expiry: str,
    new_current_price: str,
    new_remind_on: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Record a renewal: rotate current_price → prev_price, set the new price
    and expiry, roll remind_on forward, and clear last_reminded so next year's
    reminder re-arms.

    If new_remind_on is omitted, it preserves the old lead gap (expiry −
    remind_on) against the new expiry, so a "remind 7 days before" contract
    stays "7 days before" next year.
    """
    _validate_date("new_expiry", new_expiry)
    if new_remind_on is not None:
        _validate_date("new_remind_on", new_remind_on)

    contracts = _read()
    c = _find(contracts, name)
    if c is None:
        raise ValueError(f"no contract matches {name!r}")

    if new_remind_on is None:
        gap_days = 0
        if c.expiry and c.remind_on:
            try:
                gap_days = (date.fromisoformat(c.expiry) - date.fromisoformat(c.remind_on)).days
            except ValueError:
                gap_days = 0
        from datetime import timedelta
        new_remind_on = (date.fromisoformat(new_expiry) - timedelta(days=max(gap_days, 0))).isoformat()

    old_price = c.current_price
    c.prev_price = old_price
    c.current_price = new_current_price
    c.expiry = new_expiry
    c.remind_on = new_remind_on
    c.last_reminded = None
    c.status = "active"
    if notes is not None:
        c.notes = notes
    _write(contracts)
    return {
        "name": c.name,
        "new_expiry": new_expiry,
        "new_remind_on": new_remind_on,
        "prev_price": old_price,
        "current_price": new_current_price,
    }


def update_contract(name: str, field: str, value: Optional[str]) -> dict:
    """Edit one field on the first matching contract. Allowed fields:
    type, expiry, remind_on, current_price, prev_price, status, notes.
    """
    if field not in UPDATABLE_FIELDS:
        raise ValueError(f"unknown field {field!r}; allowed: {sorted(UPDATABLE_FIELDS)}")
    if field == "type" and value not in VALID_TYPES:
        raise ValueError(f"type must be one of {sorted(VALID_TYPES)}")
    if field == "status" and value not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
    if field in ("expiry", "remind_on") and value:
        _validate_date(field, value)

    contracts = _read()
    c = _find(contracts, name)
    if c is None:
        raise ValueError(f"no contract matches {name!r}")
    setattr(c, field, value or None)
    _write(contracts)
    return {"name": c.name, "field": field, "value": value}


def mark_contract_reminded(name: str, date_str: str) -> dict:
    """Set last_reminded on a contract. Internal helper for the scheduler;
    NOT an LLM tool. Reminder idempotency lives here with
    contracts_needing_reminder: ping once per cycle, re-arm on renewal.
    """
    contracts = _read()
    c = _find(contracts, name)
    if c is None:
        raise ValueError(f"no contract matches {name!r}")
    c.last_reminded = date_str
    _write(contracts)
    return {"name": c.name, "last_reminded": date_str}


def contracts_needing_reminder(today: date) -> list[dict]:
    """Active contracts whose remind_on has arrived and that haven't been
    reminded since (last_reminded empty or earlier than remind_on). Reminds
    once per cycle; renew_contract clears last_reminded to re-arm.
    """
    due: list[dict] = []
    for c in list_contracts(status_filter="active"):
        remind_on = c.get("remind_on")
        if not remind_on:
            continue
        try:
            if date.fromisoformat(remind_on) > today:
                continue
        except ValueError:
            continue
        last = c.get("last_reminded") or ""
        if not last or last < remind_on:
            due.append(c)
    return due
