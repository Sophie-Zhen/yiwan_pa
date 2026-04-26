"""my-assistant v0.1 — iteration 3: capture flow.

The bot now operates as a personal assistant: every Telegram message is
forwarded to the LLM along with a system prompt that defines the assistant's
role, the markdown storage format, and the four supported actions
(capture / query / complete / modify). The LLM uses its file tools to
read and write data/inbox.md and data/archive.md directly.
"""
import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from llm import get_backend
from prompts import render_personal_assistant

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

backend = get_backend()
USER_NAME = os.getenv("USER_NAME", "the user")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    logger.info("user: %s", text)
    system_prompt = render_personal_assistant(USER_NAME)
    try:
        reply = await asyncio.to_thread(backend.chat, text, system_prompt)
    except Exception as exc:
        logger.exception("backend.chat failed")
        reply = f"[error] {exc}"
    logger.info("bot: %s", reply[:200])
    await update.message.reply_text(reply)


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot starting... press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
