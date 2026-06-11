"""AnthropicBackend — direct anthropic SDK with a self-written agent loop.

In contrast to ClaudeCodeBackend (which delegates the agent loop to the local
`claude` CLI), this backend implements the loop itself: define tool schemas,
call the Messages API, inspect tool_use blocks, execute the corresponding
Python function, send the result back, repeat until the model is done.

This is the minimal harness — what Claude Code does at scale, in ~150 lines.

Each call is stateless: no conversation history is kept across invocations.
Persistent state lives in data/inbox.md and data/archive.md, manipulated via
the storage.markdown helpers.

Notes on configuration:
- Model defaults to Opus 4.7. Switch to `claude-sonnet-4-6` for ~3x lower
  cost on simple workloads if Opus feels excessive.
- Top-level `cache_control={"type": "ephemeral"}` auto-caches the last
  cacheable block. With render order tools → system → messages, this caches
  tools + system together. Subsequent loop turns within the same chat()
  call (and chats within ~5 minutes) read from cache instead of paying full
  input price for the prefix.
- `thinking={"type": "adaptive"}` lets the model decide when extra reasoning
  helps. Off by default on Opus 4.7; turning it on gives headroom for harder
  intents without forcing thinking on simple ones.
"""
import base64
import json
import logging
from datetime import datetime
from typing import Any, Optional

import anthropic

from storage.markdown import (
    TERMINAL_STATUSES,
    Item,
    append_to_inbox,
    append_to_notes,
    find_item,
    move_to_archive,
    read_archive,
    read_inbox,
    set_item_status,
    skip_remaining_alerts,
    update_inbox_item,
)
from tools.investments import (
    add_investment_plan,
    find_investment,
    investment_summary,
    list_investment_plans,
    record_investment,
    update_investment_confirmation,
    update_plan_status,
)
from tools.expenses import (
    adjust_inventory,
    find_purchase,
    list_inventory,
    price_history,
    record_purchase,
    top_items,
    track_item,
)
from tools.parcels import (
    apply_exchange_rate,
    find_parcel,
    parcel_summary,
    record_parcel,
    settle_shipping,
    update_parcel,
    update_parcels_by_tracking,
)

from .base import LLMBackend

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
# Vision calls swap to Sonnet 4.6 — ~5x cheaper input price ($3/M vs $15/M)
# with no quality loss observed on parcel screenshots in scripts/spike_vision.py.
# Image tokens (~1700 per phone screenshot) don't cache, so Opus pricing on
# them would noticeably bump the bill at the user's expected ~40 screenshots
# per shipment batch.
VISION_MODEL = "claude-sonnet-4-6"
# 16000 is the Anthropic-recommended default for non-streaming. It's a *cap*,
# not a target — short replies still cost only the tokens they actually use.
# Lowballing this (e.g. 1024) truncates batch operations: a single user message
# capturing N items emits N parallel tool_use blocks plus adaptive thinking,
# which easily exceeds 1024. Above ~16k, switch to streaming to avoid SDK
# HTTP timeouts.
MAX_TOKENS = 16000
MAX_LOOP_TURNS = 10  # safety bound — if exceeded, something is wrong


