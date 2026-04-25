"""my-assistant v0.1 — iteration 1: echo bot only.

The simplest possible Telegram bot: receives a message, echoes it back.
No LLM, no storage. Goal of this iteration: prove the Telegram pipeline works
end-to-end before adding any other moving parts.
"""
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    await update.message.reply_text(f"Received: {text}")


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    print("Bot starting... press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
