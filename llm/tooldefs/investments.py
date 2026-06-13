"""Fund 定投 recurring-investment tools (Google Sheet).

Tool schemas + dispatch handlers for the investments domain.
"""
from tools.investments import (
    add_investment_plan,
    find_investment,
    investment_summary,
    list_investment_plans,
    record_investment,
    update_investment_confirmation,
    update_plan_status,
)

HANDLERS = {
    "add_investment_plan": lambda a: add_investment_plan(
        fund=a["fund"], frequency=a["frequency"], planned_amount=a["planned_amount"],
        start_date=a["start_date"], day_of_month=a.get("day_of_month"),
        day_of_week=a.get("day_of_week"), notes=a.get("notes"),
    ),
    "list_investment_plans": lambda a: list_investment_plans(status_filter=a.get("status_filter", "active")),
    "update_plan_status": lambda a: update_plan_status(fund=a["fund"], status=a["status"]),
    "record_investment": lambda a: record_investment(
        debit_date=a["debit_date"], fund=a["fund"], planned_amount=a["planned_amount"],
        actual_amount=a["actual_amount"], confirm_date=a.get("confirm_date"),
        shares=a.get("shares"), status=a.get("status"), notes=a.get("notes"),
    ),
    "find_investment": lambda a: find_investment(
        debit_date=a.get("debit_date"), fund=a.get("fund"), pending_only=a.get("pending_only", False),
    ),
    "update_investment_confirmation": lambda a: update_investment_confirmation(
        row=a["row"], confirm_date=a["confirm_date"], shares=a["shares"],
    ),
    "investment_summary": lambda a: investment_summary(fund=a.get("fund"), year=a.get("year")),
}


