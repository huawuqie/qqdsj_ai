
import os
import json
import logging
import uuid
from datetime import time
from zoneinfo import ZoneInfo

import feedparser
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# -------------------------
# 基础设置
# -------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "")

TIMEZONE = ZoneInfo("Asia/Singapore")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# 暂存等待管理员审核的内容
PENDING = {}


# -------------------------
# 权限检查
# -------------------------

def is_admin(update: Update) -> bool:
    if not ADMIN_USER_ID:
        return False

    user = update.effective_user
    return bool(user and str(user.id) == ADMIN_USER_ID)


# -------------------------
# RSS 新闻采集
# -------------------------

NEWS_SEARCHES = [
    ("社会热点", "社会热点 新闻"),
    ("社会热点", "国内社会 民生 新闻"),
    ("国际新闻", "国际新闻 world news"),
    ("娱乐影视", "娱乐 影视 新闻"),
    ("科技资讯", "科技 互联网 新闻"),
    ("奇闻趣事", "奇闻 趣事"),
    ("真实故事", "真实事件 人物故事"),
]


def collect_news():
    articles = []

    for category, keyword in NEWS_SEARCHES:
        url = (
            "https://news.google.com/rss/search?q="
            + keyword.replace(" ", "+")
            + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        )

        try:
            feed = feedparser.parse(url)

            for item in feed.entries[:5]:
                title = item.get("title", "").strip()
                link = item.get("link", "").strip()
                summary = item.get("summary", "").strip()

                if title and link:
                    articles.append({
                        "category": category,
                        "title": title,
                        "summary": summary[:1200],
                        "url": link,
                    })

        except Exception:
            logger.exception("新闻采集失败：%s", keyword)

    # 按标题去重
    unique = []
    seen = set()

    for item in articles:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


# -------------------------
# AI 生成新闻和原创故事
# -------------------------

def generate_contents():
    if not OPENAI_API_KEY:
        raise RuntimeError("还没有设置 OPENAI_API_KEY")

    news = collect_news()

    if len(news) < 7:
        raise RuntimeError(
            f"目前只采集到 {len(news)} 条新闻，"
            "不足以生成 7 条有来源的新闻。"
        )

    client = OpenAI(api_key=OPENAI_API_KEY)

    source_data = json.dumps(
        news[:35],
        ensure_ascii=False,
    )

    prompt = f"""
你是一名中文新闻编辑和原创故事作者。

请生成恰好 10 条中文内容，返回合法 JSON 数组，不要输出其他文字。

内容安排：
1. 社会热点新闻 2 条
2. 国际新闻 1 条
3. 娱乐影视新闻 1 条
4. 科技资讯 1 条
5. 奇闻趣事 1 条
6. 真实故事 1 条
7. AI 原创悬疑故事 1 条
8. AI 原创情感故事 1 条
9. AI 原创历史故事 1 条

新闻规则：
- 新闻只能依据我提供的新闻资料。
- 不得编造人物、时间、地点、数字或事件。
- 新闻资料不足时，不得把推测写成事实。
- 每条新闻必须使用资料中对应的原文链接。
- 用自己的语言整理，不要照搬原文。
- 真实故事也必须有来源支持；没有可靠资料就不要编造。

原创故事规则：
- 悬疑、情感、历史故事可以原创。
- 原创内容必须标记为“AI原创故事”。
- 不得把虚构故事伪装成真实新闻。

每条内容必须包含：
category：内容类别
title：标题
body：正文
source_url：新闻原文链接；原创故事填写空字符串
content_type：新闻 或 AI原创故事

每条正文约 250 至 450 个中文字符。
标题要清楚、吸引人，不要夸大事实。

新闻资料：
{source_data}
"""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": "你必须严格遵守事实来源，并只返回合法 JSON。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.7,
    )

    result = response.choices[0].message.content.strip()

    if result.startswith("```"):
        result = result.replace("```json", "", 1)
        result = result.replace("```", "").strip()

    contents = json.loads(result)

    if not isinstance(contents, list) or len(contents) != 10:
        raise RuntimeError("AI 返回的内容不是 10 条，请重新生成。")

    return contents


# -------------------------
# 发送给管理员审核
# -------------------------