# Tool schemas — what the model "sees" as available capabilities. Anything not
# listed here, the model cannot call (the harness wouldn't know how to dispatch
# it anyway). Order matters for prompt caching: keep this list stable so the
# cached prefix stays valid.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_inbox",
        "description": "Read all pending todo items currently in the inbox. Returns a list with title, status, due, tags, and notes for each item.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_archive",
        "description": "Read all completed or cancelled todo items in the archive.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "append_to_inbox",
        "description": "Add a new pending item to the top of the inbox. Use this for: (a) a standalone task — leave type/mode/project unset; (b) a multi-step project — set type='project' and mode ('sequential' = steps must be done in order, 'parallel' = any order); (c) a step belonging to an existing project — set project=<that project's title>. Steps of a project should be appended in the order they will be executed (the storage order is the execution order).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title summarising the item.",
                },
                "type": {
                    "type": "string",
                    "enum": ["project"],
                    "description": "Set to 'project' when creating a multi-step project record. Omit for ordinary todos and for steps belonging to a project.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["sequential", "parallel"],
                    "description": "Required when type='project'. 'sequential' = steps must progress in order (only one in_progress at a time). 'parallel' = steps may proceed in any order.",
                },
                "project": {
                    "type": "string",
                    "description": "When this item is a step of a project, set this to the parent project's title. Do not set when creating the project itself or a standalone item.",
                },
                "due": {
                    "type": "string",
                    "description": "Optional due date or datetime in YYYY-MM-DD or YYYY-MM-DD HH:MM format.",
                },
                "tags": {
                    "type": "string",
                    "description": "Optional space-separated #category or #category/sub tags.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional free-form context.",
                },
                "alerts": {
                    "type": "string",
                    "description": "Optional comma-separated list of minutes-before-due offsets at which the user wants push reminders (e.g. '180,120' for T-3h and T-2h, '30' for T-30min, '0' for a push at the due time itself). Only meaningful for items whose due has HH:MM precision. Set ONLY when the user explicitly requests reminders ('提前 30 分钟', 'T-1h', '就 3 点提醒', 'remind me at the time'). Do NOT set this by default — items with no `alerts` simply don't trigger T-N pushes; they still appear in the morning / evening digest. Translate hours to minutes (3h -> 180). Use '0' when the user wants a push exactly at the due moment ('就三点'/'到点提醒'/'remind me at X sharp'). For ambiguous phrasing ('提醒我' with no offset specified), ask the user how far in advance.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_inbox_item",
        "description": "Update one non-status field of an existing inbox item, REPLACING the field's current value. The item is matched by the first whose title contains title_substring (case-insensitive). Use this for modifications such as changing the due date, title, tags, or rewriting the notes from scratch. For status changes use the set_status tool — this tool will refuse status updates. IMPORTANT — notes is overwrite-only: if the user wants to ADD to existing notes ('再加一项', '再补一条', 'append') rather than replace them, call append_to_notes instead; using this tool with field=notes would silently discard the existing notes value.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {
                    "type": "string",
                    "description": "Substring of the target item's title (case-insensitive).",
                },
                "field": {
                    "type": "string",
                    "enum": ["title", "due", "tags", "notes", "alerts"],
                    "description": "Which field to update. Status is intentionally excluded — use set_status. For alerts: value is a comma-separated minute-offset list (same format as at capture, e.g. '60' or '180,120' or '0'); updating alerts also resets the item's fired-history so the new declaration takes effect cleanly.",
                },
                "value": {
                    "type": "string",
                    "description": "New value for the field. REPLACES the existing value — does not append.",
                },
            },
            "required": ["title_substring", "field", "value"],
        },
    },
    {
        "name": "append_to_notes",
        "description": "Append a line to an inbox item's notes WITHOUT overwriting the existing content. Use this whenever the user wants to add to / extend / supplement existing notes ('再加一项 X', '再补一条 Y', 'also note Z', 'append'). If the item already has notes, the new value is joined with '; '; if notes was empty, value becomes the new notes. Use update_inbox_item with field=notes only when the user explicitly says to replace or rewrite the entire notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {
                    "type": "string",
                    "description": "Substring of the target item's title (case-insensitive).",
                },
                "value": {
                    "type": "string",
                    "description": "Text to append. Will be joined with the existing notes by '; ' if notes already has content.",
                },
            },
            "required": ["title_substring", "value"],
        },
    },
    {
        "name": "find_item",
        "description": "Search both inbox AND archive for items whose title contains the given substring (case-insensitive). Use this to verify whether an item exists or to look up its state. Do NOT infer 'item doesn't exist' from read_inbox alone — read_inbox only returns pending items, while completed or cancelled items live in archive. Returns a list of matches, each with location ('inbox' or 'archive') plus the item fields. Empty list means not found in either file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {
                    "type": "string",
                    "description": "Substring of the target item's title (case-insensitive).",
                },
            },
            "required": ["title_substring"],
        },
    },
    {
        "name": "skip_remaining_alerts",
        "description": "Cancel any pending T-N push alerts for an inbox item without losing its declared alerts configuration. Use this when the user replies to a late-alert message with 'skip <item>' / '取消提醒' / 'don't remind me' for that item — the user has already done the thing (or no longer wants the rest of the pre-due pushes). Marks all declared alert offsets as already fired. No effect on status, due, or the alerts declaration itself; only on which offsets count as 'already pushed'. The item is matched by the first whose title contains title_substring (case-insensitive).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {
                    "type": "string",
                    "description": "Substring of the target item's title (case-insensitive).",
                },
            },
            "required": ["title_substring"],
        },
    },
    {
        "name": "set_status",
        "description": "Change an inbox item's status. This is the only tool that may change status. Allowed values: 'pending' (not yet started), 'in_progress' (currently being worked on), 'done' (completed), 'cancelled' (abandoned). Behaviour: (a) terminal statuses (done / cancelled) also move the item to archive.md — no separate archive call needed; (b) when transitioning a step to in_progress, if its parent project's mode is 'sequential', the tool refuses if another step in the same project is already in_progress (only one step at a time in a sequential project). When that happens, ask the user whether to finish or pause the blocking step first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title_substring": {
                    "type": "string",
                    "description": "Substring of the target item's title (case-insensitive).",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "cancelled"],
                    "description": "New status for the item.",
                },
            },
            "required": ["title_substring", "status"],
        },
    },
    # === Transhipment parcel tools (separate workflow from todos) ===
    # These write to a Google Sheet, NOT to data/inbox.md. Use them when the
    # user is talking about ordering / receiving / consolidating parcels for
    # international shipping, not for ordinary todos.
    {
        "name": "record_parcel",
        "description": "Append a new parcel to the active transhipment tab (Stage 1: capture). Use this when the user describes a NEW online order they just placed — e.g. '今天 pdd 上买了 4 包桥头火锅底料 每包 18.8', '在 1688 下单了 1 个门锁 112 块'. Extract date, item name, platform, quantity, and any provided price. Provide AT LEAST ONE of unit_price or total_price — if user gave only quantity + unit price, pass unit_price; if user gave only quantity + total, pass total_price; if both, pass both. The sheet will keep the trio (qty, unit, total) consistent via formula. Status defaults to '未发货'; 转运渠道 is inferred from the active tab name. DO NOT use this for ordinary todos — for todos use append_to_inbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Purchase date in YYYY-MM-DD format.",
                },
                "item": {
                    "type": "string",
                    "description": "Item name as the user described it.",
                },
                "platform": {
                    "type": "string",
                    "description": "Purchase platform (e.g. pdd, 1688, 京东, tb).",
                },
                "quantity": {
                    "type": "integer",
                    "description": "Quantity purchased.",
                },
                "unit_price": {
                    "type": "number",
                    "description": "Per-unit price in RMB. Omit if the user only gave total_price.",
                },
                "total_price": {
                    "type": "number",
                    "description": "Total price in RMB. Omit if the user only gave unit_price.",
                },
                "tracking_no": {
                    "type": "string",
                    "description": "国内快递单号. Usually unknown at order time — omit unless explicitly provided.",
                },
                "weight_kg": {
                    "type": "number",
                    "description": "国内包裹重量 in kg. Usually unknown at order time — omit unless explicitly provided.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional free-form notes.",
                },
            },
            "required": ["date", "item", "platform", "quantity"],
        },
    },
    {
        "name": "find_parcel",
        "description": "Search the active parcel tab for rows whose 商品名称 or 国内快递单号 contains the query substring. Used to resolve user references like '火锅底料', '8888', '9303 那个快递'. Returns up to N matches with row number, item, tracking_no, status. When multiple matches come back, ask the user to disambiguate — DO NOT guess.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring to match against 商品名称 or 国内快递单号.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "update_parcel",
        "description": "Update fields on a specific parcel row found via find_parcel. The row argument is the 1-based sheet row number. Status enum (map natural language to these): '未发货' (default after record), '在途' (user says '发货了'/'已发'), '已签收' (user says '签收了'/'到货了'/'拿到了'), '已入库拍照' (user says '入库了'/'拍照了'/'入库拍照了'). When user reports both status and weight in one message ('入库拍照了 1kg'), pass both in one call. AUTO-COUPLING: if weight_kg is set but status is omitted, the tool sets status='已入库拍照' automatically (filling a weight = warehouse-weighing event). Pass status explicitly only if you want a value other than 已入库拍照.",
        "input_schema": {
            "type": "object",
            "properties": {
                "row": {
                    "type": "integer",
                    "description": "1-based row number returned by find_parcel.",
                },
                "status": {
                    "type": "string",
                    "enum": ["未发货", "在途", "已签收", "已入库拍照"],
                    "description": "New 快递状态. Map from natural language as above.",
                },
                "tracking_no": {
                    "type": "string",
                    "description": "国内快递单号 if user just provided it.",
                },
                "weight_kg": {
                    "type": "number",
                    "description": "国内包裹重量 in kg if user just provided it.",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-form notes to overwrite the 备注 column.",
                },
            },
            "required": ["row"],
        },
    },
    {
        "name": "parcel_summary",
        "description": "Aggregate totals over the active parcel tab. Use this to answer questions about the batch state — '总重量', '现在多少包裹', '能不能申请打包了', '有几个还没入库'. Returns row_count (number of SKU rows), distinct_tracking_count (number of physical parcels — multi-SKU rows sharing a tracking_no count as one), total_weight_kg (sum of 国内包裹重量 where filled), rows_with_weight (how many rows contribute to that sum), and status_counts (count per status value). When reporting back to the user: mention BOTH the SKU row count and the distinct tracking count (they may differ due to multi-SKU per parcel), and flag if rows_with_weight < row_count (some parcels still unweighed). DO NOT estimate totals from conversation memory — always call this tool.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_parcels_by_tracking",
        "description": "Update ALL parcel rows sharing the same 国内快递单号 (one physical parcel often contains multiple SKUs / multiple sheet rows). Use this when the user reports status / weight for a tracking number — e.g. '9303 入库拍照 1.5kg' or '9303 签收了'. Status and notes apply uniformly to every matched row. total_weight_kg is the carrier-reported weight for the WHOLE parcel and is SPLIT EQUALLY across matched rows (e.g. 1.5kg over 2 rows → 0.75kg each). Always confirm the split back to the user ('1.5kg 平分到 2 件，各 0.75kg'). AUTO-COUPLING: if total_weight_kg is set but status is omitted, the tool sets status='已入库拍照' automatically — pass status explicitly only if you want a different value. For NON-equal splits, do NOT call this tool — instead, parse the user's stated ratio/literal weights yourself and make one update_parcel call per row with the computed per-row weight.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tracking_no": {
                    "type": "string",
                    "description": "国内快递单号 to match. Substring match against the 国内快递单号 column — users typically refer to the last 4 digits.",
                },
                "status": {
                    "type": "string",
                    "enum": ["未发货", "在途", "已签收", "已入库拍照"],
                    "description": "New 快递状态 for all matched rows.",
                },
                "total_weight_kg": {
                    "type": "number",
                    "description": "Total parcel weight reported by the carrier. Split equally across matched rows.",
                },
                "notes": {
                    "type": "string",
                    "description": "Notes to write into 备注 on every matched row.",
                },
            },
            "required": ["tracking_no"],
        },
    },
    {
        "name": "settle_shipping",
        "description": "Stage 2 — call this when the user reports the carrier-consolidated totals: total billing weight (kg) and total shipping cost (RMB). Triggered by messages like '总计费重量 26kg, 运费 750', '结算: 20kg / 700元', '这批 25 公斤 800 块'. Adds a summary row at the bottom of the active tab and writes apportioning formulas to all data rows. Only call once per batch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "total_billed_weight_kg": {
                    "type": "number",
                    "description": "Total billing weight in kg as reported by the carrier.",
                },
                "total_shipping_rmb": {
                    "type": "number",
                    "description": "Total shipping fee in RMB as reported by the carrier.",
                },
            },
            "required": ["total_billed_weight_kg", "total_shipping_rmb"],
        },
    },
    {
        "name": "apply_exchange_rate",
        "description": "Stage 3 — call this when the user provides the RMB/EUR exchange rate for this batch ('汇率 7.8', 'rate 7.85'). Writes the literal rate + EUR-conversion formulas to every data row, AND writes the rate + total-EUR formula to the summary row (so the user sees the batch's total EUR cost at the bottom). Typically called after settle_shipping but can also be called independently.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rate": {
                    "type": "number",
                    "description": "RMB-per-EUR exchange rate.",
                },
            },
            "required": ["rate"],
        },
    },
    # === Fund 定投 (recurring investment) tools ===
    # These write to a SEPARATE Google Sheet (INVESTMENTS_SHEET_ID), not the
    # parcels sheet. Two tabs: 计划 (plans) and 流水 (ledger). Use when the user
    # is talking about 基金定投 — adding a plan, recording a debit/confirmation
    # from a bank text, or asking '累计投了多少'.
    {
        "name": "add_investment_plan",
        "description": "Add a new 基金定投 plan to the 计划 tab. Plan status defaults to 'active'. This records the SCHEDULE, not an actual investment — actual debits go through record_investment. frequency picks which schedule field is required: 'monthly' needs day_of_month (1-31), 'weekly' needs day_of_week (1-7 ISO; 1=Mon, 4=Thu, 7=Sun), 'irregular' needs neither (no auto-reminder will fire — user will manually record debits when they happen). Examples: '加一条定投：易方达蓝筹混合 每月 10 号 500，6月1号起' → frequency='monthly', day_of_month=10. '富国全球科技 每周四扣 1000' → frequency='weekly', day_of_week=4. '思远定投全球好资产 不定期，每次 2500' → frequency='irregular'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fund": {
                    "type": "string",
                    "description": "Fund name as the user gave it (e.g. '易方达蓝筹混合').",
                },
                "frequency": {
                    "type": "string",
                    "enum": ["monthly", "weekly", "irregular"],
                    "description": "Debit frequency. Pick by what the user describes: a specific 每月 X 号 → monthly; 每周 X → weekly; 不定期/不固定/有信号才扣 → irregular.",
                },
                "planned_amount": {
                    "type": "number",
                    "description": "Planned debit amount in RMB per debit.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Plan start date in YYYY-MM-DD format.",
                },
                "day_of_month": {
                    "type": "integer",
                    "description": "Day of month the bank debits, 1-31. Required ONLY when frequency='monthly'.",
                },
                "day_of_week": {
                    "type": "integer",
                    "description": "ISO weekday the bank debits: 1=周一, 2=周二, 3=周三, 4=周四, 5=周五, 6=周六, 7=周日. Required ONLY when frequency='weekly'. Convert from the user's wording (e.g. '每周四' → 4).",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional free-form notes.",
                },
            },
            "required": ["fund", "frequency", "planned_amount", "start_date"],
        },
    },
    {
        "name": "list_investment_plans",
        "description": "List 定投 plans. Use this to (a) answer 'what plans do I have', (b) match a fund name from a bank text against active plans before calling record_investment, (c) get planned_amount when the user forwards a debit text. Default returns only active plans; pass status_filter=null to include paused/ended too.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "enum": ["active", "paused", "ended"],
                    "description": "Filter by plan status. Omit (or null in code) to return all plans.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "update_plan_status",
        "description": "Change a plan's status. Use when the user says '暂停 X 的定投' (status='paused'), '继续 X' (status='active'), or '停掉 X' (status='ended'). Matches by exact fund name — call list_investment_plans first if you're not sure of the exact stored name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fund": {
                    "type": "string",
                    "description": "Exact fund name as stored in 计划.",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "ended"],
                    "description": "New plan status.",
                },
            },
            "required": ["fund", "status"],
        },
    },
    {
        "name": "record_investment",
        "description": "Upsert a debit event into the 流水 tab, keyed by (debit_date, fund). If a row already exists for that combination it is updated in place; non-None args overwrite, omitted args preserve existing values. Otherwise a new row is inserted. Use this whenever the user forwards a bank text — DO NOT call find_investment first to dedup, the tool handles it. Workflow: (1) call list_investment_plans to map the fund mentioned in the text to its exact stored name and get planned_amount; (2) call record_investment with whatever fields the text contained — debit-only texts pass A-D and get status '已扣款'; consolidated texts pass A-F and get status '已确认'; a confirmation-for-an-earlier-debit text also passes A-F and the upsert lands on the existing pending row, bumping it to '已确认'. The result includes operation='inserted' or 'updated' so you can phrase the reply correctly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "debit_date": {
                    "type": "string",
                    "description": "Debit date in YYYY-MM-DD format (when the bank pulled the money).",
                },
                "fund": {
                    "type": "string",
                    "description": "Fund name. Should match a plan name — call list_investment_plans first if unsure.",
                },
                "planned_amount": {
                    "type": "number",
                    "description": "Planned debit amount in RMB (copy from the matching plan).",
                },
                "actual_amount": {
                    "type": "number",
                    "description": "Actual debited amount in RMB (from the bank text).",
                },
                "confirm_date": {
                    "type": "string",
                    "description": "Share-confirmation date in YYYY-MM-DD format. Omit if not yet known.",
                },
                "shares": {
                    "type": "number",
                    "description": "Confirmed shares (份额). Omit if not yet known.",
                },
                "status": {
                    "type": "string",
                    "enum": ["已扣款", "已确认", "已跳过", "失败"],
                    "description": "Override status. Usually omitted — defaults to '已确认' if confirm_date+shares are present, otherwise '已扣款'.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional free-form notes.",
                },
            },
            "required": ["debit_date", "fund", "planned_amount", "actual_amount"],
        },
    },
    {
        "name": "find_investment",
        "description": "Search the 流水 tab. Use to (a) locate the row to update when the user forwards a share-confirmation text for a previously-recorded debit (use pending_only=true), (b) verify a debit isn't already recorded before inserting. fund is substring match (case-insensitive); debit_date is exact.",
        "input_schema": {
            "type": "object",
            "properties": {
                "debit_date": {
                    "type": "string",
                    "description": "Exact match on 扣款日期 (YYYY-MM-DD).",
                },
                "fund": {
                    "type": "string",
                    "description": "Substring of fund name (case-insensitive).",
                },
                "pending_only": {
                    "type": "boolean",
                    "description": "If true, return only rows with status='已扣款' (awaiting share confirmation).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "update_investment_confirmation",
        "description": "Fill in 确认日期 and 确认份额 on an existing 流水 row, and set status='已确认'. Use when the user forwards a share-confirmation text for a debit already recorded as '已扣款'. Find the row first with find_investment(pending_only=true), then pass its row number here.",
        "input_schema": {
            "type": "object",
            "properties": {
                "row": {
                    "type": "integer",
                    "description": "1-based row number from find_investment.",
                },
                "confirm_date": {
                    "type": "string",
                    "description": "Share-confirmation date in YYYY-MM-DD format.",
                },
                "shares": {
                    "type": "number",
                    "description": "Confirmed shares (份额).",
                },
            },
            "required": ["row", "confirm_date", "shares"],
        },
    },
    {
        "name": "investment_summary",
        "description": "Aggregate totals over the 流水 tab. Use to answer '累计投了多少', '今年定投花了多少', 'X 基金投了多少'. Returns total_debited_rmb, total_shares_confirmed (only counts rows with status='已确认'), rows_count, pending_confirmations_count (rows still awaiting 份额), and a by_fund breakdown. Skipped/failed rows are excluded from totals. DO NOT estimate from memory — always call this tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fund": {
                    "type": "string",
                    "description": "Optional exact fund name filter.",
                },
                "year": {
                    "type": "integer",
                    "description": "Optional year filter (matches YYYY prefix of 扣款日期).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "record_purchase",
        "description": "Record one shopping trip as line items in the 家庭花销 明细 ledger. Each item becomes its own row sharing the trip's date and store — this per-item detail is what powers price-trend and 'what we buy most' queries later, so capture EVERY line, not a lump sum. Receipts here are English (user is in Ireland); keep item names as printed. Use unit_price when the receipt shows a per-unit price; use subtotal when it only shows the line total (e.g. loose produce sold by weight); pass both if both are visible. IMPORTANT: when the input is a receipt photo, do NOT call this immediately — first reply with the parsed lines (store, date, each item/qty/price) and ask the user to confirm, then call record_purchase after they say it's correct. Manual text entry of a single item can be recorded directly. Auto-restock: any tracked inventory item this trip replenished is bumped automatically — the result's inventory_updates lists what changed; mention it briefly if non-empty (e.g. '咖啡豆库存 +1 → 现 3 袋').",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Purchase date YYYY-MM-DD (from the receipt; use today if the user gives none).",
                },
                "store": {
                    "type": "string",
                    "description": "Store name, e.g. 'Lidl', 'Tesco', 'Amazon'.",
                },
                "items": {
                    "type": "array",
                    "description": "One object per line item.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string", "description": "Item name as printed on the receipt."},
                            "quantity": {"type": "number", "description": "Quantity (count or weight)."},
                            "unit_price": {"type": "number", "description": "Price per unit. Omit if only the line total is known."},
                            "subtotal": {"type": "number", "description": "Line total. Omit if only the unit price is known."},
                            "unit": {"type": "string", "description": "Optional unit, e.g. 'each', 'kg', 'pack'."},
                            "category": {"type": "string", "description": "Optional coarse bucket, e.g. '食品', '日用', '装修'."},
                            "notes": {"type": "string", "description": "Optional per-item note."},
                        },
                        "required": ["item", "quantity"],
                    },
                },
                "notes": {
                    "type": "string",
                    "description": "Optional trip-level note, applied to item rows that have no note of their own.",
                },
            },
            "required": ["date", "store", "items"],
        },
    },
    {
        "name": "find_purchase",
        "description": "Look up line items in the 花销 明细 ledger. Filters AND together: item (substring), store (substring), since/until (inclusive YYYY-MM-DD date bounds). Use for '上次在 X 买了啥', '6 月在 Tesco 花在哪些东西上'. For a pure price trend of one product, prefer price_history. DO NOT estimate from memory — call this tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Substring match on 商品."},
                "store": {"type": "string", "description": "Substring match on 店铺."},
                "since": {"type": "string", "description": "Inclusive start date YYYY-MM-DD."},
                "until": {"type": "string", "description": "Inclusive end date YYYY-MM-DD."},
            },
            "required": [],
        },
    },
    {
        "name": "price_history",
        "description": "Every purchase of a product (substring match on 商品), sorted oldest→newest, with date/store/quantity/unit_price. Use to answer '咖啡豆涨价了吗', 'X 最近多少钱', 'price trend'. Returns the raw series; you read off whether the price rose or fell and by how much. DO NOT estimate from memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Product name substring, e.g. 'coffee'."},
            },
            "required": ["item"],
        },
    },
    {
        "name": "top_items",
        "description": "Rank items in the 花销 明细 ledger to answer 'what do we buy the most / spend the most on'. Reports per item: times (purchase rows), total quantity, total spend. Sort by 'spend' (default), 'count', or 'quantity'. Optional since/until date range. DO NOT estimate from memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "by": {
                    "type": "string",
                    "enum": ["spend", "count", "quantity"],
                    "description": "Ranking metric. Default 'spend'.",
                },
                "since": {"type": "string", "description": "Inclusive start date YYYY-MM-DD."},
                "until": {"type": "string", "description": "Inclusive end date YYYY-MM-DD."},
                "limit": {"type": "integer", "description": "Max items to return (default 15)."},
            },
            "required": [],
        },
    },
    {
        "name": "track_item",
        "description": "Add an item to the 花销 库存 (inventory) watchlist, or update its settings. Being on this watchlist is what enables stock tracking and (future) restock reminders for it — untracked purchases just go to the ledger. Upsert by name: re-calling updates only the fields you pass (won't wipe accumulated quantity). Pick strategy by the item's nature: 'cycle' for things bought on a rough cadence and consumed steadily (coffee beans, milk) — no need to log consumption, low = long since last bought; 'threshold' for things used down to nothing with no regular need (DIY materials like cement, screws) — low = quantity at/below the minimum, so consumption must be logged via adjust_inventory. For 'threshold' you MUST pass threshold (the minimum quantity). For 'cycle' threshold is the typical interval in days (optional). Triggers: '开始跟踪/记一下库存 X', '把 X 加入库存', '咖啡豆还剩 2 袋，低于 1 袋提醒我'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Watchlist name. Keep it a distinctive substring of how it appears on receipts (e.g. 'coffee', 'cement') so purchases auto-match."},
                "unit": {"type": "string", "description": "Unit of stock, e.g. 'bag', 'kg', 'each'."},
                "strategy": {"type": "string", "enum": ["cycle", "threshold"], "description": "'cycle' = periodic buy (coffee); 'threshold' = consumed to zero (DIY materials)."},
                "threshold": {"type": "number", "description": "For 'threshold': minimum quantity (required). For 'cycle': typical interval in days (optional)."},
                "current_quantity": {"type": "number", "description": "Starting stock. Defaults to 0 on a new item; preserved on update if omitted."},
                "last_purchase_date": {"type": "string", "description": "Optional YYYY-MM-DD of the last purchase."},
                "last_unit_price": {"type": "number", "description": "Optional last known unit price."},
                "notes": {"type": "string", "description": "Optional note."},
            },
            "required": ["item", "unit", "strategy"],
        },
    },
    {
        "name": "adjust_inventory",
        "description": "Change a tracked item's current stock — for consumption or correction. Matches one active inventory item by substring. Use `delta` for a relative change ('用了2袋' → delta=-2; bought one by hand → delta=+1) or `set_quantity` for an absolute reading ('还剩半袋' → set_quantity=0.5; '没了/用完了' → set_quantity=0). Do NOT use this for normal purchases recorded via record_purchase — those auto-restock. This is for consumption and manual fixes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Substring of the tracked item's name."},
                "delta": {"type": "number", "description": "Relative change (negative for consumption)."},
                "set_quantity": {"type": "number", "description": "Absolute new quantity (overrides delta)."},
                "notes": {"type": "string", "description": "Optional note."},
            },
            "required": ["item"],
        },
    },
    {
        "name": "list_inventory",
        "description": "List inventory items with a computed low-stock flag. Use for '库存还有啥', '什么快没了/该买什么' (pass low_only=true for the restock list), 'X 还剩多少'. `low` is derived: threshold items are low when quantity <= 阈值; cycle items are low when days since last purchase >= the interval. Each item also reports days_since_purchase. DO NOT estimate from memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "low_only": {"type": "boolean", "description": "If true, return only items flagged low (the restock list)."},
                "status_filter": {"type": "string", "description": "Inventory status to filter by; default 'active'."},
            },
            "required": [],
        },
    },
    # Server-side tool — executed on Anthropic's infrastructure, NOT dispatched
    # by _execute_tool. The model issues a query; Anthropic searches and feeds
    # results (with citations) back within the same API call. A long search may
    # return stop_reason="pause_turn", which the chat loop resumes.
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
]


