"""my-assistant v0.1 — iteration 2: bot wired to LLM backend.

Receives Telegram text messages, routes them to the configured LLM backend,
and replies with whatever the LLM returns. No storage / no system prompt yet
— that arrives in iteration 3 (capture flow).
"""
import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from llm import get_backend

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

backend = get_backend()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    logger.info("user: %s", text)
    # The shell-out is blocking and can take seconds. Run it in a thread so
    # the bot's asyncio event loop stays responsive to other Telegram traffic.
    try:
        reply = await asyncio.to_thread(backend.chat, text)
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
