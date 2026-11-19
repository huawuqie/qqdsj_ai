
import os
import json
import logging
import hashlib
import asyncio
from datetime import time
from zoneinfo import ZoneInfo

import feedparser
from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
GROUP_CHAT_ID = int(os.environ["GROUP_CHAT_ID"])

PORT = int(os.environ.get("PORT", "10000"))
RENDER_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)
pending = {}


NEWS_FEEDS = [
    ("社会热点", "https://news.google.com/rss/search?q=社会新闻&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("社会热点", "https://news.google.com/rss/search?q=社会热点&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("国际新闻", "https://news.google.com/rss/search?q=国际新闻&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("娱乐影视", "https://news.google.com/rss/search?q=娱乐影视&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("科技资讯", "https://news.google.com/rss/search?q=科技新闻&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("奇闻趣事", "https://news.google.com/rss/search?q=奇闻趣事&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("真实故事", "https://news.google.com/rss/search?q=真实故事&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "机器人已启动！\n"
        "/id - 获取用户和群组 ID\n"
        "/help - 查看帮助\n"
        "/generate - 手动生成一批待审核内容"
    )


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"用户 ID：{update.effective_user.id}\n"
        f"聊天 ID：{update.effective_chat.id}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - 启动机器人\n"
        "/id - 查看 ID\n"
        "/generate - 手动生成内容"
    )


def collect_news():
    items = []
    for category, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                if title and link:
                    items.append({
                        "category": category,
                        "title": title,
                        "url": link,
                    })
        except Exception:
            logger.exception("新闻 RSS 获取失败")
    return items


async def create_content():
    sources = collect_news()
    if len(sources) < 7:
        raise RuntimeError("采集到的新闻来源不足 7 条，请稍后重试。")

    prompt = f"""
你是中文新闻编辑和原创故事作者。请生成严格的 JSON 数组，共 10 条内容。

其中 7 条是新闻摘要，必须从提供的新闻来源中选择，不得编造新闻事实：
- 社会热点 2 条
- 国际新闻 1 条
- 娱乐影视新闻 1 条
- 科技资讯 1 条
- 奇闻趣事 1 条
- 真实故事 1 条

另外 3 条是 AI 原创故事：
- 悬疑故事 1 条
- 情感故事 1 条
- 历史故事 1 条

新闻必须使用对应来源的原始标题和 URL，不得编造网址。
新闻摘要用自己的话写，控制在 100 至 180 个汉字。
原创故事控制在 250 至 400 个汉字，并标注“AI 原创故事”，不得冒充真实事件。

每项字段：
category、title、content、source_url、kind

新闻 kind 填 news，原创故事 kind 填 original。
没有来源的原创故事 source_url 填空字符串。
只输出 JSON 数组，不要 Markdown 代码块。

可用新闻来源：
{json.dumps(sources, ensure_ascii=False)}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "你必须遵守用户要求，只返回有效 JSON 数组。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    data = json.loads(raw)
    if not isinstance(data, list) or len(data) != 10:
        raise RuntimeError("AI 返回内容数量不正确，请重新生成。")

    return data


def format_content(item):
    kind = item.get("kind", "news")
    label = "【AI 原创故事】" if kind == "original" else "【新闻摘要】"
    text = (
        f"{label} {item.get('category', '内容')}\n\n"
        f"<b>{item.get('title', '未命名')}</b>\n\n"
        f"{item.get('content', '')}"
    )
    source = item.get("source_url", "")
    if source and kind == "news":
        text += f'\n\n<a href="{source}">查看新闻来源</a>'
    return text


async def send_for_review(context: ContextTypes.DEFAULT_TYPE, items):
    global pending
    pending = {}

    for index, item in enumerate(items):
        pending[index] = item
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ 通过并发布",
                    callback_data=f"approve:{index}",
                ),
                InlineKeyboardButton(
                    "❌ 拒绝",
                    callback_data=f"reject:{index}",
                ),
            ]
        ])

        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"第 {index + 1}/10 条待审核\n\n{format_content(item)}",
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )


async def generate_and_review(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text="正在采集新闻并生成 10 条内容，请稍候……",
        )
        items = await create_content()
        await send_for_review(context, items)
    except Exception as exc:
        logger.exception("生成内容失败")
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"本次内容生成失败：{str(exc)[:800]}",
        )


async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("你没有权限执行此操作。")
        return

    await update.message.reply_text("收到，正在生成内容……")
    asyncio.create_task(generate_and_review(context))


async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.from_user.id != ADMIN_USER_ID:
        await query.answer("你没有审核权限。", show_alert=True)
        return

    await query.answer()

    try:
        action, number = query.data.split(":")
        index = int(number)
        item = pending.get(index)

        if not item:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("这条内容已处理或已失效。")
            return

        if action == "approve":
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=format_content(item),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            result = "已通过并发布到群组。"
        else:
            result = "已拒绝，不会发布。"

        pending.pop(index, None)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(result)

    except Exception:
        logger.exception("审核处理失败")
        await query.message.reply_text("处理失败，请检查机器人日志。")


async def post_init(app: Application):
    await app.bot.set_webhook(
        url=f"{RENDER_URL}/{hashlib.sha256(BOT_TOKEN.encode()).hexdigest()}",
        secret_token=hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:40],
        allowed_updates=Update.ALL_TYPES,
    )

    app.job_queue.run_daily(
        generate_and_review,
        time=time(hour=9, minute=0, tzinfo=ZoneInfo("Asia/Singapore")),
        name="daily_content",
    )

    logger.info("机器人已启动，每天新加坡时间 09:00 生成内容。")


def main():
    webhook_path = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()
    secret_token = webhook_path[:40]

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("generate", generate_command))
    app.add_handler(CallbackQueryHandler(review_callback))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path,
        secret_token=secret_token,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
