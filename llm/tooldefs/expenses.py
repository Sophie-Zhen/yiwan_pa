"""家庭花销 spending + 库存 inventory tools (Google Sheet).

Tool schemas + dispatch handlers for the expenses domain.
"""
from tools.expenses import (
    amend_purchase,
    find_purchase,
    price_history,
    record_purchase,
    record_transaction,
    set_purchase_quantity,
    spend_summary,
    top_items,
    void_purchase,
)
from tools.inventory import (
    adjust_inventory,
    list_inventory,
    track_item,
)

HANDLERS = {
    "record_purchase": lambda a: record_purchase(
        date=a["date"], store=a["store"], items=a["items"], notes=a.get("notes"),
        receipt_total=a.get("receipt_total"),
    ),
    "record_transaction": lambda a: record_transaction(
        date=a["date"], description=a["description"], amount=a["amount"],
        direction=a["direction"], account=a.get("account", "现金"),
        category=a.get("category"), notes=a.get("notes"),
    ),
    "find_purchase": lambda a: find_purchase(
        item=a.get("item"), store=a.get("store"), since=a.get("since"), until=a.get("until"),
    ),
    "price_history": lambda a: price_history(item=a["item"]),
    "top_items": lambda a: top_items(
        by=a.get("by", "spend"), since=a.get("since"), until=a.get("until"), limit=a.get("limit", 15),
    ),
    "spend_summary": lambda a: spend_summary(since=a.get("since"), until=a.get("until")),
    "track_item": lambda a: track_item(
        item=a["item"], unit=a["unit"], strategy=a["strategy"], threshold=a.get("threshold"),
        current_quantity=a.get("current_quantity"), last_purchase_date=a.get("last_purchase_date"),
        last_unit_price=a.get("last_unit_price"), notes=a.get("notes"),
    ),
    "adjust_inventory": lambda a: adjust_inventory(
        item=a["item"], delta=a.get("delta"), set_quantity=a.get("set_quantity"), notes=a.get("notes"),
    ),
    "list_inventory": lambda a: list_inventory(
        status_filter=a.get("status_filter", "active"), low_only=a.get("low_only", False),
    ),
    "amend_purchase": lambda a: amend_purchase(
        rows=a["rows"], field=a["field"], value=a["value"],
    ),
    "set_purchase_quantity": lambda a: set_purchase_quantity(
        row=a["row"], quantity=a["quantity"],
    ),
    "void_purchase": lambda a: void_purchase(rows=a["rows"]),
}


