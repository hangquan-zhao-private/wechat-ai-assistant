"""Post a tech + basketball news briefing to a WeChat group, tailored to recent group chat.

Usage:
  python -m agent.group_post            # research, write, send to the group
  python -m agent.group_post --dry-run  # print the post instead of sending
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from agent.mcp_client import call_tool

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".group_post_state.json"
LOCAL_TZ = ZoneInfo(os.environ.get("DIGEST_TIMEZONE", "America/Los_Angeles"))
CONTEXT_HOURS = 72
MAX_CHAT_CHARS = 12_000

PROMPT = """你是微信群「{group}」里的新闻小助手。现在是 {now}（太平洋时间）。
请先用网络搜索查最新信息（优先过去 6-12 小时，其次 24 小时内），再写一条发到群里的中文简报。

群友最近在聊的内容（用来判断大家关心什么，不要复述聊天）：
{chat}

固定关注方向：
- 科技股：谷歌 GOOGL、微软 MSFT、Meta、亚马逊 AMZN、英伟达 NVDA、AMD、英特尔 INTC、苹果 AAPL、特斯拉 TSLA，以及群里最近提到的其他股票/比特币。查最新价格和涨跌幅，注明是收盘、盘前/盘后还是盘中。
- 科技新品与大厂动态：新产品发布、AI 模型、财报、裁员/招聘。
- NBA：最近一轮比赛结果和比分、詹姆斯 LeBron 的数据、群里常聊的球队和球员（76人、马刺、湖人、活塞、马克西、恩比德、布朗等），伤病和交易。
- 如有皇马/姆巴佩重要消息可一句带过。

写法：像群里一个懂行的朋友在聊天，让不看盘、不追球的人也一眼看懂，不要堆数据。
- 每节第一行先用一句大白话说结论（例如「今天科技股整体回调，芯片股最惨」）。
- 科技股：不要罗列所有股票，只挑 3-4 只变化最大或群里关心的，写成「名字 ↑/↓涨跌幅，一句原因」，例如「英特尔 ↓29%，财报指引太差吓跑了投资人」；价格只在有意义时提，平淡的股票合并成一句带过。
- 科技新品：说清是什么、对普通人有什么用，少用术语和型号。
- NBA：先说最有看点的一场（谁赢了、关键人物、为什么精彩），其他比赛一句一个比分；最后一句提今晚值得看的比赛。

格式要求（会直接粘贴到微信，必须遵守）：
- 纯文本，不要 Markdown（不要 #、*、**、表格、链接方括号），不要 emoji；可以用 ↑ ↓ 表示涨跌。
- 第一行：「科技+篮球快讯 {now_short}」。
- 分三节，标题用【科技股】【科技新品】【NBA】，节与节之间空一行；每节结论句之后 2-4 条，每条一行、以「- 」开头，口语化。
- 总长度不超过 700 字。
- 只写搜索到且能确认的事实，数字要准确；查不到的条目直接省略，不要写「没有搜索到」之类的说明，不要编造。
- 只输出要发到群里的正文：不要来源链接、引用标记、字数统计或任何解释。
- 不要和上一次发的内容重复，除非有新进展。上一次发的：
{previous}
"""


def _fmt(ts: float, pattern: str = "%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(int(ts), LOCAL_TZ).strftime(pattern)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set; add it to .env (see .env.example)")
    return value


def _group_name() -> str:
    return require_env("GROUP_POST_TARGET")


async def recent_chat(group: str, since: int) -> str:
    raw = await call_tool("get_recent_messages", {"contact_name": group, "limit": 300})
    try:
        messages = json.loads(raw).get("messages", [])
    except json.JSONDecodeError:
        return ""
    lines = []
    for m in messages:
        if int(m.get("create_time") or 0) < since:
            continue
        text = (m.get("text") or "").replace("\n", " ").strip()
        if text:
            lines.append(f"{_fmt(m['create_time'])} {m.get('sender_display') or '?'}: {text[:200]}")
    return "\n".join(lines)[-MAX_CHAT_CHARS:]


def write_post(prompt: str) -> str:
    resp = httpx.post(
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/responses",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={
            "model": os.environ.get("GROUP_POST_MODEL", "gpt-5.5"),
            "tools": [{"type": "web_search"}],
            "input": prompt,
        },
        timeout=httpx.Timeout(300),
    )
    resp.raise_for_status()
    texts = [
        part.get("text", "")
        for item in resp.json().get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    return clean("\n".join(texts))


def clean(text: str) -> str:
    text = re.sub(r"\s*\(\[[^\]]*\]\([^)]*\)\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]*\)", r"\1", text)
    # Search tool citation markers, e.g. "citeturn578248finance1" wrapped in private-use chars.
    text = re.sub(r"[\ue000-\uf8ff]*cite(?:turn\d+\w+)+[\ue000-\uf8ff]*", "", text)
    text = re.sub(r"[\ue000-\uf8ff]", "", text)
    text = re.sub(r"[*#`]", "", text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


async def send(group: str, text: str) -> dict[str, Any]:
    raw = await call_tool("send_text_msg", {"recipient_name": group, "message": text, "wait_seconds": 30})
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "error", "message": raw[:500]}


async def generate(group: str, now: float) -> str:
    chat = await recent_chat(group, int(now - CONTEXT_HOURS * 3600))
    prompt = PROMPT.format(
        group=group,
        now=_fmt(now, "%Y-%m-%d %H:%M"),
        now_short=_fmt(now),
        chat=chat or "（最近没有聊天）",
        previous=_load_state().get("last_post") or "（无）",
    )
    return await asyncio.to_thread(write_post, prompt)


async def deliver(group: str, post: str, now: float) -> dict[str, Any]:
    result = await send(group, post)
    if result.get("status") in {"executed", "queued", "timeout"}:
        # Save even when unconfirmed: never auto-resend a message that may have gone out.
        STATE_FILE.write_text(
            json.dumps({"last_post": post, "sent_at": int(now), "status": result["status"]}, ensure_ascii=False)
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.group_post")
    parser.add_argument("--dry-run", action="store_true", help="Print instead of sending; do not save state")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env", override=True)
    group = _group_name()
    now = time.time()

    post = asyncio.run(generate(group, now))
    if not post:
        print("error: model returned an empty post", file=sys.stderr)
        return 1

    if args.dry_run:
        print(post)
        print(f"\n({len(post)} chars)", file=sys.stderr)
        return 0

    result = asyncio.run(deliver(group, post, now))
    status = result.get("status")
    print(f"{_fmt(now)} send status={status} action_id={result.get('action_id')} chars={len(post)}")
    if status in {"executed", "queued", "timeout"}:
        return 0
    print(json.dumps(result, ensure_ascii=False)[:1000], file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
