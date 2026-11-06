"""Daily family news post, clearly signed as an AI assistant, for the parents' WeChat group.

Usage:
  python -m agent.family_post --dry-run           # print the post
  python -m agent.family_post --to 文件传输助手     # test-send somewhere else
  python -m agent.family_post                     # send to the family group
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from agent.group_post import recent_chat, require_env, send, write_post

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".family_post_state.json"
HOME_TZ = ZoneInfo("Asia/Shanghai")
CONTEXT_DAYS = 14
WEEKDAYS = "一二三四五六日"
EDITIONS = {
    "morning": {
        "edition": "早间版",
        "title": "早间家讯",
        "greeting": "早上好",
        "intro": "今天给你们整理了几条消息",
        "edition_focus": "早间版重点：今天的天气和出行、今天就能去的活动、昨晚到今早的新闻。",
    },
    "evening": {
        "edition": "晚间版",
        "title": "晚间家讯",
        "greeting": "晚上好",
        "intro": "今天辛苦了，给你们说说今天的新鲜事和明天的安排",
        "edition_focus": "晚间版重点：今天白天发生的新闻和趣事、明天的天气和出行提醒、明天和周末可以去的活动；语气更放松，可以多一条轻松有趣的本地小事。不要重复早间版已经发过的内容。",
    },
}

PROMPT = """你是「{bot}」，一个由儿子{son}设置的 AI 助手，每天早晚各一次给他的爸妈在微信家庭群里发新闻简报，这一条是{edition}。
现在是北京时间 {now}。家在{home}，老妈是家庭主妇、平时在{home}家里，空闲时间多；老爸在{work_city}的{company}上班。请先用网络搜索查最近 24 小时（最多 48 小时）的新闻，再写简报。
{edition_focus}

节假日：先搜索确认{date_check}是不是中国的传统节日（春节、元宵、端午、七夕、中秋、重阳、腊八、小年、除夕等）、法定节假日（元旦、清明、劳动节、国庆等）、二十四节气，或母亲节、父亲节。如果是，在问候语后加一句温暖的祝福（以 AI 小助手的身份，不冒充儿子），并把一条内容换成这个节日的习俗、吃什么或{home}当地的节日活动；法定假期前一天提醒放假安排和出行高峰。不是节日就不要硬加。

家庭群最近的聊天（只用来了解爸妈关心什么；不要复述、不要引用、不要评论里面的家事）：
{chat}

选题方向（每天挑 5-7 条，不必每类都有；{home}和{work_city}是重点）：
- {home}吃喝玩乐（给老妈，每天 1-2 条，最重要）：近期可以去的活动、市集、展览、演出、电影、公园景点（{spots}等）、美食和新开的店、社区和老年大学的免费活动、时令水果蔬菜。写清时间、地点、怎么去、要不要花钱，让人看完就能出门。
  这一类每天必须有：可以搜「{home} 活动 本周」「{home}文旅」「{home}发布」「{home}日报」等，时间范围放宽到未来 7 天内的活动；实在没有新活动，就按季节推荐一个{home}本地值得去的地方或当季吃的，写明这是推荐。
- {home}新鲜事：本地新闻、有趣的人和事、民生政策、交通、天气、本地产业（{industries}等）。
- {work_city}（老爸上班的地方，每天 1-2 条）：{work_city}的新闻、天气、交通管制、民生政策、城市建设、有意思的事；如果影响通勤或来回{home}（高铁、高速）要提醒。
- 健康：只用权威来源（国家卫健委、疾控中心、三甲医院医生、正规媒体），季节性提醒（流感、降温、饮食安全）、实用养生常识；如果最近有流传的养生谣言，可以辟谣。不推荐药物、偏方或保健品。
- 房产和理财：{home}、{work_city}和上海楼市政策、房贷利率、存款利率、国债和理财产品变化；只讲事实和对普通家庭的影响，不给买卖建议。
- {company}和服装行业：{company}公司的新闻（新品、经营、开店、奖项等），以及男装/服装纺织行业的重要动态；只写搜到的真实新闻，没有就不写。
- 科技和就业：国内科技公司、自动驾驶、机器人行业的大新闻，美国签证/H1B 政策的重大变化；有大事才写。
- 生活消费：新能源车、家电以旧换新、补贴政策等实用信息。

