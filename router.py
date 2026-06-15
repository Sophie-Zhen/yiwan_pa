"""Per-message domain routing: decide which prompt sections + tool bundles ship.

Shrinks the per-call prefix by sending only the active domain(s)' instructions
and tools instead of all of them (~18k -> ~5-6k tokens on a typical message).
Returns a SET (union, never a single label) so a multi-domain message keeps
every relevant section. Ambiguity or no signal => ALL domains => byte-identical
to the pre-routing behavior, so a misroute degrades gracefully (the model just
lacks some guidance) rather than failing.

Ships behind ROUTING_ENABLED: while False, route_domains always returns ALL, so
the wiring is a no-op and the bot is byte-for-byte today's behavior. Flip to True
to actually narrow, after the byte-identity guard confirms the dark path matches.

Trigger words are lifted from the matching sections in prompts.py — keep the two
in sync (scripts/test_router.py guards the coupling).
"""
import os
import re

from prompts import ALL_DOMAINS

# Off by default (ships dark): always return ALL = today's exact behavior. Flip
# via env (ROUTING_ENABLED=true) so it can be enabled/rolled back on the Pi
# without editing source, like LLM_BACKEND / USER_NAME.
ROUTING_ENABLED = os.getenv("ROUTING_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# domain -> compiled regex of trigger words (copied from each prompt section).
# CJK terms match as substrings (word boundaries don't apply to CJK); ASCII
# tokens are wrapped in \b so unrelated English ("a TB of storage", "outback",
# "cwiczenia") can't substring-match a domain. Genuinely ambiguous bare words
# (the taobao "tb") are dropped — the CJK 淘宝 still routes them.
_TRIGGERS = {
    "parcels": re.compile(r"买了|下单|签收|发货|入库|拍照|运费|计费重量|汇率|京东|淘宝|\b(?:pdd|1688)\b", re.I),
    "investments": re.compile(r"定投|基金|加仓|扣款|申购|确认份额|净值|份额", re.I),
    "expenses": re.compile(r"花了|买菜|超市|话费|家庭花销|库存|剩|用完|记一笔|\b(?:lidl|tesco|aldi|dunnes)\b", re.I),
    "contracts": re.compile(r"续约|合同|保单到期|续费", re.I),
    "documents": re.compile(r"文档|存档|保单|\bpolicy\b", re.I),
    "cwi": re.compile(r"教学日志|上课|课时|抱石|\b(?:dlog|cwi|taster|induction)\b", re.I),
    "todos": re.compile(r"提醒|记一下|别忘了|计划|步骤|\btodo\b", re.I),
}


def route_domains(text: str = "", has_image: bool = False, has_document: bool = False) -> set[str]:
    """Return the set of active domains for a message. ALL on ambiguity / no hit."""
    if not ROUTING_ENABLED:
        return set(ALL_DOMAINS)

    active: set[str] = set()
    # Input-type hard rules. A PDF is a document; a photo is a parcel screenshot
    # or a receipt/inventory shot. KNOWN LIMITATION: a photographed (non-PDF)
    # paper contract/policy isn't routed to documents/contracts — caption it
    # ("保单"/"合同") to pull those in. Widen here if it bites in practice.
    if has_document:
        active.add("documents")  # contracts + expenses added by the co-load rule below
    if has_image:
        active |= {"parcels", "expenses"}

    for domain, pat in _TRIGGERS.items():
        if pat.search(text or ""):
            active.add(domain)

    # documents follow-ups call add_contract + record_purchase — co-load so the
    # cross-referenced sections/tools are present when the follow-up fires.
    if "documents" in active:
        active |= {"contracts", "expenses"}

    # No signal at all => fall back to the full prompt (zero regression).
    if not active:
        return set(ALL_DOMAINS)

    # web_search section is cheap and its tool is always-on; keep it present.
    active.add("web_search")
    return active
