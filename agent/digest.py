"""Scheduled WeChat briefing: collect new data over MCP, summarize with an LLM, email it.

Usage:
  python -m agent.digest            # collect since last run, summarize, send
  python -m agent.digest --dry-run  # print the email instead of sending
  python -m agent.digest --hours 24 # ignore saved state, look back N hours
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from agent.mcp_client import call_tool

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".digest_state.json"
LOCAL_TZ = ZoneInfo(os.environ.get("DIGEST_TIMEZONE", "America/Los_Angeles"))
DEFAULT_LOOKBACK_HOURS = 12
MAX_TRANSCRIPT_CHARS = 60_000

PROMPT = """你是我的私人微信秘书。下面是我微信里一段时间内的新内容（时间都是太平洋时间）。
我懒得看消息，请写一份中文简报邮件（纯文本，不要 Markdown 表格），要求：
1. 第一段直接说最要紧的事：需要我回复/行动/赴约的事情，带具体时间。
2. 然后按"私人/朋友"、"家人"、"@我的"、"求职相关"、"其他群"、"朋友圈"、"公众号"分节，没有内容的节省略。
3. 私聊和 @我 的要详细，写清谁、说了什么、是否需要我回应；群聊提炼要点，噪音和广告一句话带过。
4. 公众号挑值得读的写标题和一句话看点，附原文链接；其余列名字即可。
5. 不要编造，原文没有的信息不要补。
我的微信名：{my_names}

时间范围：{window}

