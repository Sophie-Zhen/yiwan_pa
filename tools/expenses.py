"""Tools for tracking 家庭花销 (household spending) in Google Sheets.

Phase 1 scope: the 明细 (line-item) tab only — an append-only ledger where one
shopping trip becomes N rows, one per item. This granularity is the point: it
is what lets us answer "how has the price of coffee beans moved?" and "what do
we buy the most?" later. The 库存 (inventory) tab is added in a later phase.

「明细」(line-item ledger) — append-only. One row per item per trip.
    A 日期    | input (YYYY-MM-DD)
    B 店铺    | input (store name; receipts here are English, so values are too)
    C 商品    | input (item name as printed on the receipt)
    D 数量    | input
    E 单位    | input, optional (each / kg / pack ... free text)
    F 单价    | input or formula (=G/D when only the line total is known)
    G 小计    | input or formula (=D*F when only the unit price is known)
    H 类别    | input, optional (coarse bucket: 食品 / 日用 / 装修 ...)
    I 备注    | input, optional

Why batch (record_purchase takes a list): a receipt is naturally many lines
sharing one date and store. Writing them in one call keeps the LLM from firing
N separate tool calls per receipt, and keeps the rows visually grouped.
"""

import os

from datetime import date

import gspread
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound

load_dotenv()

LEDGER_TAB = "明细"
COL_DATE = 1
COL_STORE = 2
COL_ITEM = 3
COL_QUANTITY = 4
COL_UNIT = 5
COL_UNIT_PRICE = 6
COL_SUBTOTAL = 7
COL_CATEGORY = 8
COL_NOTES = 9
NUM_COLS = 9
LAST_COL = "I"

# 「库存」(inventory) — entity tab in the SAME spreadsheet. One row per tracked
# item. Being a row IN this tab IS being on the watchlist: only items here get
# stock tracking and (Phase 3) restock reminders. A purchase of an item NOT in
# this tab just lands in 明细 — it does not auto-create inventory.
#     A 商品        | the watchlist name; also the substring matched against
#                     receipt line items for auto-restock (keep it distinctive)
#     B 当前数量    | current stock
#     C 单位        | each / kg / bag ...
#     D 补货策略    | one of VALID_STRATEGIES:
#                       'cycle'     → buy on a rough cadence (coffee beans);
#                                     low = long since last purchase. No need
#                                     to log consumption.
#                       'threshold' → consumed down to nothing (DIY materials);
#                                     low = 当前数量 <= 阈值. Needs decrement.
#     E 阈值        | threshold meaning depends on strategy:
#                       'threshold' → minimum quantity (required)
#                       'cycle'     → typical interval in DAYS (optional; blank
#                                     means derive from history in Phase 3)
#     F 上次购买日   | refreshed on auto-restock (YYYY-MM-DD)
#     G 上次单价     | refreshed on auto-restock
#     H 状态         | one of VALID_INVENTORY_STATUSES
#     I 上次提醒日期 | written by the restock scheduler; gates re-reminding
#     J 备注         | input
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


def _spreadsheet() -> gspread.Spreadsheet:
    client = gspread.service_account(filename=os.environ["GOOGLE_SHEETS_CREDENTIALS"])
    return client.open_by_key(os.environ["EXPENSES_SHEET_ID"])


def _ledger_tab() -> gspread.Worksheet:
    return _spreadsheet().worksheet(LEDGER_TAB)


def _inventory_tab() -> gspread.Worksheet:
    return _spreadsheet().worksheet(INVENTORY_TAB)


def _find_first_empty_row(tab: gspread.Worksheet) -> int:
    """First row (>=2) whose 日期 column is empty.

    Sequentially fills rows 2, 3, ... regardless of phantom structure. Mirrors
    the parcels.py / investments.py workaround for banded ranges making
    append_row jump rows.
    """
    all_values = tab.get_all_values()
    for i, row in enumerate(all_values[1:], start=2):
        cell = row[COL_DATE - 1] if len(row) >= COL_DATE else ""
        if not cell:
            return i
    return len(all_values) + 1