def _item_payload(item: Item) -> dict[str, Any]:
    """Compact serialisation for a single Item — drops empty fields. Used by
    list and find serialisers below.

    Deliberately EXCLUDES scheduler-internal fields (alerts, alerted,
    alerted_stale): the LLM should not see, edit, or echo them back to the
    user. Capture / query / status flows have no business touching alert
    state — only the scheduler scan loop reads them, and only the
    skip_remaining_alerts tool writes them indirectly.
    """
    return {
        k: v
        for k, v in {
            "title": item.title,
            "type": item.type,
            "mode": item.mode,
            "project": item.project,
            "due": item.due,
            "status": item.status,
            "tags": item.tags,
            "notes": item.notes,
        }.items()
        if v is not None
    }


def _items_to_payload(items: list[Item]) -> list[dict[str, Any]]:
    """Compact serialisation for tool_result content — drops empty fields."""
    return [_item_payload(item) for item in items]


def _matches_to_payload(
    matches: list[tuple[str, Item]],
) -> list[dict[str, Any]]:
    """Serialise find_item matches; each entry carries its location."""
    return [{"location": loc, **_item_payload(item)} for loc, item in matches]


def _execute_tool(name: str, args: dict[str, Any]) -> Any:
    """Dispatch a tool_use block to the corresponding storage function."""
    if name == "read_inbox":
        return _items_to_payload(read_inbox())
    if name == "read_archive":
        return _items_to_payload(read_archive())
    if name == "append_to_inbox":
        # Dispatch-layer guard against the Bug 1a hallucination pattern:
        # LLM occasionally calls append_to_inbox an extra time after a
        # legitimate update, producing a literal duplicate. Refuse here
        # if an item with the exact same title already exists in inbox;
        # the LLM should pivot to update_inbox_item or rephrase the title.
        # Case-insensitive exact match — strict enough not to false-positive
        # on similar-but-different items, lenient enough to catch literal
        # title repeats which is the actual failure mode observed.
        new_title = args["title"]
        for existing in read_inbox():
            if existing.title.lower() == new_title.lower():
                return {
                    "ok": False,
                    "reason": (
                        f"An item titled {existing.title!r} already exists in "
                        "inbox. Use update_inbox_item to modify it, or choose "
                        "a more specific title if you really intend a new item."
                    ),
                }
        item = Item(
            title=new_title,
            created=datetime.now().strftime("%Y-%m-%d %H:%M"),
            status="pending",
            type=args.get("type"),
            mode=args.get("mode"),
            project=args.get("project"),
            due=args.get("due"),
            tags=args.get("tags"),
            notes=args.get("notes"),
            alerts=args.get("alerts"),
        )
        append_to_inbox(item)
        return {"ok": True, "title": item.title}
    if name == "update_inbox_item":
        updated = update_inbox_item(
            args["title_substring"], args["field"], args["value"]
        )
        if updated is None:
            return {"ok": False, "reason": "no item matched"}
        return {
            "ok": True,
            "title": updated.title,
            "field": args["field"],
            "value": args["value"],
        }
    if name == "append_to_notes":
        updated = append_to_notes(args["title_substring"], args["value"])
        if updated is None:
            return {"ok": False, "reason": "no item matched"}
        return {
            "ok": True,
            "title": updated.title,
            "notes": updated.notes,
        }
    if name == "find_item":
        return _matches_to_payload(find_item(args["title_substring"]))
    if name == "skip_remaining_alerts":
        item = skip_remaining_alerts(args["title_substring"])
        if item is None:
            return {"ok": False, "reason": "no item matched"}
        return {"ok": True, "title": item.title}
    if name == "set_status":
        return _execute_set_status(args["title_substring"], args["status"])
    if name == "record_parcel":
        return record_parcel(
            date=args["date"],
            item=args["item"],
            platform=args["platform"],
            quantity=args["quantity"],
            unit_price=args.get("unit_price"),
            total_price=args.get("total_price"),
            tracking_no=args.get("tracking_no"),
            weight_kg=args.get("weight_kg"),
            notes=args.get("notes"),
        )
    if name == "find_parcel":
        return find_parcel(args["query"])
    if name == "update_parcel":
        return update_parcel(
            row=args["row"],
            status=args.get("status"),
            tracking_no=args.get("tracking_no"),
            weight_kg=args.get("weight_kg"),
            notes=args.get("notes"),
        )
    if name == "parcel_summary":
        return parcel_summary()
    if name == "update_parcels_by_tracking":
        return update_parcels_by_tracking(
            tracking_no=args["tracking_no"],
            status=args.get("status"),
            total_weight_kg=args.get("total_weight_kg"),
            notes=args.get("notes"),
        )
    if name == "settle_shipping":
        return settle_shipping(
            total_billed_weight_kg=args["total_billed_weight_kg"],
            total_shipping_rmb=args["total_shipping_rmb"],
        )
    if name == "apply_exchange_rate":
        return apply_exchange_rate(args["rate"])
    if name == "add_investment_plan":
        return add_investment_plan(
            fund=args["fund"],
            frequency=args["frequency"],
            planned_amount=args["planned_amount"],
            start_date=args["start_date"],
            day_of_month=args.get("day_of_month"),
            day_of_week=args.get("day_of_week"),
            notes=args.get("notes"),
        )
    if name == "list_investment_plans":
        return list_investment_plans(status_filter=args.get("status_filter", "active"))
    if name == "update_plan_status":
        return update_plan_status(fund=args["fund"], status=args["status"])
    if name == "record_investment":
        return record_investment(
            debit_date=args["debit_date"],
            fund=args["fund"],
            planned_amount=args["planned_amount"],
            actual_amount=args["actual_amount"],
            confirm_date=args.get("confirm_date"),
            shares=args.get("shares"),
            status=args.get("status"),
            notes=args.get("notes"),
        )
    if name == "find_investment":
        return find_investment(
            debit_date=args.get("debit_date"),
            fund=args.get("fund"),
            pending_only=args.get("pending_only", False),
        )
    if name == "update_investment_confirmation":
        return update_investment_confirmation(
            row=args["row"],
            confirm_date=args["confirm_date"],
            shares=args["shares"],
        )
    if name == "investment_summary":
        return investment_summary(fund=args.get("fund"), year=args.get("year"))
    if name == "record_purchase":
        return record_purchase(
            date=args["date"],
            store=args["store"],
            items=args["items"],
            notes=args.get("notes"),
        )
    if name == "find_purchase":
        return find_purchase(
            item=args.get("item"),
            store=args.get("store"),
            since=args.get("since"),
            until=args.get("until"),
        )
    if name == "price_history":
        return price_history(item=args["item"])
    if name == "top_items":
        return top_items(
            by=args.get("by", "spend"),
            since=args.get("since"),
            until=args.get("until"),
            limit=args.get("limit", 15),
        )
    if name == "track_item":
        return track_item(
            item=args["item"],
            unit=args["unit"],
            strategy=args["strategy"],
            threshold=args.get("threshold"),
            current_quantity=args.get("current_quantity"),
            last_purchase_date=args.get("last_purchase_date"),
            last_unit_price=args.get("last_unit_price"),
            notes=args.get("notes"),
        )
    if name == "adjust_inventory":
        return adjust_inventory(
            item=args["item"],
            delta=args.get("delta"),
            set_quantity=args.get("set_quantity"),
            notes=args.get("notes"),
        )
    if name == "list_inventory":
        return list_inventory(
            status_filter=args.get("status_filter", "active"),
            low_only=args.get("low_only", False),
        )
    raise ValueError(f"unknown tool: {name!r}")