{transcript}
"""


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), LOCAL_TZ).strftime("%m-%d %H:%M")


async def _tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    text = await call_tool(name, args)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"status": "error", "message": text[:500]}


async def collect(since: int, until: int) -> dict[str, Any]:
    status = await _tool("get_runtime_status", {})
    chats_payload = await _tool("get_recent_chats", {"limit": 200})
    chats = []
    for chat in chats_payload.get("chats", []):
        last = chat.get("last_message") or {}
        if int(last.get("create_time") or 0) < since:
            continue
        contact = chat.get("contact") or {}
        payload = await _tool("get_recent_messages", {"contact_name": contact.get("username"), "limit": 200})
        messages = [
            m for m in payload.get("messages", []) if since <= int(m.get("create_time") or 0) <= until
        ]
        if messages:
            chats.append({"contact": contact, "messages": messages})
    window = {"start_timestamp": since, "end_timestamp": until}
    return {
        "status": status,
        "chats": chats,
        "biz": await _tool("get_official_account_messages", {**window, "limit": 300}),
        "moments": await _tool("get_moments", {**window, "limit": 200}),
    }


def _my_names() -> list[str]:
    return [n.strip() for n in os.environ.get("DIGEST_MY_NAMES", "").split(",") if n.strip()]


def build_transcript(data: dict[str, Any]) -> tuple[str, dict[str, int]]:
    names = _my_names()
    lines: list[str] = []
    stats = {"chats": 0, "messages": 0, "mentions": 0, "articles": 0, "moments": 0}
    for chat in data["chats"]:
        contact = chat["contact"]
        kind = "群聊" if contact.get("is_chatroom") else "私聊"
        lines.append(f"\n## [{kind}] {contact.get('display_name')}")
        stats["chats"] += 1
        for m in chat["messages"]:
            text = (m.get("text") or "").replace("\n", " ").strip()
            if not text:
                text = f"[{m.get('type_name') or '非文本'}]"
            mention = any(f"@{n}" in text for n in names)
            stats["messages"] += 1
            stats["mentions"] += int(mention)
            flag = " (@我)" if mention else ""
            lines.append(f"{_fmt(m['create_time'])} {m.get('sender_display') or '?'}{flag}: {text[:800]}")

    moments = data.get("moments") or {}
    if moments.get("posts") or moments.get("new_post_notices") or moments.get("interactions"):
        lines.append("\n## [朋友圈]")
        for p in moments.get("posts", []):
            stats["moments"] += 1
            body = p.get("text") or p.get("link_title") or f"[{p.get('content_type_name')}]"
            lines.append(f"{_fmt(p['create_time'])} {p.get('author')}: {body[:500]} {p.get('location') or ''}")
        for n in moments.get("new_post_notices", []):
            lines.append(f"{_fmt(n['create_time'])} {n.get('author')} 发了新朋友圈（只有提醒，无正文）")
        for i in moments.get("interactions", []):
            lines.append(f"{_fmt(i['create_time'])} {i.get('from_display')} 互动了我的朋友圈: {i.get('text') or '[点赞]'}")

    biz = data.get("biz") or {}
    if biz.get("pushes"):
        lines.append("\n## [公众号]")
        for push in biz["pushes"]:
            for a in push.get("articles", []):
                stats["articles"] += 1
                digest = (a.get("digest") or "")[:120].replace("\n", " ")
                title = a["title"].replace("\n", " ")[:150]
                lines.append(f"{_fmt(push['create_time'])} {push.get('account')}: {title} | {digest} | {a.get('url')}")
            if not push.get("articles") and push.get("text"):
                lines.append(f"{_fmt(push['create_time'])} {push.get('account')}: {push['text'][:300]}")

    transcript = "\n".join(lines).strip()
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n...(内容过长已截断)"
    return transcript, stats


def summarize(prompt: str) -> str:
    timeout = httpx.Timeout(180)
    if os.environ.get("ANTHROPIC_API_KEY"):
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": os.environ.get("DIGEST_LLM_MODEL", "claude-sonnet-4-5"),
                "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return "".join(block.get("text", "") for block in resp.json().get("content", []))
    if os.environ.get("OPENAI_API_KEY"):
        resp = httpx.post(
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={
                "model": os.environ.get("DIGEST_LLM_MODEL", "gpt-4.1-mini"),
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    raise RuntimeError("No ANTHROPIC_API_KEY or OPENAI_API_KEY configured")


def send_email(subject: str, body: str) -> None:
    sender = os.environ["DIGEST_GMAIL_ADDRESS"]
    password = os.environ["DIGEST_GMAIL_APP_PASSWORD"].replace(" ", "")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = os.environ.get("DIGEST_TO", sender)
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)


def _load_since(hours: float | None, now: int) -> int:
    if hours is None and STATE_FILE.exists():
        try:
            return int(json.loads(STATE_FILE.read_text())["last_until"])
        except (ValueError, KeyError, json.JSONDecodeError):
            pass
    return now - int((hours or DEFAULT_LOOKBACK_HOURS) * 3600)


def _health_note(status: dict[str, Any]) -> str:
    text = json.dumps(status, ensure_ascii=False)
    if '"available": true' in text and '"chat_window_ready": true' in text:
        return ""
    return "⚠️ 微信可能掉线或数据库不可用，请打开 http://localhost:3000 检查/重新扫码。\n\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.digest")
    parser.add_argument("--dry-run", action="store_true", help="Print instead of sending; do not save state")
    parser.add_argument("--hours", type=float, default=None, help="Look back N hours (ignores saved state)")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env", override=True)
    now = int(time.time())
    since = _load_since(args.hours, now)
    window = f"{_fmt(since)} → {_fmt(now)}"

    try:
        data = asyncio.run(collect(since, now))
    except Exception as exc:  # noqa: BLE001 - still notify when MCP is down
        data = None
        body = f"⚠️ 无法连接 wechat-ai MCP（{type(exc).__name__}: {exc}）。\n容器或微信可能停了，请检查 http://localhost:3000"
        stats = {}
    if data is not None:
        transcript, stats = build_transcript(data)
        health = _health_note(data["status"])
        if not transcript:
            body = health + "这段时间没有新消息、公众号或朋友圈。"
        else:
            try:
                body = health + summarize(PROMPT.format(my_names="、".join(_my_names()), window=window, transcript=transcript))
            except Exception as exc:  # noqa: BLE001 - fall back to raw digest
                body = health + f"（AI 总结失败：{type(exc).__name__}: {exc}，以下是原始整理）\n\n{transcript}"

    subject = f"微信简报 {window}"
    if stats.get("mentions"):
        subject += f" · @我 {stats['mentions']} 条"

    if args.dry_run:
        print(subject)
        print("=" * 60)
        print(body)
        print("=" * 60, json.dumps(stats, ensure_ascii=False), file=sys.stderr)
        return 0

    send_email(subject, body)
    if data is not None:
        STATE_FILE.write_text(json.dumps({"last_until": now, "sent_at": now, "stats": stats}))
    print(f"sent: {subject} {json.dumps(stats, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