def _to_float(raw: str) -> float:
    """Parse a numeric cell to float; empty / non-numeric → 0.0."""
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def record_purchase(
    date: str,
    store: str,
    items: list[dict],
    notes: str | None = None,
) -> dict:
    """Append a shopping trip as N line-item rows sharing one date and store.

    Each item dict:
        item        (str, required)  — name as printed on the receipt
        quantity    (number, required)
        unit_price  (number, optional)
        subtotal    (number, optional)  — the line total
        unit        (str, optional)
        category    (str, optional)
        notes       (str, optional)

    Must provide unit_price or subtotal (or both) per item. When only one is
    given, the other is written as a formula so the trio (qty, unit_price,
    subtotal) stays consistent — same approach as parcels.record_parcel.

    The top-level `notes` (e.g. "周末囤货") is written to any item row that did
    not carry its own notes.
    """
    if not items:
        raise ValueError("items must be a non-empty list")

    tab = _ledger_tab()
    start_row = _find_first_empty_row(tab)

    rows: list[list] = []
    for offset, it in enumerate(items):
        r = start_row + offset
        name = it.get("item")
        if not name:
            raise ValueError(f"item #{offset} missing 'item' name")
        qty = it.get("quantity")
        if qty is None:
            raise ValueError(f"item {name!r} missing 'quantity'")
        unit_price = it.get("unit_price")
        subtotal = it.get("subtotal")
        if unit_price is None and subtotal is None:
            raise ValueError(f"item {name!r} needs unit_price or subtotal")

        row = [""] * NUM_COLS
        row[COL_DATE - 1] = date
        row[COL_STORE - 1] = store
        row[COL_ITEM - 1] = name
        row[COL_QUANTITY - 1] = qty
        row[COL_UNIT - 1] = it.get("unit", "")

        if unit_price is not None and subtotal is not None:
            row[COL_UNIT_PRICE - 1] = unit_price
            row[COL_SUBTOTAL - 1] = subtotal
        elif unit_price is not None:
            row[COL_UNIT_PRICE - 1] = unit_price
            row[COL_SUBTOTAL - 1] = f"=D{r}*F{r}"
        else:
            row[COL_UNIT_PRICE - 1] = f"=G{r}/D{r}"
            row[COL_SUBTOTAL - 1] = subtotal

        row[COL_CATEGORY - 1] = it.get("category", "")
        row[COL_NOTES - 1] = it.get("notes") or (notes or "")
        rows.append(row)

    end_row = start_row + len(rows) - 1
    tab.update(
        range_name=f"A{start_row}:{LAST_COL}{end_row}",
        values=rows,
        value_input_option="USER_ENTERED",
    )

    # Auto-restock: bump any watchlist item this trip replenished. Done at the
    # data layer (not left to the LLM) so "buying X also restocks X" can't be
    # forgotten — same reliability argument as record_investment's upsert.
    inventory_updates = _apply_purchase_to_inventory(date, items)

    return {
        "rows": [start_row, end_row],
        "count": len(rows),
        "date": date,
        "store": store,
        "inventory_updates": inventory_updates,
    }