def _execute_set_status(title_substring: str, new_status: str) -> dict[str, Any]:
    """Dispatch for the set_status tool. Resolves the target item, enforces
    the sequential-project in_progress invariant, then either writes the
    new status in place (non-terminal) or moves the item to archive
    (terminal). Returns a JSON-friendly dict for the tool_result.
    """
    # Resolve the target. We search both inbox and archive so we can give a
    # useful error if the item is already archived (status changes only make
    # sense on inbox items).
    matches = find_item(title_substring)
    if not matches:
        return {"ok": False, "reason": "no item matched"}
    target: Optional[Item] = None
    for loc, item in matches:
        if loc == "inbox":
            target = item
            break
    if target is None:
        return {
            "ok": False,
            "reason": "item is in archive, not inbox — cannot change its status",
        }

    # Sequential-project invariant: only one in_progress step per project.
    if new_status == "in_progress" and target.project:
        project_record: Optional[Item] = None
        for _loc, it in find_item(target.project):
            if it.type == "project" and it.title == target.project:
                project_record = it
                break
        if project_record and project_record.mode == "sequential":
            siblings_in_progress = [
                it
                for it in read_inbox()
                if it.project == target.project
                and it.status == "in_progress"
                and it.title != target.title
            ]
            if siblings_in_progress:
                blocker = siblings_in_progress[0]
                return {
                    "ok": False,
                    "reason": (
                        f"sequential project '{target.project}' already has an "
                        f"in_progress step: '{blocker.title}'. Finish or pause "
                        "that step before starting another."
                    ),
                }

    # Apply the change. Terminal statuses go through move_to_archive so the
    # item physically moves; non-terminal stays in inbox.
    if new_status in TERMINAL_STATUSES:
        moved = move_to_archive(target.title, new_status)
        if moved is None:
            return {"ok": False, "reason": "no item matched on move"}
        return {
            "ok": True,
            "title": moved.title,
            "status": new_status,
            "moved_to_archive": True,
        }
    updated = set_item_status(target.title, new_status)
    if updated is None:
        return {"ok": False, "reason": "no item matched on set"}
    return {"ok": True, "title": updated.title, "status": new_status}


