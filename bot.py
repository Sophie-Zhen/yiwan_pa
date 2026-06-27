"""my-assistant v0.1 — iteration 4: scheduled morning digest.

On top of iteration 3 (capture / query / complete via system prompt), this
adds:

- /id command — replies with the current chat's ID, so the user can copy it
  into TELEGRAM_USER_CHAT_ID in .env (needed for scheduled push).
- /digest command — triggers an immediate digest, mainly for testing the
  digest pipeline without waiting for the scheduled time.
- Scheduled digests — the daily morning/evening pushes live in
  digest_scheduler.py, registered from main() alongside the other schedulers.
"""
import asyncio
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import contract_scheduler
import cwi_scheduler
import digest_scheduler
import inventory_scheduler
import investment_scheduler
import scheduler
from llm import get_backend
from llm.tooldefs import build_tools
from prompts import (
    render_morning_digest_request,
    render_personal_assistant,
    render_todos_request,
)
from router import route_domains
from storage.history import append_turn, read_history
from tools.documents import DOCS_DIR, slugify

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

backend = get_backend()
USER_NAME = os.getenv("USER_NAME", "the user")
USER_LANGUAGE = os.getenv("USER_LANGUAGE", "Chinese")

_chat_id_env = os.getenv("TELEGRAM_USER_CHAT_ID", "").strip()
USER_CHAT_ID: int | None = int(_chat_id_env) if _chat_id_env else None

# /<command> -> (domain bundle it loads, Telegram menu description). `todos` is
# always-on (build_tools adds it), so each command ships: core prompt + todos
# tools + this one domain's section & tools — a ~4-6k prefix instead of the full
# ~19k. This dict is the single source of truth for BOTH the CommandHandler
# registration in main() and the "/" command menu in _set_commands(), so the two
# can't drift. Manually tagging a message is how the user opts into a small
# prefix; an untagged message loads everything (full prefix, always correct).
_DOMAIN_COMMANDS: dict[str, tuple[set[str], str]] = {
    "spend":    ({"expenses"},    "记一笔家庭花销 / 超市小票"),
    "parcel":   ({"parcels"},     "转运包裹下单 / 签收 / 入库"),
    "fund":     ({"investments"}, "基金定投扣款 / 计划"),
    "contract": ({"contracts"},   "合同续约 / 到期"),
    "doc":      ({"documents"},   "文档存档与问答"),
    "cwi":      ({"cwi"},         "CWI 教学日志"),
    "todo":     ({"todos"},       "只记待办（最小 prefix）"),
}


def _is_authorized(update: Update) -> bool:
    """Fail closed: only the configured chat_id may invoke the LLM / tools.

    When TELEGRAM_USER_CHAT_ID is unset, everything except /id is blocked —
    the legit user bootstraps by calling /id, copying the value into .env,
    and restarting the bot.
    """
    if USER_CHAT_ID is None:
        return False
    return update.effective_chat.id == USER_CHAT_ID


async def _ask_llm(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    images: list[bytes] | None = None,
    documents: list[bytes] | None = None,
    domains: set[str] | None = None,
) -> str:
    """Run a single LLM round-trip with the personal-assistant system prompt.

    `domains` (from router.route_domains) selects which prompt sections + tools
    ship, shrinking the per-call prefix. None = all domains — used by the canned
    digest/todos commands and any direct caller, and byte-identical to before."""
    system_prompt = render_personal_assistant(USER_NAME, domains)
    tools = build_tools(domains)
    return await asyncio.to_thread(
        backend.chat, user_message, system_prompt, history, images, documents, tools
    )


# Telegram rejects a single message over 4096 chars with BadRequest("Message is
# too long"). LLM replies (e.g. a multi-paragraph CWI DLOG draft) can exceed it,
# so split into chunks. 4000 leaves margin.
TELEGRAM_MAX_CHARS = 4000