def _apply_purchase_to_inventory(purchase_date: str, items: list[dict]) -> list[dict]:
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
    """
    try:
        tab = _inventory_tab()
    except WorksheetNotFound:
        return []

    all_values = tab.get_all_values()
    updates: list[dict] = []
    cells: list[gspread.cell.Cell] = []
    for i, row in enumerate(all_values[1:], start=2):
        name = row[INV_COL_ITEM - 1] if len(row) >= INV_COL_ITEM else ""
        status = row[INV_COL_STATUS - 1] if len(row) >= INV_COL_STATUS else ""
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
            old = _to_float(row[INV_COL_QUANTITY - 1] if len(row) >= INV_COL_QUANTITY else "")
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


def find_purchase(
    item: str | None = None,
    store: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Find line-item rows. All filters AND together.

    - item:  substring match on 商品 (case-insensitive)
    - store: substring match on 店铺 (case-insensitive)
    - since/until: inclusive YYYY-MM-DD bounds on 日期 (string compare is safe
      for zero-padded ISO dates)
    """
    tab = _ledger_tab()
    all_values = tab.get_all_values()
    item_lower = item.lower() if item else None
    store_lower = store.lower() if store else None

    matches: list[dict] = []
    for i, row in enumerate(all_values[1:], start=2):
        d = row[COL_DATE - 1] if len(row) >= COL_DATE else ""
        if not d:
            continue
        name = row[COL_ITEM - 1] if len(row) >= COL_ITEM else ""
        st = row[COL_STORE - 1] if len(row) >= COL_STORE else ""
        if item_lower is not None and item_lower not in name.lower():
            continue
        if store_lower is not None and store_lower not in st.lower():
            continue
        if since is not None and d < since:
            continue
        if until is not None and d > until:
            continue
        matches.append({
            "row": i,
            "date": d,
            "store": st,
            "item": name,
            "quantity": row[COL_QUANTITY - 1] if len(row) >= COL_QUANTITY else "",
            "unit": row[COL_UNIT - 1] if len(row) >= COL_UNIT else "",
            "unit_price": row[COL_UNIT_PRICE - 1] if len(row) >= COL_UNIT_PRICE else "",
            "subtotal": row[COL_SUBTOTAL - 1] if len(row) >= COL_SUBTOTAL else "",
            "category": row[COL_CATEGORY - 1] if len(row) >= COL_CATEGORY else "",
            "notes": row[COL_NOTES - 1] if len(row) >= COL_NOTES else "",
        })
    return matches


def price_history(item: str) -> list[dict]:
    """Every purchase of an item (substring match), sorted by date ascending.

    Returns the fields needed to eyeball a price trend: date, store, unit,
    quantity, unit_price. This is the raw series; the caller (LLM) narrates
    whether the price rose or fell.
    """
    rows = find_purchase(item=item)
    rows.sort(key=lambda r: r["date"])
    return [
        {
            "date": r["date"],
            "store": r["store"],
            "item": r["item"],
            "unit": r["unit"],
            "quantity": r["quantity"],
            "unit_price": r["unit_price"],
        }
        for r in rows
    ]


def top_items(
    since: str | None = None,
    until: str | None = None,
    by: str = "spend",
    limit: int = 15,
) -> list[dict]:
    """Aggregate the ledger by item to answer "what do we buy the most?".

    Groups rows by 商品 (lowercased for the key, original casing kept for
    display). For each item reports times (number of purchase rows), total
    quantity, and total spend. Sorted by `by` ∈ {'spend', 'count', 'quantity'}
    descending; top `limit` returned.

    Date range via since/until (inclusive YYYY-MM-DD), same semantics as
    find_purchase.
    """
    valid_by = {"spend", "count", "quantity"}
    if by not in valid_by:
        raise ValueError(f"by must be one of {sorted(valid_by)}, got {by!r}")

    rows = find_purchase(since=since, until=until)
    agg: dict[str, dict] = {}
    for r in rows:
        key = r["item"].lower()
        bucket = agg.setdefault(
            key, {"item": r["item"], "times": 0, "quantity": 0.0, "spend": 0.0}
        )
        bucket["times"] += 1
        bucket["quantity"] += _to_float(str(r["quantity"]))
        bucket["spend"] += _to_float(str(r["subtotal"]))

    sort_key = {"spend": "spend", "count": "times", "quantity": "quantity"}[by]
    ranked = sorted(agg.values(), key=lambda b: b[sort_key], reverse=True)
    return [
        {
            "item": b["item"],
            "times": b["times"],
            "quantity": round(b["quantity"], 3),
            "spend": round(b["spend"], 2),
        }
        for b in ranked[:limit]
    ]


# --- 库存 (inventory) ---------------------------------------------------------

