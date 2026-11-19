
import os
import logging
import hashlib

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", "10000"))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")


async def start(update: Update, context):
    await update.message.reply_text(
        "机器人已经启动！\n\n"
        "发送 /id 获取 Telegram 用户 ID 和群组 ID。\n"
        "发送 /help 查看帮助。"
    )


async def get_id(update: Update, context):
    user = update.effective_user
    chat = update.effective_chat

    await update.message.reply_text(
        f"你的 Telegram 用户 ID：{user.id}\n"
        f"当前聊天/群组 ID：{chat.id}\n"
        f"聊天类型：{chat.type}"
    )


async def help_command(update: Update, context):
    await update.message.reply_text(
        "/start - 启动机器人\n"
        "/id - 获取用户和群组 ID\n"
        "/help - 查看帮助"
    )


def main():
    if not RENDER_URL:
        raise RuntimeError("没有读取到 Render 的服务网址")

    webhook_path = hashlib.sha256(
        BOT_TOKEN.encode("utf-8")
    ).hexdigest()

    secret_token = webhook_path[:40]
    webhook_url = f"{RENDER_URL}/{webhook_path}"

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("help", help_command))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path,
        webhook_url=webhook_url,
        secret_token=secret_token,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
