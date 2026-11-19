
import os
import json
import logging
import hashlib
import asyncio
import html
from datetime import time
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import feedparser
from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =========================
# 基础配置
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
GROUP_CHAT_ID = int(os.environ["GROUP_CHAT_ID"])

PORT = int(os.environ.get("PORT", "10000"))
RENDER_URL = os.environ["RENDER_EXTERNAL_URL"].strip().rstrip("/")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

# 检查公网地址
parsed_url = urlparse(RENDER_URL)
if parsed_url.scheme != "https" or not parsed_url.netloc:
    raise RuntimeError(
        "RENDER_EXTERNAL_URL 必须是有效的 HTTPS 地址。"
    )

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# 待审核内容暂存在内存中
pending = {}


# =========================
# 新闻来源
# =========================

NEWS_FEEDS = [
    (
        "社会热点",
        "https://news.google.com/rss/search?q=社会新闻&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
    (
        "社会热点",
        "https://news.google.com/rss/search?q=社会热点&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
    (
        "国际新闻",
        "https://news.google.com/rss/search?q=国际新闻&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
    (
        "娱乐影视",
        "https://news.google.com/rss/search?q=娱乐影视&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
    (
        "科技资讯",
        "https://news.google.com/rss/search?q=科技新闻&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
    (
        "奇闻趣事",
        "https://news.google.com/rss/search?q=奇闻趣事&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
    (
        "真实故事",
        "https://news.google.com/rss/search?q=真实故事&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
]


# =========================
# Telegram 基础命令
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "机器人已启动！\n\n"
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
        "/help - 查看帮助\n"
        "/generate - 手动生成内容"
    )


# =========================
# 采集新闻
# =========================

def collect_news():
    items = []
    seen_urls = set()

    for category, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)

            for entry in feed.entries[:5]:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()

                if not title or not link:
                    continue

                if link in seen_urls:
                    continue

                seen_urls.add(link)

                items.append({
                    "category": category,
                    "title": title,
                    "url": link,
                })

        except Exception:
            logger.exception("新闻 RSS 获取失败")

    return items


# =========================
# AI 生成内容
# =========================

async def create_content():
    sources = collect_news()

    if len(sources) < 7:
        raise RuntimeError(
            "采集到的新闻来源不足 7 条，请稍后重试。"
        )

    prompt = f"""
你是中文新闻编辑和原创故事作者。

请生成严格的 JSON 数组，共 10 条内容。

其中 7 条是新闻摘要，必须从提供的新闻来源中选择，
不得编造新闻事实。

新闻分类数量：
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

新闻要求：
1. 必须使用提供的新闻来源标题和 URL。
2. 不得编造新闻事实或网址。
3. 用自己的话撰写摘要。
4. 每条摘要控制在 100 至 180 个汉字。
5. 保留对应新闻来源链接。

原创故事要求：
1. 每篇控制在 250 至 400 个汉字。
2. 必须标注 AI 原创故事。
3. 不得冒充真实事件或真实新闻。
4. 历史故事如涉及史实，应避免编造具体史实。

每项必须包含以下字段：
category、title、content、source_url、kind

新闻 kind 填 news。
原创故事 kind 填 original。
原创故事 source_url 填空字符串。

只输出 JSON 数组，不要输出 Markdown 代码块。

可用新闻来源：
{json.dumps(sources, ensure_ascii=False)}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "你必须遵守要求，只返回有效 JSON 数组。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.7,
    )

    raw = response.choices[0].message.content

    if not raw:
        raise RuntimeError("AI 没有返回内容。")

    raw = raw.strip()

    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    data = json.loads(raw)

    if not isinstance(data, list) or len(data) != 10:
        raise RuntimeError("AI 返回内容数量不正确，请重新生成。")

    required_fields = [
        "category",
        "title",
        "content",
        "source_url",
        "kind",
    ]

    for item in data:
        if not isinstance(item, dict):
            raise RuntimeError("AI 返回的数据格式不正确。")

        for field in required_fields:
            if field not in item:
                raise RuntimeError(
                    f"AI 返回内容缺少字段：{field}"
                )

        if item["kind"] not in ("news", "original"):
            raise RuntimeError("内容类型不正确。")

        if item["kind"] == "news":
            source_url = item["source_url"]
            parsed = urlparse(source_url)

            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise RuntimeError("新闻来源链接无效。")

    return data


# =========================
# 内容格式
# =========================

def format_content(item):
    kind = item.get("kind", "news")

    if kind == "original":
        label = "【AI 原创故事】"
    else:
        label = "【新闻摘要】"

    category = html.escape(str(item.get("category", "内容")))
    title = html.escape(str(item.get("title", "未命名")))
    content = html.escape(str(item.get("content", "")))

    text = (
        f"{label} {category}\n\n"
        f"<b>{title}</b>\n\n"
        f"{content}"
    )

    source = str(item.get("source_url", "")).strip()

    if source and kind == "news":
        safe_url = html.escape(source, quote=True)
        text += f'\n\n<a href="{safe_url}">查看新闻来源</a>'

    return text


# =========================
# 发送管理员审核
# =========================

async def send_for_review(
    context: ContextTypes.DEFAULT_TYPE,
    items,
):
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
            text=(
                f"第 {index + 1}/10 条待审核\n\n"
                f"{format_content(item)}"
            ),
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )


# =========================
# 生成并提交审核
# =========================

async def generate_and_review(
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text="正在采集新闻并生成 10 条内容，请稍候……",
        )

        items = await create_content()

        await send_for_review(context, items)

        logger.info("10 条内容已发送管理员审核。")

    except Exception as exc:
        logger.exception("生成内容失败")

        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=f"本次内容生成失败：{str(exc)[:800]}",
        )


async def generate_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text(
            "你没有权限执行此操作。"
        )
        return

    await update.message.reply_text(
        "收到，正在生成内容……"
    )

    asyncio.create_task(
        generate_and_review(context)
    )


# =========================
# 管理员审核按钮
# =========================

async def review_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query.from_user.id != ADMIN_USER_ID:
        await query.answer(
            "你没有审核权限。",
            show_alert=True,
        )
        return

    await query.answer()

    try:
        action, number = query.data.split(":")
        index = int(number)

        item = pending.get(index)

        if not item:
            await query.edit_message_reply_markup(
                reply_markup=None
            )
            await query.message.reply_text(
                "这条内容已处理或已失效。"
            )
            return

        if action == "approve":
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=format_content(item),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

            result = "已通过并发布到群组。"

        elif action == "reject":
            result = "已拒绝，不会发布。"

        else:
            return

        pending.pop(index, None)

        await query.edit_message_reply_markup(
            reply_markup=None
        )

        await query.message.reply_text(result)

    except Exception:
        logger.exception("审核处理失败")

        await query.message.reply_text(
            "处理失败，请检查机器人日志。"
        )


# =========================
# 启动后的定时任务
# =========================

async def post_init(app: Application):
    app.job_queue.run_daily(
        generate_and_review,
        time=time(
            hour=9,
            minute=0,
            tzinfo=ZoneInfo("Asia/Singapore"),
        ),
        name="daily_content",
    )

    logger.info(
        "机器人初始化完成，每天新加坡时间 09:00 生成内容。"
    )


# =========================
# 主程序
# =========================

def main():
    webhook_path = hashlib.sha256(
        BOT_TOKEN.encode()
    ).hexdigest()

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
    app.add_handler(
        CallbackQueryHandler(review_callback)
    )

    logger.info("正在启动 Telegram Webhook……")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path,
        webhook_url=f"{RENDER_URL}/{webhook_path}",
        secret_token=secret_token,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