SCHEMAS = [{'name': 'add_investment_plan',
  'description': "Add a new 基金定投 plan to the 计划 tab. Plan status defaults to 'active'. This "
                 'records the SCHEDULE, not an actual investment — actual debits go through '
                 'record_investment. frequency picks which schedule field is required: '
                 "'monthly' needs day_of_month (1-31), 'weekly' needs day_of_week (1-7 ISO; "
                 "1=Mon, 4=Thu, 7=Sun), 'irregular' needs neither (no auto-reminder will fire "
                 '— user will manually record debits when they happen). Examples: '
                 "'加一条定投：易方达蓝筹混合 每月 10 号 500，6月1号起' → frequency='monthly', day_of_month=10. "
                 "'富国全球科技 每周四扣 1000' → frequency='weekly', day_of_week=4. '思远定投全球好资产 不定期，每次 "
                 "2500' → frequency='irregular'.",
  'input_schema': {'type': 'object',
                   'properties': {'fund': {'type': 'string',
                                           'description': 'Fund name as the user gave it (e.g. '
                                                          "'易方达蓝筹混合')."},
                                  'frequency': {'type': 'string',
                                                'enum': ['monthly', 'weekly', 'irregular'],
                                                'description': 'Debit frequency. Pick by what '
                                                               'the user describes: a specific '
                                                               '每月 X 号 → monthly; 每周 X → '
                                                               'weekly; 不定期/不固定/有信号才扣 → '
                                                               'irregular.'},
                                  'planned_amount': {'type': 'number',
                                                     'description': 'Planned debit amount in '
                                                                    'RMB per debit.'},
                                  'start_date': {'type': 'string',
                                                 'description': 'Plan start date in YYYY-MM-DD '
                                                                'format.'},
                                  'day_of_month': {'type': 'integer',
                                                   'description': 'Day of month the bank '
                                                                  'debits, 1-31. Required ONLY '
                                                                  "when frequency='monthly'."},
                                  'day_of_week': {'type': 'integer',
                                                  'description': 'ISO weekday the bank debits: '
                                                                 '1=周一, 2=周二, 3=周三, 4=周四, '
                                                                 '5=周五, 6=周六, 7=周日. Required '
                                                                 'ONLY when '
                                                                 "frequency='weekly'. Convert "
                                                                 "from the user's wording "
                                                                 "(e.g. '每周四' → 4)."},
                                  'notes': {'type': 'string',
                                            'description': 'Optional free-form notes.'}},
                   'required': ['fund', 'frequency', 'planned_amount', 'start_date']}},
 {'name': 'list_investment_plans',
  'description': "List 定投 plans. Use this to (a) answer 'what plans do I have', (b) match a "
                 'fund name from a bank text against active plans before calling '
                 'record_investment, (c) get planned_amount when the user forwards a debit '
                 'text. Default returns only active plans; pass status_filter=null to include '
                 'paused/ended too.',
  'input_schema': {'type': 'object',
                   'properties': {'status_filter': {'type': 'string',
                                                    'enum': ['active', 'paused', 'ended'],
                                                    'description': 'Filter by plan status. '
                                                                   'Omit (or null in code) to '
                                                                   'return all plans.'}},
                   'required': []}},
 {'name': 'update_plan_status',
  'description': "Change a plan's status. Use when the user says '暂停 X 的定投' (status='paused'), "
                 "'继续 X' (status='active'), or '停掉 X' (status='ended'). Matches by exact fund "
                 "name — call list_investment_plans first if you're not sure of the exact "
                 'stored name.',
  'input_schema': {'type': 'object',
                   'properties': {'fund': {'type': 'string',
                                           'description': 'Exact fund name as stored in 计划.'},
                                  'status': {'type': 'string',
                                             'enum': ['active', 'paused', 'ended'],
                                             'description': 'New plan status.'}},
                   'required': ['fund', 'status']}},
 {'name': 'record_investment',
  'description': 'Upsert a debit event into the 流水 tab, keyed by (debit_date, fund). If a row '
                 'already exists for that combination it is updated in place; non-None args '
                 'overwrite, omitted args preserve existing values. Otherwise a new row is '
                 'inserted. Use this whenever the user forwards a bank text — DO NOT call '
                 'find_investment first to dedup, the tool handles it. Workflow: (1) call '
                 'list_investment_plans to map the fund mentioned in the text to its exact '
                 'stored name and get planned_amount; (2) call record_investment with whatever '
                 "fields the text contained — debit-only texts pass A-D and get status '已扣款'; "
                 "consolidated texts pass A-F and get status '已确认'; a "
                 'confirmation-for-an-earlier-debit text also passes A-F and the upsert lands '
                 "on the existing pending row, bumping it to '已确认'. The result includes "
                 "operation='inserted' or 'updated' so you can phrase the reply correctly.",
  'input_schema': {'type': 'object',
                   'properties': {'debit_date': {'type': 'string',
                                                 'description': 'Debit date in YYYY-MM-DD '
                                                                'format (when the bank pulled '
                                                                'the money).'},
                                  'fund': {'type': 'string',
                                           'description': 'Fund name. Should match a plan name '
                                                          '— call list_investment_plans first '
                                                          'if unsure.'},
                                  'planned_amount': {'type': 'number',
                                                     'description': 'Planned debit amount in '
                                                                    'RMB (copy from the '
                                                                    'matching plan).'},
                                  'actual_amount': {'type': 'number',
                                                    'description': 'Actual debited amount in '
                                                                   'RMB (from the bank text).'},
                                  'confirm_date': {'type': 'string',
                                                   'description': 'Share-confirmation date in '
                                                                  'YYYY-MM-DD format. Omit if '
                                                                  'not yet known.'},
                                  'shares': {'type': 'number',
                                             'description': 'Confirmed shares (份额). Omit if '
                                                            'not yet known.'},
                                  'status': {'type': 'string',
                                             'enum': ['已扣款', '已确认', '已跳过', '失败'],
                                             'description': 'Override status. Usually omitted '
                                                            "— defaults to '已确认' if "
                                                            'confirm_date+shares are present, '
                                                            "otherwise '已扣款'."},
                                  'notes': {'type': 'string',
                                            'description': 'Optional free-form notes.'}},
                   'required': ['debit_date', 'fund', 'planned_amount', 'actual_amount']}},
 {'name': 'find_investment',
  'description': 'Search the 流水 tab. Use to (a) locate the row to update when the user '
                 'forwards a share-confirmation text for a previously-recorded debit (use '
                 "pending_only=true), (b) verify a debit isn't already recorded before "
                 'inserting. fund is substring match (case-insensitive); debit_date is exact.',
  'input_schema': {'type': 'object',
                   'properties': {'debit_date': {'type': 'string',
                                                 'description': 'Exact match on 扣款日期 '
                                                                '(YYYY-MM-DD).'},
                                  'fund': {'type': 'string',
                                           'description': 'Substring of fund name '
                                                          '(case-insensitive).'},
                                  'pending_only': {'type': 'boolean',
                                                   'description': 'If true, return only rows '
                                                                  "with status='已扣款' (awaiting "
                                                                  'share confirmation).'}},
                   'required': []}},
 {'name': 'update_investment_confirmation',
  'description': "Fill in 确认日期 and 确认份额 on an existing 流水 row, and set status='已确认'. Use when "
                 'the user forwards a share-confirmation text for a debit already recorded as '
                 "'已扣款'. Find the row first with find_investment(pending_only=true), then pass "
                 'its row number here.',
  'input_schema': {'type': 'object',
                   'properties': {'row': {'type': 'integer',
                                          'description': '1-based row number from '
                                                         'find_investment.'},
                                  'confirm_date': {'type': 'string',
                                                   'description': 'Share-confirmation date in '
                                                                  'YYYY-MM-DD format.'},
                                  'shares': {'type': 'number',
                                             'description': 'Confirmed shares (份额).'}},
                   'required': ['row', 'confirm_date', 'shares']}},
 {'name': 'investment_summary',
  'description': "Aggregate totals over the 流水 tab. Use to answer '累计投了多少', '今年定投花了多少', 'X "
                 "基金投了多少'. Returns total_debited_rmb, total_shares_confirmed (only counts rows "
                 "with status='已确认'), rows_count, pending_confirmations_count (rows still "
                 'awaiting 份额), and a by_fund breakdown. Skipped/failed rows are excluded from '
                 'totals. DO NOT estimate from memory — always call this tool.',
  'input_schema': {'type': 'object',
                   'properties': {'fund': {'type': 'string',
                                           'description': 'Optional exact fund name filter.'},
                                  'year': {'type': 'integer',
                                           'description': 'Optional year filter (matches YYYY '
                                                          'prefix of 扣款日期).'}},
                   'required': []}}]
