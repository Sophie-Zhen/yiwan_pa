"""International transhipment parcel tools (Google Sheet).

Tool schemas + dispatch handlers for the parcels domain.
"""
from tools.parcels import (
    apply_exchange_rate,
    find_parcel,
    parcel_summary,
    record_parcel,
    settle_shipping,
    update_parcel,
    update_parcels_by_tracking,
)

HANDLERS = {
    "record_parcel": lambda a: record_parcel(
        date=a["date"], item=a["item"], platform=a["platform"], quantity=a["quantity"],
        unit_price=a.get("unit_price"), total_price=a.get("total_price"),
        tracking_no=a.get("tracking_no"), weight_kg=a.get("weight_kg"), notes=a.get("notes"),
    ),
    "find_parcel": lambda a: find_parcel(a["query"]),
    "update_parcel": lambda a: update_parcel(
        row=a["row"], status=a.get("status"), tracking_no=a.get("tracking_no"),
        weight_kg=a.get("weight_kg"), notes=a.get("notes"),
    ),
    "parcel_summary": lambda a: parcel_summary(),
    "update_parcels_by_tracking": lambda a: update_parcels_by_tracking(
        tracking_no=a["tracking_no"], status=a.get("status"),
        total_weight_kg=a.get("total_weight_kg"), notes=a.get("notes"),
    ),
    "settle_shipping": lambda a: settle_shipping(
        total_billed_weight_kg=a["total_billed_weight_kg"],
        total_shipping_rmb=a["total_shipping_rmb"],
    ),
    "apply_exchange_rate": lambda a: apply_exchange_rate(a["rate"]),
}