def _extract_text(content: list[Any]) -> str:
    """Concatenate text blocks from a model response. Skips thinking/tool_use blocks."""
    return "\n".join(b.text for b in content if b.type == "text").strip()


class AnthropicBackend(LLMBackend):
    def __init__(self, model: str = MODEL) -> None:
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = model

    def chat(
        self,
        user_message: str,
        system_prompt: str = "",
        history: list[dict[str, str]] | None = None,
        images: list[bytes] | None = None,
    ) -> str:
        # Conversation history (if any) goes at the front of the messages
        # list so the model sees prior turns as context for the new one.
        # The agent loop appends its own assistant + tool_result blocks on
        # top of this during a single chat() call.
        messages: list[dict[str, Any]] = list(history) if history else []

        if images:
            # Per-call model swap: vision goes to Sonnet 4.6 (cheaper, equal
            # quality on parcel screenshots per the spike).
            model = VISION_MODEL
            content: list[dict[str, Any]] = []
            for img in images:
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(img).decode("ascii"),
                        },
                    }
                )
            content.append({"type": "text", "text": user_message})
            messages.append({"role": "user", "content": content})
        else:
            model = self.model
            messages.append({"role": "user", "content": user_message})

        for turn in range(MAX_LOOP_TURNS):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "tools": TOOLS,
                "messages": messages,
                "thinking": {"type": "adaptive"},
                "cache_control": {"type": "ephemeral"},
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            try:
                response = self.client.messages.create(**kwargs)
            except anthropic.APIStatusError as exc:
                logger.exception("Anthropic API error on turn %d", turn)
                raise RuntimeError(f"Anthropic API error: {exc}") from exc

            usage = response.usage
            logger.info(
                "turn=%d stop_reason=%s in=%d out=%d cache_read=%d cache_create=%d",
                turn,
                response.stop_reason,
                usage.input_tokens,
                usage.output_tokens,
                getattr(usage, "cache_read_input_tokens", 0) or 0,
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
            )

            if response.stop_reason == "refusal":
                return "[refusal] The model declined to respond."

            if response.stop_reason == "max_tokens":
                # Hit the per-response cap; surface what we have so far.
                partial = _extract_text(response.content)
                return partial + "\n[truncated: max_tokens reached]"

            if response.stop_reason in ("end_turn", "stop_sequence"):
                return _extract_text(response.content)

            if response.stop_reason == "pause_turn":
                # A server-side tool (web_search) hit its internal iteration cap
                # mid-run. Re-send the assistant content to resume — do NOT add
                # a user message; the server picks up from the trailing
                # server_tool_use block.
                messages.append({"role": "assistant", "content": response.content})
                continue

            if response.stop_reason != "tool_use":
                logger.warning("unexpected stop_reason: %s", response.stop_reason)
                return _extract_text(response.content)

            # tool_use: dispatch every tool_use block, then loop with results.
            messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                logger.info("tool_use: %s(%s)", block.name, block.input)
                try:
                    result = _execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                except Exception as exc:
                    logger.exception("tool %s failed", block.name)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"error": str(exc)}),
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(
            f"agent loop exceeded MAX_LOOP_TURNS={MAX_LOOP_TURNS}; "
            "the model is likely stuck in a tool-use cycle."
        )
