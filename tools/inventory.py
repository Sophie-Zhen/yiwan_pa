"""Tools for the 库存 (inventory) watchlist in the 家庭花销 Google Sheet.

Split out of tools/expenses.py: the 明细 ledger (that module) and this 库存
watchlist are two responsibilities sharing one spreadsheet. Being a row in the
库存 tab IS being on the watchlist — only items here get stock tracking and
restock reminders. A purchase of an item NOT in this tab just lands in 明细.

The one coupling back to the ledger is auto-restock: expenses.record_purchase
calls apply_purchase() so "buying X also restocks X" happens at the data layer.

「库存」(inventory) — entity tab in the SAME spreadsheet (EXPENSES_SHEET_ID).
One row per tracked item.
    A 商品        | the watchlist name; also the substring matched against
                    receipt line items for auto-restock (keep it distinctive)
    B 当前数量    | current stock
    C 单位        | each / kg / bag ...
    D 补货策略    | one of VALID_STRATEGIES:
                      'cycle'     → buy on a rough cadence (coffee beans);
                                    low = long since last purchase. No need
                                    to log consumption.
                      'threshold' → consumed down to nothing (DIY materials);
                                    low = 当前数量 <= 阈值. Needs decrement.
    E 阈值        | threshold meaning depends on strategy:
                      'threshold' → minimum quantity (required)
                      'cycle'     → typical interval in DAYS (optional; blank
                                    means derive from history in Phase 3)
    F 上次购买日   | refreshed on auto-restock (YYYY-MM-DD)
    G 上次单价     | refreshed on auto-restock
    H 状态         | one of VALID_INVENTORY_STATUSES
    I 上次提醒日期 | written by the restock scheduler; gates re-reminding
    J 备注         | input
"""

from datetime import date

import gspread
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound

from storage import sheets

load_dotenv()

INVENTORY_TAB = "库存"
INV_COL_ITEM = 1
INV_COL_QUANTITY = 2
INV_COL_UNIT = 3
INV_COL_STRATEGY = 4
INV_COL_THRESHOLD = 5
INV_COL_LAST_PURCHASE = 6
INV_COL_LAST_PRICE = 7
INV_COL_STATUS = 8
INV_COL_LAST_REMINDED = 9
INV_COL_NOTES = 10
INV_NUM_COLS = 10
INV_LAST_COL = "J"

STRATEGY_CYCLE = "cycle"
STRATEGY_THRESHOLD = "threshold"
VALID_STRATEGIES = {STRATEGY_CYCLE, STRATEGY_THRESHOLD}
VALID_INVENTORY_STATUSES = {"active", "archived"}


def _inventory_tab() -> gspread.Worksheet:
    return sheets.open_sheet("EXPENSES_SHEET_ID").worksheet(INVENTORY_TAB)


def apply_purchase(purchase_date: str, items: list[dict]) -> list[dict]:
    """Add purchased quantities to matching ACTIVE inventory rows.

    Matching is intentionally simple: an inventory item matches a purchased
    line when the inventory name (lowercased) is a SUBSTRING of the receipt
    line name. So a watchlist entry 'coffee' catches 'Coffee Beans 1kg'. Keep
    watchlist names distinctive to avoid over-matching. Watchlist-only: this
    never creates new inventory rows — a purchase of an untracked item is
    ledger-only.

    For each matched inventory row: 当前数量 += summed purchased quantity,
    上次购买日 ← purchase_date, 上次单价 ← the matched line's unit price
    (explicit, else subtotal/quantity).

    No-ops silently if the 库存 tab doesn't exist yet (Phase 1 sheets).
    Returns [{item, added, new_quantity}] for rows actually bumped.

    Called by expenses.record_purchase — the one coupling between the ledger
    and the watchlist (auto-restock).
    """
    try:
        tab = _inventory_tab()
    except WorksheetNotFound:
        return []

    all_values = tab.get_all_values()
    updates: list[dict] = []
    cells: list[gspread.cell.Cell] = []
    for i, row in enumerate(all_values[1:], start=2):
        name = sheets.cell(row, INV_COL_ITEM)
        status = sheets.cell(row, INV_COL_STATUS)
        if not name or status == "archived":
            continue
        name_lower = name.lower()

        added = 0.0
        last_price: float | None = None
        for it in items:
            if name_lower not in it["item"].lower():
                continue
            qty = float(it["quantity"])
            added += qty
            up = it.get("unit_price")
            if up is None and it.get("subtotal") is not None and qty:
                up = float(it["subtotal"]) / qty
            if up is not None:
                last_price = float(up)

        if added > 0:
            old = sheets.to_float(sheets.cell(row, INV_COL_QUANTITY))
            new_q = old + added
            cells.append(gspread.cell.Cell(i, INV_COL_QUANTITY, new_q))
            cells.append(gspread.cell.Cell(i, INV_COL_LAST_PURCHASE, purchase_date))
            if last_price is not None:
                cells.append(gspread.cell.Cell(i, INV_COL_LAST_PRICE, last_price))
            updates.append(
                {"item": name, "added": round(added, 3), "new_quantity": round(new_q, 3)}
            )

    if cells:
        tab.update_cells(cells, value_input_option="USER_ENTERED")
    return updates


