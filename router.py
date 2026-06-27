"""Input-type domain routing for PHOTO and PDF messages.

Decides which prompt sections + tool bundles ship for an image / document, so a
photo or PDF call sends only the relevant domains (e.g. a photo -> parcels +
expenses) instead of the full ~19k-token prefix. Returns a SET (union, never a
single label) so a multi-domain message keeps every relevant section; on no
signal it falls back to ALL domains, so a misroute degrades gracefully (the model
just lacks some guidance) rather than failing.

Scope note: the TEXT path no longer uses this. Text messages select their bundle
by manual /<domain> slash-command tags (see bot._DOMAIN_COMMANDS), and an untagged
text message loads all domains. route_domains is now called only by the photo /
document handlers in bot.py, always with has_image or has_document set.

The decisive part is the input-type rule (a photo is a parcel/receipt shot, a PDF
is a document) — deterministic, not keyword-based. The caption `_TRIGGERS` regex
only adds to that, and only on the rare captioned image/PDF (e.g. a photo of a
paper policy captioned "保单" -> documents). Trigger words are lifted from the
matching sections in prompts.py — keep the two in sync (scripts/test_router.py
guards the coupling).
"""
import os
import re

from prompts import ALL_DOMAINS

# Toggles photo/PDF routing without editing source (like LLM_BACKEND / USER_NAME).
# When False, route_domains returns ALL domains, so an image/PDF ships the full
# prefix; when True, it narrows to the input-type bundle. Default off; the Pi sets
# ROUTING_ENABLED=true to get the smaller image/PDF prefix.
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
