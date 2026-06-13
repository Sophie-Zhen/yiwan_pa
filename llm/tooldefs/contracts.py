"""Annual contract renewal tools (data/contracts.md).

Tool schemas + dispatch handlers for the contracts domain.
"""
from tools.contracts import add_contract, list_contracts, renew_contract, update_contract

HANDLERS = {
    "add_contract": lambda a: add_contract(
        name=a["name"], contract_type=a["contract_type"], expiry=a["expiry"],
        remind_on=a.get("remind_on"), current_price=a.get("current_price"), notes=a.get("notes"),
    ),
    "list_contracts": lambda a: list_contracts(status_filter=a.get("status_filter", "active")),
    "renew_contract": lambda a: renew_contract(
        name=a["name"], new_expiry=a["new_expiry"], new_current_price=a["new_current_price"],
        new_remind_on=a.get("new_remind_on"), notes=a.get("notes"),
    ),
    "update_contract": lambda a: update_contract(
        name=a["name"], field=a["field"], value=a.get("value"),
    ),
}


SCHEMAS = [{'name': 'add_contract',
  'description': 'Add an annual contract to track for renewal (energy, broadband, home/car '
                 "insurance). Use when Sophie wants to remember a contract's expiry and be "
                 "reminded to shop around / switch — e.g. '记一下能源合同 7月2号到期', 'add my car "
                 "insurance, renews 2027-06-15'. remind_on defaults to the expiry date; set it "
                 'earlier for lead time to compare prices. current_price is free-form text '
                 "(e.g. '0.42/kWh + €260 standing', '€540/year') so it can hold whatever the "
                 'bill actually states.',
  'input_schema': {'type': 'object',
                   'properties': {'name': {'type': 'string',
                                           'description': 'Contract name incl. provider, e.g. '
                                                          "'Electric Ireland 电费', 'AXA car "
                                                          "insurance'."},
                                  'contract_type': {'type': 'string',
                                                    'enum': ['energy',
                                                             'broadband',
                                                             'home_insurance',
                                                             'car_insurance',
                                                             'other']},
                                  'expiry': {'type': 'string',
                                             'description': 'Expiry date YYYY-MM-DD.'},
                                  'remind_on': {'type': 'string',
                                                'description': 'Date to remind, YYYY-MM-DD. '
                                                               'Defaults to expiry; set '
                                                               'earlier for lead time.'},
                                  'current_price': {'type': 'string',
                                                    'description': 'What she pays now, '
                                                                   'free-form (annual premium, '
                                                                   'unit rate, etc.).'},
                                  'notes': {'type': 'string', 'description': 'Optional note.'}},
                   'required': ['name', 'contract_type', 'expiry']}},
 {'name': 'list_contracts',
  'description': 'List tracked contracts (default active), each with days_until_expiry. Use '
                 "for '我有哪些合同', '什么快到期了', 'X 合同什么时候到期 / 现在多少钱'. DO NOT estimate from memory.",
  'input_schema': {'type': 'object',
                   'properties': {'status_filter': {'type': 'string',
                                                    'description': 'Status to filter by; '
                                                                   "default 'active'. Pass "
                                                                   'null for all.'}},
                   'required': []}},
 {'name': 'renew_contract',
  'description': 'Record that a contract was renewed: rotates the old price into prev_price '
                 '(for year-over-year comparison), sets the new price and expiry, rolls the '
                 "reminder forward, and re-arms next year's reminder. Use for '车险续约了，新到期 "
                 "2027-06-15，今年 €560', 'I renewed energy, now 0.39/kWh'. If new_remind_on is "
                 'omitted it keeps the same lead time relative to expiry as before.',
  'input_schema': {'type': 'object',
                   'properties': {'name': {'type': 'string',
                                           'description': 'Substring of the contract name.'},
                                  'new_expiry': {'type': 'string',
                                                 'description': 'New expiry date YYYY-MM-DD.'},
                                  'new_current_price': {'type': 'string',
                                                        'description': 'The new price, '
                                                                       'free-form.'},
                                  'new_remind_on': {'type': 'string',
                                                    'description': 'Optional new reminder date '
                                                                   'YYYY-MM-DD; defaults to '
                                                                   'preserving the old lead '
                                                                   'time.'},
                                  'notes': {'type': 'string',
                                            'description': 'Optional updated note.'}},
                   'required': ['name', 'new_expiry', 'new_current_price']}},
 {'name': 'update_contract',
  'description': 'Edit one field on a contract (type, expiry, remind_on, current_price, '
                 'prev_price, status, notes). Use for corrections or to archive a contract '
                 "('don't track the broadband one anymore' → status=archived). For a renewal "
                 'use renew_contract instead, which also rotates the price history.',
  'input_schema': {'type': 'object',
                   'properties': {'name': {'type': 'string',
                                           'description': 'Substring of the contract name.'},
                                  'field': {'type': 'string',
                                            'enum': ['type',
                                                     'expiry',
                                                     'remind_on',
                                                     'current_price',
                                                     'prev_price',
                                                     'status',
                                                     'notes']},
                                  'value': {'type': 'string',
                                            'description': 'New value (empty to clear).'}},
                   'required': ['name', 'field', 'value']}}]