def track_item(
    item: str,
    unit: str,
    strategy: str,
    threshold: float | None = None,
    current_quantity: float | None = None,
    last_purchase_date: str | None = None,
    last_unit_price: float | None = None,
    notes: str | None = None,
) -> dict:
    """Add an item to the inventory watchlist, or update its settings.

    Upsert by item name (case-insensitive exact). On update, only the fields
    you pass are overwritten; omitted fields keep their existing values — so
    re-calling to change a threshold won't wipe the accumulated 当前数量.

    strategy selects what 阈值 means and whether consumption must be logged:
      - 'threshold' → threshold = minimum quantity (REQUIRED). Low when stock
                      falls to/below it. Decrement via adjust_inventory.
      - 'cycle'     → threshold = typical interval in days (optional). Low when
                      it's been that long since 上次购买日. No need to log use.

    current_quantity sets the starting stock (defaults to 0 on a new item;
    preserved on update if omitted).
    """
    if strategy not in VALID_STRATEGIES:
        raise ValueError(
            f"Invalid strategy {strategy!r}. Must be one of: {sorted(VALID_STRATEGIES)}"
        )
    if strategy == STRATEGY_THRESHOLD and threshold is None:
        raise ValueError("strategy='threshold' requires threshold (minimum quantity)")

    tab = _inventory_tab()
    all_values = tab.get_all_values()

    existing_row: int | None = None
    existing: list[str] | None = None
    for i, row in enumerate(all_values[1:], start=2):
        name = sheets.cell(row, INV_COL_ITEM)
        if name.lower() == item.lower():
            existing_row = i
            existing = row
            break

    row = [""] * INV_NUM_COLS
    row[INV_COL_ITEM - 1] = item
    row[INV_COL_QUANTITY - 1] = (
        current_quantity if current_quantity is not None
        else (sheets.keep(existing, INV_COL_QUANTITY) if existing else 0)
    )
    row[INV_COL_UNIT - 1] = unit
    row[INV_COL_STRATEGY - 1] = strategy
    if threshold is not None:
        row[INV_COL_THRESHOLD - 1] = threshold
    elif existing:
        row[INV_COL_THRESHOLD - 1] = sheets.keep(existing, INV_COL_THRESHOLD)
    row[INV_COL_LAST_PURCHASE - 1] = last_purchase_date if last_purchase_date is not None else sheets.keep(existing, INV_COL_LAST_PURCHASE)
    if last_unit_price is not None:
        row[INV_COL_LAST_PRICE - 1] = last_unit_price
    elif existing:
        row[INV_COL_LAST_PRICE - 1] = sheets.keep(existing, INV_COL_LAST_PRICE)
    row[INV_COL_STATUS - 1] = "active"
    # Preserve reminder state across re-tracking so changing a setting doesn't
    # silently re-arm (or suppress) a restock reminder.
    row[INV_COL_LAST_REMINDED - 1] = sheets.keep(existing, INV_COL_LAST_REMINDED)
    row[INV_COL_NOTES - 1] = notes if notes is not None else sheets.keep(existing, INV_COL_NOTES)

    target_row = existing_row if existing_row is not None else sheets.first_empty_row(tab, INV_COL_ITEM)
    sheets.write_row(tab, target_row, row, INV_LAST_COL)
    return {
        "row": target_row,
        "item": item,
        "strategy": strategy,
        "operation": "updated" if existing_row is not None else "inserted",
    }