SCHEMAS = [{'name': 'record_parcel',
  'description': 'Append a new parcel to the active transhipment tab (Stage 1: capture). Use '
                 "this when the user describes a NEW online order they just placed — e.g. '今天 "
                 "pdd 上买了 4 包桥头火锅底料 每包 18.8', '在 1688 下单了 1 个门锁 112 块'. Extract date, item "
                 'name, platform, quantity, and any provided price. Provide AT LEAST ONE of '
                 'unit_price or total_price — if user gave only quantity + unit price, pass '
                 'unit_price; if user gave only quantity + total, pass total_price; if both, '
                 'pass both. The sheet will keep the trio (qty, unit, total) consistent via '
                 "formula. Status defaults to '未发货'; 转运渠道 is inferred from the active tab "
                 'name. DO NOT use this for ordinary todos — for todos use append_to_inbox.',
  'input_schema': {'type': 'object',
                   'properties': {'date': {'type': 'string',
                                           'description': 'Purchase date in YYYY-MM-DD '
                                                          'format.'},
                                  'item': {'type': 'string',
                                           'description': 'Item name as the user described '
                                                          'it.'},
                                  'platform': {'type': 'string',
                                               'description': 'Purchase platform (e.g. pdd, '
                                                              '1688, 京东, tb).'},
                                  'quantity': {'type': 'integer',
                                               'description': 'Quantity purchased.'},
                                  'unit_price': {'type': 'number',
                                                 'description': 'Per-unit price in RMB. Omit '
                                                                'if the user only gave '
                                                                'total_price.'},
                                  'total_price': {'type': 'number',
                                                  'description': 'Total price in RMB. Omit if '
                                                                 'the user only gave '
                                                                 'unit_price.'},
                                  'tracking_no': {'type': 'string',
                                                  'description': '国内快递单号. Usually unknown at '
                                                                 'order time — omit unless '
                                                                 'explicitly provided.'},
                                  'weight_kg': {'type': 'number',
                                                'description': '国内包裹重量 in kg. Usually unknown '
                                                               'at order time — omit unless '
                                                               'explicitly provided.'},
                                  'notes': {'type': 'string',
                                            'description': 'Optional free-form notes.'}},
                   'required': ['date', 'item', 'platform', 'quantity']}},
 {'name': 'find_parcel',
  'description': 'Search the active parcel tab for rows whose 商品名称 or 国内快递单号 contains the '
                 "query substring. Used to resolve user references like '火锅底料', '8888', '9303 "
                 "那个快递'. Returns up to N matches with row number, item, tracking_no, status. "
                 'When multiple matches come back, ask the user to disambiguate — DO NOT '
                 'guess.',
  'input_schema': {'type': 'object',
                   'properties': {'query': {'type': 'string',
                                            'description': 'Substring to match against 商品名称 or '
                                                           '国内快递单号.'}},
                   'required': ['query']}},
 {'name': 'update_parcel',
  'description': 'Update fields on a specific parcel row found via find_parcel. The row '
                 'argument is the 1-based sheet row number. Status enum (map natural language '
                 "to these): '未发货' (default after record), '在途' (user says '发货了'/'已发'), '已签收' "
                 "(user says '签收了'/'到货了'/'拿到了'), '已入库拍照' (user says '入库了'/'拍照了'/'入库拍照了'). When "
                 "user reports both status and weight in one message ('入库拍照了 1kg'), pass both "
                 'in one call. AUTO-COUPLING: if weight_kg is set but status is omitted, the '
                 "tool sets status='已入库拍照' automatically (filling a weight = "
                 'warehouse-weighing event). Pass status explicitly only if you want a value '
                 'other than 已入库拍照.',
  'input_schema': {'type': 'object',
                   'properties': {'row': {'type': 'integer',
                                          'description': '1-based row number returned by '
                                                         'find_parcel.'},
                                  'status': {'type': 'string',
                                             'enum': ['未发货', '在途', '已签收', '已入库拍照'],
                                             'description': 'New 快递状态. Map from natural '
                                                            'language as above.'},
                                  'tracking_no': {'type': 'string',
                                                  'description': '国内快递单号 if user just provided '
                                                                 'it.'},
                                  'weight_kg': {'type': 'number',
                                                'description': '国内包裹重量 in kg if user just '
                                                               'provided it.'},
                                  'notes': {'type': 'string',
                                            'description': 'Free-form notes to overwrite the '
                                                           '备注 column.'}},
                   'required': ['row']}},
 {'name': 'parcel_summary',
  'description': 'Aggregate totals over the active parcel tab. Use this to answer questions '
                 "about the batch state — '总重量', '现在多少包裹', '能不能申请打包了', '有几个还没入库'. Returns "
                 'row_count (number of SKU rows), distinct_tracking_count (number of physical '
                 'parcels — multi-SKU rows sharing a tracking_no count as one), '
                 'total_weight_kg (sum of 国内包裹重量 where filled), rows_with_weight (how many '
                 'rows contribute to that sum), and status_counts (count per status value). '
                 'When reporting back to the user: mention BOTH the SKU row count and the '
                 'distinct tracking count (they may differ due to multi-SKU per parcel), and '
                 'flag if rows_with_weight < row_count (some parcels still unweighed). DO NOT '
                 'estimate totals from conversation memory — always call this tool.',
  'input_schema': {'type': 'object', 'properties': {}, 'required': []}},
 {'name': 'update_parcels_by_tracking',
  'description': 'Update ALL parcel rows sharing the same 国内快递单号 (one physical parcel often '
                 'contains multiple SKUs / multiple sheet rows). Use this when the user '
                 "reports status / weight for a tracking number — e.g. '9303 入库拍照 1.5kg' or "
                 "'9303 签收了'. Status and notes apply uniformly to every matched row. "
                 'total_weight_kg is the carrier-reported weight for the WHOLE parcel and is '
                 'SPLIT EQUALLY across matched rows (e.g. 1.5kg over 2 rows → 0.75kg each). '
                 "Always confirm the split back to the user ('1.5kg 平分到 2 件，各 0.75kg'). "
                 'AUTO-COUPLING: if total_weight_kg is set but status is omitted, the tool '
                 "sets status='已入库拍照' automatically — pass status explicitly only if you want "
                 'a different value. For NON-equal splits, do NOT call this tool — instead, '
                 "parse the user's stated ratio/literal weights yourself and make one "
                 'update_parcel call per row with the computed per-row weight.',
  'input_schema': {'type': 'object',
                   'properties': {'tracking_no': {'type': 'string',
                                                  'description': '国内快递单号 to match. Substring '
                                                                 'match against the 国内快递单号 '
                                                                 'column — users typically '
                                                                 'refer to the last 4 digits.'},
                                  'status': {'type': 'string',
                                             'enum': ['未发货', '在途', '已签收', '已入库拍照'],
                                             'description': 'New 快递状态 for all matched rows.'},
                                  'total_weight_kg': {'type': 'number',
                                                      'description': 'Total parcel weight '
                                                                     'reported by the carrier. '
                                                                     'Split equally across '
                                                                     'matched rows.'},
                                  'notes': {'type': 'string',
                                            'description': 'Notes to write into 备注 on every '
                                                           'matched row.'}},
                   'required': ['tracking_no']}},
 {'name': 'settle_shipping',
  'description': 'Stage 2 — call this when the user reports the carrier-consolidated totals: '
                 'total billing weight (kg) and total shipping cost (RMB). Triggered by '
                 "messages like '总计费重量 26kg, 运费 750', '结算: 20kg / 700元', '这批 25 公斤 800 块'. "
                 'Adds a summary row at the bottom of the active tab and writes apportioning '
                 'formulas to all data rows. Only call once per batch.',
  'input_schema': {'type': 'object',
                   'properties': {'total_billed_weight_kg': {'type': 'number',
                                                             'description': 'Total billing '
                                                                            'weight in kg as '
                                                                            'reported by the '
                                                                            'carrier.'},
                                  'total_shipping_rmb': {'type': 'number',
                                                         'description': 'Total shipping fee in '
                                                                        'RMB as reported by '
                                                                        'the carrier.'}},
                   'required': ['total_billed_weight_kg', 'total_shipping_rmb']}},
 {'name': 'apply_exchange_rate',
  'description': 'Stage 3 — call this when the user provides the RMB/EUR exchange rate for '
                 "this batch ('汇率 7.8', 'rate 7.85'). Writes the literal rate + EUR-conversion "
                 'formulas to every data row, AND writes the rate + total-EUR formula to the '
                 "summary row (so the user sees the batch's total EUR cost at the bottom). "
                 'Typically called after settle_shipping but can also be called independently.',
  'input_schema': {'type': 'object',
                   'properties': {'rate': {'type': 'number',
                                           'description': 'RMB-per-EUR exchange rate.'}},
                   'required': ['rate']}}]