写法：
- 对象是 50-60 岁的爸妈：大白话、短句，少用英文和专业术语，必须用时解释一下。
- 每条先说「发生了什么」，再说「跟咱家有什么关系」。
- 不碰政治和争议话题；不提儿子的工作、求职、身份等私事；不以儿子的口吻说话，不要用「儿子觉得」之类的话。
- 不要写「老妈在家无聊」「老妈是家庭主妇」之类的话，直接推荐就好，比如「老妈周末可以约上朋友去……」。

格式（直接粘贴到微信，必须遵守）：
- 纯文本，不要 Markdown（不要 #、*、**、表格、链接），不要 emoji。
- 第一行：「【{bot} · {title}】{date}」
- 第二行：「老爸老妈{greeting}，我是{son}设置的 AI 小助手，{intro}：」
- 然后空一行，每条以「【类别】」开头写成一小段，每段 2-3 句，段与段之间空一行。
- 最后一行：「—— AI 自动整理，仅供参考；健康和投资问题请以医生、银行等专业意见为准。」
- 总长度不超过 750 字。
- 只写搜索到且能确认的事实，查不到就省略，不要编造，不要写来源链接或引用标记。
- 不要和上一次发的重复，除非有新进展。上一次发的：
{previous}
"""


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


async def generate(group: str, now: float) -> str:
    chat = await recent_chat(group, int(now - CONTEXT_DAYS * 86400))
    local = datetime.fromtimestamp(now, HOME_TZ)
    bot = os.environ.get("FAMILY_POST_BOT_NAME", "AI 小助手")
    slot = "morning" if local.hour < 14 else "evening"
    tomorrow = local + timedelta(days=1)
    date_check = f"今天（{local:%Y-%m-%d}）"
    if slot == "evening":
        date_check += f"或明天（{tomorrow:%Y-%m-%d}）"
    prompt = PROMPT.format(
        bot=bot,
        **EDITIONS[slot],
        date_check=date_check,
        son=require_env("FAMILY_POST_SON_NAME"),
        home=require_env("FAMILY_POST_HOME"),
        work_city=require_env("FAMILY_POST_WORK_CITY"),
        company=require_env("FAMILY_POST_COMPANY"),
        spots=os.environ.get("FAMILY_POST_LOCAL_SPOTS") or "本地公园、古镇、湖边绿道",
        industries=os.environ.get("FAMILY_POST_LOCAL_INDUSTRIES") or "本地特色产业",
        now=local.strftime("%Y-%m-%d %H:%M"),
        date=f"{local.month}月{local.day}日 周{WEEKDAYS[local.weekday()]}",
        chat=chat or "（最近没有聊天）",
        previous=_load_state().get("last_post") or "（无）",
    )
    return await asyncio.to_thread(write_post, prompt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.family_post")
    parser.add_argument("--dry-run", action="store_true", help="Print instead of sending")
    parser.add_argument("--to", help="Send to this chat instead of the family group (for testing)")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env", override=True)
    group = require_env("FAMILY_POST_TARGET")
    now = time.time()

    post = asyncio.run(generate(group, now))
    if not post:
        print("error: model returned an empty post", file=sys.stderr)
        return 1
    if args.dry_run:
        print(post)
        print(f"\n({len(post)} chars)", file=sys.stderr)
        return 0

    target = args.to or group
    result = asyncio.run(send(target, post))
    status = result.get("status")
    print(f"{datetime.fromtimestamp(now, HOME_TZ):%m-%d %H:%M} send to={target} status={status} chars={len(post)}")
    if status not in {"executed", "queued", "timeout"}:
        print(json.dumps(result, ensure_ascii=False)[:1000], file=sys.stderr)
        return 1
    if not args.to:
        # Save even when unconfirmed: never auto-resend a message that may have gone out.
        STATE_FILE.write_text(json.dumps({"last_post": post, "sent_at": int(now), "status": status}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