def adjust_inventory(
    item: str,
    delta: float | None = None,
    set_quantity: float | None = None,
    notes: str | None = None,
) -> dict:
    """Change a tracked item's 当前数量 — for consumption or correction.

    Matches one ACTIVE inventory row by substring (case-insensitive). Raises if
    nothing matches (item not tracked → use track_item first) or if more than
    one matches (ambiguous → caller should be more specific).

    Use delta for relative change ("用了2袋" → delta=-2; bought 1 by hand →
    delta=+1) or set_quantity for an absolute reading ("还剩半袋" →
    set_quantity=0.5; "没了" → set_quantity=0). One of the two is required.
    """
    if delta is None and set_quantity is None and notes is None:
        raise ValueError("provide delta, set_quantity, or notes")

    tab = _inventory_tab()
    all_values = tab.get_all_values()
    item_lower = item.lower()
    matches: list[tuple[int, list[str]]] = []
    for i, row in enumerate(all_values[1:], start=2):
        name = sheets.cell(row, INV_COL_ITEM)
        status = sheets.cell(row, INV_COL_STATUS)
        if name and status != "archived" and item_lower in name.lower():
            matches.append((i, row))

    if not matches:
        raise ValueError(
            f"No tracked item matches {item!r}. Use track_item to start tracking it."
        )
    if len(matches) > 1:
        names = [m[1][INV_COL_ITEM - 1] for m in matches]
        raise ValueError(f"{item!r} matches multiple tracked items: {names}. Be more specific.")

    r, row = matches[0]
    name = row[INV_COL_ITEM - 1]
    old = sheets.to_float(sheets.cell(row, INV_COL_QUANTITY))
    if set_quantity is not None:
        new_q = float(set_quantity)
    else:
        new_q = old + float(delta or 0)

    cells = [gspread.cell.Cell(r, INV_COL_QUANTITY, new_q)]
    if notes is not None:
        cells.append(gspread.cell.Cell(r, INV_COL_NOTES, notes))
    tab.update_cells(cells, value_input_option="USER_ENTERED")
    return {
        "row": r,
        "item": name,
        "old_quantity": round(old, 3),
        "new_quantity": round(new_q, 3),
    }


def list_inventory(status_filter: str | None = "active", low_only: bool = False) -> list[dict]:
    """List inventory items with a computed low-stock flag.

    `low` is derived, not stored (so it can't go stale):
      - 'threshold' items: low when 当前数量 <= 阈值.
      - 'cycle' items: low when days since 上次购买日 >= 阈值(interval). If the
        item has no interval set, low stays False here (Phase 3 will derive a
        cadence from purchase history); days_since_purchase is still reported.

    low_only=True returns only the items flagged low (the restock list).
    """
    tab = _inventory_tab()
    all_values = tab.get_all_values()
    today = date.today()

    out: list[dict] = []
    for i, row in enumerate(all_values[1:], start=2):
        name = sheets.cell(row, INV_COL_ITEM)
        if not name:
            continue
        status = sheets.cell(row, INV_COL_STATUS)
        if status_filter is not None and status != status_filter:
            continue

        strategy = sheets.cell(row, INV_COL_STRATEGY)
        qty = sheets.to_float(sheets.cell(row, INV_COL_QUANTITY))
        threshold_raw = sheets.cell(row, INV_COL_THRESHOLD)
        last_purchase = sheets.cell(row, INV_COL_LAST_PURCHASE)

        days_since: int | None = None
        if last_purchase:
            try:
                days_since = (today - date.fromisoformat(last_purchase)).days
            except ValueError:
                pass

        low = False
        if strategy == STRATEGY_THRESHOLD and threshold_raw:
            low = qty <= float(threshold_raw)
        elif strategy == STRATEGY_CYCLE and threshold_raw and days_since is not None:
            low = days_since >= float(threshold_raw)

        if low_only and not low:
            continue

        out.append({
            "row": i,
            "item": name,
            "quantity": qty,
            "unit": sheets.cell(row, INV_COL_UNIT),
            "strategy": strategy,
            "threshold": threshold_raw,
            "last_purchase_date": last_purchase,
            "last_unit_price": sheets.cell(row, INV_COL_LAST_PRICE),
            "days_since_purchase": days_since,
            "low": low,
            "status": status,
            "last_reminded": sheets.cell(row, INV_COL_LAST_REMINDED),
            "notes": sheets.cell(row, INV_COL_NOTES),
        })
    return out


def mark_inventory_reminded(row: int, date_str: str) -> dict:
    """Set 上次提醒日期 on an inventory row. Internal helper for the restock
    scheduler; NOT an LLM tool. Restock idempotency lives here together with
    items_needing_restock_reminder: a low item is reminded once, then stays
    quiet until a purchase advances 上次购买日 past 上次提醒日期.
    """
    tab = _inventory_tab()
    tab.update_cell(row, INV_COL_LAST_REMINDED, date_str)
    return {"row": row, "last_reminded": date_str}


def items_needing_restock_reminder() -> list[dict]:
    """Active, low-stock items not yet reminded since their last restock.

    Reuses list_inventory's derived `low`. An item is due when it is low AND
    (never reminded OR last reminded before the last purchase) — so a reminder
    fires once when stock goes low, then stays quiet until a purchase re-arms
    it. Items never purchased (no 上次购买日) are reminded once and then quiet.
    """
    due: list[dict] = []
    for it in list_inventory(low_only=True):
        last_reminded = it.get("last_reminded") or ""
        last_purchase = it.get("last_purchase_date") or ""
        if not last_reminded or (last_purchase and last_reminded < last_purchase):
            due.append(it)
    return due