def _find_first_empty_inventory_row(tab: gspread.Worksheet) -> int:
    all_values = tab.get_all_values()
    for i, row in enumerate(all_values[1:], start=2):
        cell = row[INV_COL_ITEM - 1] if len(row) >= INV_COL_ITEM else ""
        if not cell:
            return i
    return len(all_values) + 1


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
        name = row[INV_COL_ITEM - 1] if len(row) >= INV_COL_ITEM else ""
        if name.lower() == item.lower():
            existing_row = i
            existing = row
            break

    def _keep(col: int) -> str:
        if existing is None:
            return ""
        return existing[col - 1] if len(existing) >= col else ""

    row = [""] * INV_NUM_COLS
    row[INV_COL_ITEM - 1] = item
    row[INV_COL_QUANTITY - 1] = (
        current_quantity if current_quantity is not None
        else (_keep(INV_COL_QUANTITY) if existing else 0)
    )
    row[INV_COL_UNIT - 1] = unit
    row[INV_COL_STRATEGY - 1] = strategy
    if threshold is not None:
        row[INV_COL_THRESHOLD - 1] = threshold
    elif existing:
        row[INV_COL_THRESHOLD - 1] = _keep(INV_COL_THRESHOLD)
    row[INV_COL_LAST_PURCHASE - 1] = last_purchase_date if last_purchase_date is not None else _keep(INV_COL_LAST_PURCHASE)
    if last_unit_price is not None:
        row[INV_COL_LAST_PRICE - 1] = last_unit_price
    elif existing:
        row[INV_COL_LAST_PRICE - 1] = _keep(INV_COL_LAST_PRICE)
    row[INV_COL_STATUS - 1] = "active"
    # Preserve reminder state across re-tracking so changing a setting doesn't
    # silently re-arm (or suppress) a restock reminder.
    row[INV_COL_LAST_REMINDED - 1] = _keep(INV_COL_LAST_REMINDED)
    row[INV_COL_NOTES - 1] = notes if notes is not None else _keep(INV_COL_NOTES)

    target_row = existing_row if existing_row is not None else _find_first_empty_inventory_row(tab)
    tab.update(
        range_name=f"A{target_row}:{INV_LAST_COL}{target_row}",
        values=[row],
        value_input_option="USER_ENTERED",
    )
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
        name = row[INV_COL_ITEM - 1] if len(row) >= INV_COL_ITEM else ""
        status = row[INV_COL_STATUS - 1] if len(row) >= INV_COL_STATUS else ""
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
    old = _to_float(row[INV_COL_QUANTITY - 1] if len(row) >= INV_COL_QUANTITY else "")
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
        name = row[INV_COL_ITEM - 1] if len(row) >= INV_COL_ITEM else ""
        if not name:
            continue
        status = row[INV_COL_STATUS - 1] if len(row) >= INV_COL_STATUS else ""
        if status_filter is not None and status != status_filter:
            continue

        strategy = row[INV_COL_STRATEGY - 1] if len(row) >= INV_COL_STRATEGY else ""
        qty = _to_float(row[INV_COL_QUANTITY - 1] if len(row) >= INV_COL_QUANTITY else "")
        threshold_raw = row[INV_COL_THRESHOLD - 1] if len(row) >= INV_COL_THRESHOLD else ""
        last_purchase = row[INV_COL_LAST_PURCHASE - 1] if len(row) >= INV_COL_LAST_PURCHASE else ""

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
            "unit": row[INV_COL_UNIT - 1] if len(row) >= INV_COL_UNIT else "",
            "strategy": strategy,
            "threshold": threshold_raw,
            "last_purchase_date": last_purchase,
            "last_unit_price": row[INV_COL_LAST_PRICE - 1] if len(row) >= INV_COL_LAST_PRICE else "",
            "days_since_purchase": days_since,
            "low": low,
            "status": status,
            "last_reminded": row[INV_COL_LAST_REMINDED - 1] if len(row) >= INV_COL_LAST_REMINDED else "",
            "notes": row[INV_COL_NOTES - 1] if len(row) >= INV_COL_NOTES else "",
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