SCHEMAS = [{'name': 'record_purchase',
  'description': 'Record one shopping trip as line items in the 家庭花销 明细 ledger. Each item '
                 "becomes its own row sharing the trip's date and store — this per-item detail "
                 "is what powers price-trend and 'what we buy most' queries later, so capture "
                 'EVERY line, not a lump sum. Receipts here are English (user is in Ireland); '
                 'keep item names as printed. Use unit_price when the receipt shows a per-unit '
                 'price; use subtotal when it only shows the line total (e.g. loose produce '
                 'sold by weight); pass both if both are visible. IMPORTANT: when the input is '
                 'a receipt photo, do NOT call this immediately — first reply with the parsed '
                 'lines (store, date, each item/qty/price) and ask the user to confirm, then '
                 "call record_purchase after they say it's correct. Manual text entry of a "
                 'single item can be recorded directly. Auto-restock: any tracked inventory '
                 "item this trip replenished is bumped automatically — the result's "
                 'inventory_updates lists what changed; mention it briefly if non-empty (e.g. '
                 "'咖啡豆库存 +1 → 现 3 袋').",
  'input_schema': {'type': 'object',
                   'properties': {'date': {'type': 'string',
                                           'description': 'Purchase date YYYY-MM-DD (from the '
                                                          'receipt; use today if the user '
                                                          'gives none).'},
                                  'store': {'type': 'string',
                                            'description': "Store name, e.g. 'Lidl', 'Tesco', "
                                                           "'Amazon'."},
                                  'items': {'type': 'array',
                                            'description': 'One object per line item.',
                                            'items': {'type': 'object',
                                                      'properties': {'item': {'type': 'string',
                                                                              'description': 'Item '
                                                                                             'name '
                                                                                             'as '
                                                                                             'printed '
                                                                                             'on '
                                                                                             'the '
                                                                                             'receipt.'},
                                                                     'quantity': {'type': 'number',
                                                                                  'description': 'Quantity '
                                                                                                 '(count '
                                                                                                 'or '
                                                                                                 'weight).'},
                                                                     'unit_price': {'type': 'number',
                                                                                    'description': 'Price '
                                                                                                   'per '
                                                                                                   'unit. '
                                                                                                   'Omit '
                                                                                                   'if '
                                                                                                   'only '
                                                                                                   'the '
                                                                                                   'line '
                                                                                                   'total '
                                                                                                   'is '
                                                                                                   'known.'},
                                                                     'subtotal': {'type': 'number',
                                                                                  'description': 'Line '
                                                                                                 'total. '
                                                                                                 'Omit '
                                                                                                 'if '
                                                                                                 'only '
                                                                                                 'the '
                                                                                                 'unit '
                                                                                                 'price '
                                                                                                 'is '
                                                                                                 'known.'},
                                                                     'unit': {'type': 'string',
                                                                              'description': 'Optional '
                                                                                             'unit, '
                                                                                             'e.g. '
                                                                                             "'each', "
                                                                                             "'kg', "
                                                                                             "'pack'."},
                                                                     'category': {'type': 'string',
                                                                                  'description': 'Optional '
                                                                                                 'coarse '
                                                                                                 'bucket, '
                                                                                                 'e.g. '
                                                                                                 "'食品', "
                                                                                                 "'日用', "
                                                                                                 "'装修'."},
                                                                     'notes': {'type': 'string',
                                                                               'description': 'Optional '
                                                                                              'per-item '
                                                                                              'note.'}},
                                                      'required': ['item', 'quantity']}},
                                  'notes': {'type': 'string',
                                            'description': 'Optional trip-level note, applied '
                                                           'to item rows that have no note of '
                                                           'their own.'},
                                  'receipt_total': {'type': 'number',
                                                    'description': 'The grand total PRINTED on '
                                                                   'the receipt. Always pass it '
                                                                   'when visible: the result '
                                                                   'returns a reconcile block '
                                                                   'checking it against the sum '
                                                                   'of the line items, which '
                                                                   'catches a dropped or '
                                                                   'miscounted line.'}},
                   'required': ['date', 'store', 'items']}},
 {'name': 'record_transaction',
  'description': 'Record ONE manually-tracked transaction in the 单笔 (transactions) ledger — '
                 'the complete one-row-per-transaction record. Use this ONLY for money a bank '
                 'statement will NOT import: CASH spending or income, a cash settle with '
                 'someone. Examples: "付 AnyVan 尾款现金 120" / "现金买菜 25" / "收到现金 200". '
                 'direction is "支出" (money out) or "收入" (money in); amount is the positive '
                 'number — the sign is applied automatically. account defaults to 现金. For a '
                 '支出, set category to one of the 单笔 buckets: 能源/通讯/交通/燃料/充电/超市/食品/面包/咖啡豆/'
                 '餐饮/日用/家居/垃圾处理/装修/保险/税费/软件/运动/旅游/娱乐/教育/快递/其他 (leave blank for 收入). '
                 'DO NOT use this for card/bank purchases — the monthly statement import already '
                 'brings those into 单笔, so logging them here double-counts. For a supermarket '
                 'receipt with multiple line items, use record_purchase (→ 明细) instead.',
  'input_schema': {'type': 'object',
                   'properties': {'date': {'type': 'string',
                                           'description': 'Transaction date YYYY-MM-DD (use '
                                                          'today if the user gives none).'},
                                  'description': {'type': 'string',
                                                  'description': 'What it was, e.g. "AnyVan '
                                                                 'final payment (curtain '
                                                                 'track)".'},
                                  'amount': {'type': 'number',
                                             'description': 'Positive magnitude in EUR; the '
                                                            'sign is set from direction.'},
                                  'direction': {'type': 'string',
                                                'enum': ['支出', '收入'],
                                                'description': '支出 = money out (stored '
                                                               'negative), 收入 = money in '
                                                               '(stored positive).'},
                                  'account': {'type': 'string',
                                              'description': 'Source of funds. Defaults to 现金 '
                                                             '(cash) — the usual manual case.'},
                                  'category': {'type': 'string',
                                               'description': 'For 支出: a 单笔 taxonomy bucket '
                                                              '(交通/餐饮/装修/日用/…). Omit for '
                                                              '收入.'},
                                  'notes': {'type': 'string',
                                            'description': 'Optional note.'}},
                   'required': ['date', 'description', 'amount', 'direction']}},
 {'name': 'find_purchase',
  'description': 'Look up line items in the 花销 明细 ledger. Filters AND together: item '
                 '(substring), store (substring), since/until (inclusive YYYY-MM-DD date '
                 "bounds). Use for '上次在 X 买了啥', '6 月在 Tesco 花在哪些东西上'. For a pure price trend "
                 'of one product, prefer price_history. DO NOT estimate from memory — call '
                 'this tool.',
  'input_schema': {'type': 'object',
                   'properties': {'item': {'type': 'string',
                                           'description': 'Substring match on 商品.'},
                                  'store': {'type': 'string',
                                            'description': 'Substring match on 店铺.'},
                                  'since': {'type': 'string',
                                            'description': 'Inclusive start date YYYY-MM-DD.'},
                                  'until': {'type': 'string',
                                            'description': 'Inclusive end date YYYY-MM-DD.'}},
                   'required': []}},
 {'name': 'price_history',
  'description': 'Every purchase of a product (substring match on 商品), sorted oldest→newest, '
                 "with date/store/quantity/unit_price. Use to answer '咖啡豆涨价了吗', 'X 最近多少钱', "
                 "'price trend'. Returns the raw series; you read off whether the price rose "
                 'or fell and by how much. DO NOT estimate from memory.',
  'input_schema': {'type': 'object',
                   'properties': {'item': {'type': 'string',
                                           'description': 'Product name substring, e.g. '
                                                          "'coffee'."}},
                   'required': ['item']}},
 {'name': 'top_items',
  'description': "Rank items in the 花销 明细 ledger to answer 'what do we buy the most / spend "
                 "the most on'. Reports per item: times (purchase rows), total quantity, total "
                 "spend. Sort by 'spend' (default), 'count', or 'quantity'. Optional "
                 'since/until date range. DO NOT estimate from memory.',
  'input_schema': {'type': 'object',
                   'properties': {'by': {'type': 'string',
                                         'enum': ['spend', 'count', 'quantity'],
                                         'description': "Ranking metric. Default 'spend'."},
                                  'since': {'type': 'string',
                                            'description': 'Inclusive start date YYYY-MM-DD.'},
                                  'until': {'type': 'string',
                                            'description': 'Inclusive end date YYYY-MM-DD.'},
                                  'limit': {'type': 'integer',
                                            'description': 'Max items to return (default '
                                                           '15).'}},
                   'required': []}},
 {'name': 'spend_summary',
  'description': 'Total household spending over a date range, broken down by 类别 (category). '
                 "Use for '这个月花了多少', '6 月各类花了多少', 'X 月到 Y 月总支出'. The category breakdown makes "
                 'big fixed/annual costs (保险/能源/固定支出) show up next to everyday spending, so '
                 'the total has no unexplained gap. Computed from the ledger — DO NOT '
                 'estimate. Note: only the 花销 ledger is summed here; contract premiums are NOT '
                 'double-counted (contracts are reminders only) — the annual payment is '
                 "captured because it's also recorded as a 花销 line under a fixed-cost category "
                 'when paid.',
  'input_schema': {'type': 'object',
                   'properties': {'since': {'type': 'string',
                                            'description': 'Inclusive start date YYYY-MM-DD '
                                                           '(e.g. month start).'},
                                  'until': {'type': 'string',
                                            'description': 'Inclusive end date YYYY-MM-DD '
                                                           '(e.g. month end).'}},
                   'required': []}},
 {'name': 'track_item',
  'description': 'Add an item to the 花销 库存 (inventory) watchlist, or update its settings. '
                 'Being on this watchlist is what enables stock tracking and (future) restock '
                 'reminders for it — untracked purchases just go to the ledger. Upsert by '
                 "name: re-calling updates only the fields you pass (won't wipe accumulated "
                 "quantity). Pick strategy by the item's nature: 'cycle' for things bought on "
                 'a rough cadence and consumed steadily (coffee beans, milk) — no need to log '
                 "consumption, low = long since last bought; 'threshold' for things used down "
                 'to nothing with no regular need (DIY materials like cement, screws) — low = '
                 'quantity at/below the minimum, so consumption must be logged via '
                 "adjust_inventory. For 'threshold' you MUST pass threshold (the minimum "
                 "quantity). For 'cycle' threshold is the typical interval in days (optional). "
                 "Triggers: '开始跟踪/记一下库存 X', '把 X 加入库存', '咖啡豆还剩 2 袋，低于 1 袋提醒我'.",
  'input_schema': {'type': 'object',
                   'properties': {'item': {'type': 'string',
                                           'description': 'Watchlist name. Keep it a '
                                                          'distinctive substring of how it '
                                                          "appears on receipts (e.g. 'coffee', "
                                                          "'cement') so purchases auto-match."},
                                  'unit': {'type': 'string',
                                           'description': "Unit of stock, e.g. 'bag', 'kg', "
                                                          "'each'."},
                                  'strategy': {'type': 'string',
                                               'enum': ['cycle', 'threshold'],
                                               'description': "'cycle' = periodic buy "
                                                              "(coffee); 'threshold' = "
                                                              'consumed to zero (DIY '
                                                              'materials).'},
                                  'threshold': {'type': 'number',
                                                'description': "For 'threshold': minimum "
                                                               'quantity (required). For '
                                                               "'cycle': typical interval in "
                                                               'days (optional).'},
                                  'current_quantity': {'type': 'number',
                                                       'description': 'Starting stock. '
                                                                      'Defaults to 0 on a new '
                                                                      'item; preserved on '
                                                                      'update if omitted.'},
                                  'last_purchase_date': {'type': 'string',
                                                         'description': 'Optional YYYY-MM-DD '
                                                                        'of the last '
                                                                        'purchase.'},
                                  'last_unit_price': {'type': 'number',
                                                      'description': 'Optional last known unit '
                                                                     'price.'},
                                  'notes': {'type': 'string', 'description': 'Optional note.'}},
                   'required': ['item', 'unit', 'strategy']}},
 {'name': 'adjust_inventory',
  'description': "Change a tracked item's current stock — for consumption or correction. "
                 'Matches one active inventory item by substring. Use `delta` for a relative '
                 "change ('用了2袋' → delta=-2; bought one by hand → delta=+1) or `set_quantity` "
                 "for an absolute reading ('还剩半袋' → set_quantity=0.5; '没了/用完了' → "
                 'set_quantity=0). Do NOT use this for normal purchases recorded via '
                 'record_purchase — those auto-restock. This is for consumption and manual '
                 'fixes.',
  'input_schema': {'type': 'object',
                   'properties': {'item': {'type': 'string',
                                           'description': "Substring of the tracked item's "
                                                          'name.'},
                                  'delta': {'type': 'number',
                                            'description': 'Relative change (negative for '
                                                           'consumption).'},
                                  'set_quantity': {'type': 'number',
                                                   'description': 'Absolute new quantity '
                                                                  '(overrides delta).'},
                                  'notes': {'type': 'string', 'description': 'Optional note.'}},
                   'required': ['item']}},
 {'name': 'list_inventory',
  'description': "List inventory items with a computed low-stock flag. Use for '库存还有啥', "
                 "'什么快没了/该买什么' (pass low_only=true for the restock list), 'X 还剩多少'. `low` is "
                 'derived: threshold items are low when quantity <= 阈值; cycle items are low '
                 'when days since last purchase >= the interval. Each item also reports '
                 'days_since_purchase. DO NOT estimate from memory.',
  'input_schema': {'type': 'object',
                   'properties': {'low_only': {'type': 'boolean',
                                               'description': 'If true, return only items '
                                                              'flagged low (the restock '
                                                              'list).'},
                                  'status_filter': {'type': 'string',
                                                    'description': 'Inventory status to filter '
                                                                   "by; default 'active'."}},
                   'required': []}},
 {'name': 'amend_purchase',
  'description': 'Correct a NON-quantity field on already-recorded 花销 明细 rows (e.g. wrong '
                 'store, wrong date, a price that was occluded on the receipt). First call '
                 'find_purchase to locate the rows, show the user exactly what will change '
                 '(which rows, old→new), get confirmation, THEN call this. Editing unit_price '
                 'also fixes 小计 (= 数量 × 单价); editing subtotal fixes 单价. To change quantity '
                 'use set_purchase_quantity; to delete a line use void_purchase.',
  'input_schema': {'type': 'object',
                   'properties': {'rows': {'type': 'array',
                                           'items': {'type': 'integer'},
                                           'description': 'Sheet row numbers from find_purchase '
                                                          'to amend (all get the same change).'},
                                  'field': {'type': 'string',
                                            'enum': ['store', 'date', 'category', 'unit',
                                                     'notes', 'item', 'unit_price', 'subtotal'],
                                            'description': 'Which field to set on every row.'},
                                  'value': {'type': ['string', 'number'],
                                            'description': 'New value: string for '
                                                           'store/date/category/unit/notes/item, '
                                                           'number for unit_price/subtotal.'}},
                   'required': ['rows', 'field', 'value']}},
 {'name': 'set_purchase_quantity',
  'description': 'Correct the quantity of a single 花销 明细 row (row number from find_purchase). '
                 '小计 recomputes, and any tracked inventory item this line replenished is '
                 'adjusted by the difference (new − old). Locate with find_purchase and confirm '
                 'before calling. For non-quantity fields use amend_purchase.',
  'input_schema': {'type': 'object',
                   'properties': {'row': {'type': 'integer',
                                          'description': 'Sheet row number from find_purchase.'},
                                  'quantity': {'type': 'number',
                                               'description': 'The corrected quantity.'}},
                   'required': ['row', 'quantity']}},
 {'name': 'void_purchase',
  'description': 'Delete one or more 花销 明细 rows recorded by mistake (row numbers from '
                 'find_purchase) and reverse their inventory effect — subtract the quantities, '
                 'and roll 上次购买日/上次单价 back to the most recent remaining purchase (cleared if '
                 'none remain). DESTRUCTIVE: always locate with find_purchase and get explicit '
                 'confirmation first. To fix a value instead of deleting, use amend_purchase.',
  'input_schema': {'type': 'object',
                   'properties': {'rows': {'type': 'array',
                                           'items': {'type': 'integer'},
                                           'description': 'Sheet row numbers from find_purchase '
                                                          'to delete.'}},
                   'required': ['rows']}}]