async def send_for_review(bot, contents):
    if not ADMIN_USER_ID:
        raise RuntimeError("还没有设置管理员 Telegram ID")

    admin_id = int(ADMIN_USER_ID)

    for item in contents:
        content_id = str(uuid.uuid4())[:12]
        PENDING[content_id] = item

        source = item.get("source_url", "")
        text = (
            f"【待审核内容】\n"
            f"类别：{item.get('category', '')}\n"
            f"类型：{item.get('content_type', '')}\n\n"
            f"{item.get('title', '')}\n\n"
            f"{item.get('body', '')}\n\n"
        )

        if source:
            text += f"新闻来源：\n{source}"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ 审核通过并发布",
                    callback_data=f"approve:{content_id}",
                ),
                InlineKeyboardButton(
                    "❌ 拒绝",
                    callback_data=f"reject:{content_id}",
                ),
            ]
        ])

        await bot.send_message(
            chat_id=admin_id,
            text=text[:4000],
            reply_markup=keyboard,
        )


# -------------------------
# 审核按钮处理
# -------------------------

async def review_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not ADMIN_USER_ID or str(query.from_user.id) != ADMIN_USER_ID:
        await query.answer("你没有审核权限。", show_alert=True)
        return

    action, content_id = query.data.split(":", 1)
    item = PENDING.get(content_id)

    if not item:
        await query.edit_message_text("这条内容已处理或已失效。")
        return

    if action == "reject":
        PENDING.pop(content_id, None)
        await query.edit_message_text("已拒绝这条内容。")
        return

    if action == "approve":
        if not GROUP_CHAT_ID:
            await query.answer("还没有设置群组 ID。", show_alert=True)
            return

        source = item.get("source_url", "")
        text = (
            f"{item.get('title', '')}\n\n"
            f"{item.get('body', '')}"
        )

        if source:
            text += f"\n\n新闻来源：{source}"
        else:
            text += "\n\n（AI原创故事）"

        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=text[:4000],
        )

        PENDING.pop(content_id, None)
        await query.edit_message_text("✅ 已审核通过并发布到群组。")


# -------------------------
# 生成今日 10 条内容
# -------------------------

async def generate_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):
        await update.message.reply_text(
            "此功能仅管理员可以使用。"
        )
        return

    await update.message.reply_text(
        "正在采集新闻并生成 10 条内容，请稍候……"
    )

    try:
        contents = generate_contents()
        await send_for_review(context.bot, contents)

        await update.message.reply_text(
            "已生成 10 条内容，并发送给你逐条审核。"
        )

    except Exception as error:
        logger.exception("生成内容失败")
        await update.message.reply_text(
            f"生成失败：{str(error)[:800]}"
        )


# -------------------------
# 每日定时生成
# -------------------------

async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        contents = generate_contents()
        await send_for_review(context.bot, contents)
        logger.info("每日 10 条内容已发送审核")

    except Exception:
        logger.exception("每日自动生成失败")

        if ADMIN_USER_ID:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text="今日新闻或故事生成失败，请检查机器人日志和 API 设置。",
            )


# -------------------------
# 基础命令
# -------------------------

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "你好！我是你的 AI 新闻与故事机器人。\n\n"
        "使用 /id 查看 Telegram 用户 ID 和群组 ID。\n"
        "管理员可以使用 /generate 生成今日 10 条内容。"
    )


async def id_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        f"你的 Telegram 用户 ID：{user_id}\n"
        f"当前聊天 ID：{chat_id}\n\n"
        "请保存这两个数字，后续部署时需要使用。"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "/start - 启动机器人\n"
        "/id - 查看用户 ID 和聊天 ID\n"
        "/generate - 立即生成 10 条内容（管理员）"
    )


# -------------------------
# 启动机器人
# -------------------------

async def post_init(application: Application):
    if not BOT_TOKEN:
        raise RuntimeError("缺少 BOT_TOKEN")

    if not OPENAI_API_KEY:
        logger.warning("尚未设置 OPENAI_API_KEY，暂时不能生成内容")

    if ADMIN_USER_ID and OPENAI_API_KEY:
        application.job_queue.run_daily(
            daily_job,
            time=time(hour=9, minute=0, tzinfo=TIMEZONE),
            name="daily_content_generation",
        )
        logger.info("每日 09:00（新加坡时间）自动生成任务已启用")
    else:
        logger.warning(
            "管理员 ID 或 AI API Key 尚未设置，自动生成任务未启用"
        )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("请先设置 BOT_TOKEN 环境变量")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("generate", generate_command))
    application.add_handler(CallbackQueryHandler(review_callback))

    logger.info("Telegram AI 机器人启动中……")
    application.run_polling()


if __name__ == "__main__":
    main()