def _split_for_telegram(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Split a reply into <=limit-char chunks, preferring paragraph > line >
    space boundaries over cutting mid-word."""
    rest = (text or "").strip() or "(empty reply)"
    chunks: list[str] = []
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit  # no good boundary — hard cut
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


async def _send_reply(message, text: str) -> None:
    """Send a possibly-long LLM reply as one or more messages. A long reply used
    to raise BadRequest('Message is too long') from reply_text — and because the
    send sits outside the handler's try/except, that silently dropped the entire
    reply. Splitting + a guarded send means a reply is never lost to length."""
    for chunk in _split_for_telegram(text):
        try:
            await message.reply_text(chunk)
        except Exception:
            logger.exception("failed to send a reply chunk (%d chars)", len(chunk))


async def _handle_text(
    message, chat_id: int, text: str, domains: set[str] | None
) -> None:
    """Shared text round-trip for plain messages AND the /<domain> tag commands.

    `domains` selects which prompt sections + tools ship (None = all). The
    plain-text path passes None (load everything — always correct, full prefix);
    a /<domain> command passes that one domain's bundle to shrink the prefix.
    This is the manual replacement for regex routing on text: tag to go small,
    and forgetting to tag costs tokens, never a missing tool."""
    # Read recent history (sliding window: 6 turns AND last 30 min, whichever
    # is shorter — see storage.history). Empty list on first message.
    history = read_history(chat_id)

    # Inject current wall-clock time so the LLM can resolve relative
    # references ("10 分钟后", "半小时后") and fill `created` accurately.
    # The system prompt deliberately omits HH:MM for prompt-cache stability;
    # putting time on the per-message side keeps the cached prefix intact.
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prefixed = f"[Now: {now_str}]\n{text}"

    try:
        reply = await _ask_llm(prefixed, history=history, domains=domains)
    except Exception as exc:
        logger.exception("backend.chat failed")
        reply = f"[error] {exc}"

    # Persist the ORIGINAL message (not prefixed) so history files stay
    # human-readable and prior turns don't accumulate stale [Now: ...] tags.
    append_turn(chat_id, text, reply)

    logger.info("bot: %s", reply[:200])
    await _send_reply(message, reply)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (message)", chat_id)
        return
    text = update.message.text
    logger.info("user (chat %s): %s", chat_id, text)
    # Untagged plain text loads all domains (full prefix, always correct). Use a
    # /<domain> command to opt into a smaller prefix — see _DOMAIN_COMMANDS.
    await _handle_text(update.message, chat_id, text, domains=None)


def _command_body(text: str) -> str:
    """Strip the leading '/cmd' (or '/cmd@bot') token from a command message and
    return the rest verbatim. Splits only on the FIRST whitespace run, so a
    multi-line body survives intact."""
    parts = (text or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _make_domain_command(domains: set[str]):
    """Build a /<domain> command handler that loads only `domains`' bundle. Called
    once per _DOMAIN_COMMANDS entry, so the commands share _handle_text instead of
    duplicating it."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not _is_authorized(update):
            logger.warning("unauthorized chat %s blocked (domain command)", chat_id)
            return
        body = _command_body(update.message.text)
        if not body:
            await update.message.reply_text("用法：/<命令> 后面跟内容，例如 /spend 咖啡 8.99")
            return
        logger.info("user (chat %s) [%s]: %s", chat_id, ",".join(sorted(domains)), body)
        await _handle_text(update.message, chat_id, body, domains)

    return handler


async def _process_images(message, chat_id: int, images: list[bytes], caption: str) -> None:
    """Run one vision LLM round-trip over `images` and reply once.

    Shared by the single-photo and album (media-group) paths so both behave
    identically apart from the image count. `message` is the reply anchor.
    """
    marker = "[图片]" if len(images) == 1 else f"[图片 ×{len(images)}]"

    logger.info(
        "user (chat %s, %d image(s), %d bytes total, caption=%r)",
        chat_id,
        len(images),
        sum(len(b) for b in images),
        caption,
    )

    history = read_history(chat_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_message = f"[Now: {now_str}] {marker} {caption}".strip()

    domains = route_domains(caption, has_image=True)
    try:
        reply = await _ask_llm(user_message, history=history, images=images, domains=domains)
    except Exception as exc:
        logger.exception("photo backend.chat failed")
        reply = f"[error] {exc}"

    # History stores the caption + [图片] marker so future turns know image(s)
    # were sent, but NOT the bytes — they would balloon the JSONL file and the
    # model can't re-look at past images on subsequent turns anyway.
    history_text = f"{marker} {caption}".strip()
    append_turn(chat_id, history_text, reply)

    logger.info("bot: %s", reply[:200])
    await _send_reply(message, reply)


# Album aggregation. Telegram delivers a multi-photo album as N separate photo
# updates that share one media_group_id, fired back-to-back. We buffer each
# group's images and flush them as ONE LLM call once no new photo has arrived
# for _GROUP_DEBOUNCE_SECONDS — so several screenshots of the same order are
# read together instead of as N unrelated single-image calls. A lone photo has
# media_group_id=None and is processed immediately (no debounce delay).
#
# The window must exceed the GAP BETWEEN consecutive photos, not the whole
# album: PTB processes updates sequentially, and each handler re-arms the timer
# only AFTER it has downloaded its image — so on a slow Pi link the gap is
# roughly one image's download time. 3s leaves margin for that while keeping the
# album reply prompt. Only albums wait; lone photos are unaffected. Tune if a Pi
# album ever splits into two replies (gap exceeded the window).
_GROUP_DEBOUNCE_SECONDS = 3.0
_media_groups: dict[str, dict] = {}


async def _flush_media_group(group_id: str) -> None:
    """Fire _GROUP_DEBOUNCE_SECONDS after the last photo of `group_id` arrived;
    rescheduled (and the prior task cancelled) by every new photo, so reaching
    the flush means the album is complete."""
    try:
        await asyncio.sleep(_GROUP_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return
    group = _media_groups.pop(group_id, None)
    if not group:
        return
    await _process_images(
        group["message"], group["chat_id"], group["images"], group["caption"]
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Image messages — typically a parcel order screenshot or warehouse
    arrival notification. The caption (if any) gives the LLM hints about
    platform / context; without one, the LLM still has to identify the type
    from the image. Multi-photo albums are buffered and processed together.
    """
    chat_id = update.effective_chat.id
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (photo)", chat_id)
        return

    # photo is a list of PhotoSize at increasing resolutions; last = largest.
    # Telegram already downscales user photos to ~1280px wide for `photo`
    # messages, so even the largest is reasonably sized for vision.
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    caption = (update.message.caption or "").strip()

    group_id = update.message.media_group_id
    if group_id is None:
        await _process_images(update.message, chat_id, [image_bytes], caption)
        return

    # Album: buffer this image and (re)arm the debounced flush. Single-threaded
    # asyncio + sequential update processing means these dict ops need no lock.
    group = _media_groups.get(group_id)
    if group is None:
        group = {
            "chat_id": chat_id,
            "message": update.message,  # reply anchor = first photo of the album
            "images": [],
            "caption": "",
            "task": None,
        }
        _media_groups[group_id] = group
    group["images"].append(image_bytes)
    if caption and not group["caption"]:  # caption rides on one album member
        group["caption"] = caption
    if group["task"] is not None:
        group["task"].cancel()
    group["task"] = asyncio.create_task(_flush_media_group(group_id))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PDF document messages — typically an insurance policy or similar to be
    distilled into a fact-sheet. The original PDF is saved to data/documents/
    (source of truth / future full-read fallback); the bytes are sent to the
    LLM (Opus, via the documents path) which extracts a fact-sheet and asks the
    user to confirm before saving it.
    """
    chat_id = update.effective_chat.id
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (document)", chat_id)
        return

    doc = update.message.document
    fname = doc.file_name or "document.pdf"
    mime = (doc.mime_type or "").lower()
    if "pdf" not in mime and not fname.lower().endswith(".pdf"):
        await update.message.reply_text("目前只支持 PDF 文档。")
        return
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("这个 PDF 太大了（>20MB），发个小一点的版本吧。")
        return

    file = await context.bot.get_file(doc.file_id)
    pdf_bytes = bytes(await file.download_as_bytearray())

    # Persist the original immediately, before any LLM work.
    stem = fname[:-4] if fname.lower().endswith(".pdf") else fname
    saved_name = f"{slugify(stem)}.pdf"
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / saved_name).write_bytes(pdf_bytes)

    caption = (update.message.caption or "").strip()
    logger.info(
        "user (chat %s, document %r %d bytes, caption=%r) saved as %s",
        chat_id, fname, len(pdf_bytes), caption, saved_name,
    )

    history = read_history(chat_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_message = (
        f"[Now: {now_str}] [文档 PDF: {fname}，原件已存为 {saved_name}] {caption}".strip()
    )

    domains = route_domains(caption, has_document=True)
    try:
        reply = await _ask_llm(user_message, history=history, documents=[pdf_bytes], domains=domains)
    except Exception as exc:
        logger.exception("document backend.chat failed")
        reply = f"[error] {exc}"

    history_text = f"[文档 PDF: {fname}] {caption}".strip()
    append_turn(chat_id, history_text, reply)

    logger.info("bot: %s", reply[:200])
    await _send_reply(update.message, reply)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"chat_id: {chat_id}\n\n"
        f"Paste this into your .env as TELEGRAM_USER_CHAT_ID to enable scheduled pushes."
    )


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand digest — useful for testing without waiting for the scheduled time."""
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (/digest)", update.effective_chat.id)
        return
    try:
        # Digest only reads + formats the inbox: minimal todos-only bundle.
        reply = await _ask_llm(render_morning_digest_request(USER_LANGUAGE), domains={"todos"})
    except Exception as exc:
        logger.exception("digest failed")
        reply = f"[error] {exc}"
    await _send_reply(update.message, reply)


async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand full pending list, including items with no due date.
    Distinct from /digest, which uses the morning-digest format and only
    surfaces no-due items via the stale filter."""
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (/todos)", update.effective_chat.id)
        return
    try:
        reply = await _ask_llm(render_todos_request(USER_LANGUAGE), domains={"todos"})
    except Exception as exc:
        logger.exception("todos failed")
        reply = f"[error] {exc}"
    await _send_reply(update.message, reply)


async def cmd_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or set the active parcel tab (e.g. 6月有易) for transhipment tools.

    `/active` — show current active tab.
    `/active <name>` — switch to that tab; validates it exists in the sheet.
    """
    if not _is_authorized(update):
        logger.warning("unauthorized chat %s blocked (/active)", update.effective_chat.id)
        return

    from gspread.exceptions import WorksheetNotFound

    from tools.parcels import get_active_tab, set_active_tab

    arg = " ".join(context.args).strip() if context.args else ""
    if not arg:
        current = get_active_tab()
        msg = (
            f"当前 active tab: {current}"
            if current
            else "未设置 active tab，用 `/active <tab名>` 设置"
        )
        await update.message.reply_text(msg)
        return

    try:
        set_active_tab(arg)
    except WorksheetNotFound:
        await update.message.reply_text(
            f"找不到 tab: {arg}（拼写是否与 sheet 中显示一致？）"
        )
        return
    except KeyError as exc:
        await update.message.reply_text(
            f"配置缺失: {exc}（检查 .env 里的 GOOGLE_SHEETS_CREDENTIALS / GOOGLE_SHEET_ID）"
        )
        return
    await update.message.reply_text(f"active tab 切换到 {arg}")


async def _set_commands(application: Application) -> None:
    """Register the slash-command menu Telegram shows when the user types '/'.
    Runs once on startup via post_init; idempotent. Keep in sync with the
    CommandHandler list in main()."""
    await application.bot.set_my_commands([
        BotCommand("id", "Show current chat_id"),
        BotCommand("digest", "Trigger today's morning digest"),
        BotCommand("todos", "List all pending items"),
        BotCommand("active", "Show or set the active parcel tab"),
        *[BotCommand(cmd, desc) for cmd, (_d, desc) in _DOMAIN_COMMANDS.items()],
    ])


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).post_init(_set_commands).build()

    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("todos", cmd_todos))
    app.add_handler(CommandHandler("active", cmd_active))
    for _cmd, (_domains, _desc) in _DOMAIN_COMMANDS.items():
        app.add_handler(CommandHandler(_cmd, _make_domain_command(_domains)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    if app.job_queue is None:
        logger.warning(
            "JobQueue is not available — install with `pip install \"python-telegram-bot[job-queue]\"`"
        )
    else:
        digest_scheduler.register_jobs(app)
        scheduler.register_jobs(app)
        investment_scheduler.register_jobs(app)
        inventory_scheduler.register_jobs(app)
        contract_scheduler.register_jobs(app)
        cwi_scheduler.register_jobs(app)

    print("Bot starting... press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
