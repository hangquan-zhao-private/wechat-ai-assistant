"""Personal read-only WeChat helper over wechat-ai MCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from agent.mcp_client import call_tool, list_tools


def _print(text: str) -> None:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        print(text)
        return
    print(json.dumps(parsed, ensure_ascii=False, indent=2))


async def _run(name: str, arguments: dict[str, Any] | None = None) -> int:
    text = await call_tool(name, arguments)
    _print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent",
        description="Personal WeChat-AI helper (read-only by default).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Runtime / DB / RPA status")
    sub.add_parser("whoami", help="Current WeChat identity (redacted)")
    sub.add_parser("tools", help="List MCP tools")

    c = sub.add_parser("contacts", help="Search contacts / rooms")
    c.add_argument("query")
    c.add_argument("--limit", type=int, default=20)

    d = sub.add_parser("contact", help="Contact / room detail")
    d.add_argument("name", help="Nickname / remark / WeChat ID")

    ch = sub.add_parser("chats", help="Recent chats")
    ch.add_argument("--limit", type=int, default=20)

    m = sub.add_parser("messages", help="Recent messages for a contact/room")
    m.add_argument("name", help="Nickname / remark / WeChat ID")
    m.add_argument("--limit", type=int, default=30)

    s = sub.add_parser("search", help="Search text in one contact/room")
    s.add_argument("name", help="Contact or room to search in")
    s.add_argument("keyword", nargs="?", default="", help="Keyword (optional if using time range)")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--start", type=int, default=None, help="Unix start timestamp")
    s.add_argument("--end", type=int, default=None, help="Unix end timestamp")

    sum_p = sub.add_parser("summary", help="LLM-friendly chat context")
    sum_p.add_argument("name", help="Nickname / remark / WeChat ID")
    sum_p.add_argument("--limit", type=int, default=40)

    for name, help_text, default_limit in (
        ("moments", "Cached Moments (朋友圈) posts, notices, likes/comments", 50),
        ("biz", "Official-account (公众号) pushes and articles", 100),
    ):
        f = sub.add_parser(name, help=help_text)
        f.add_argument("name", nargs="?", default="", help="Optional contact / account filter")
        f.add_argument("--limit", type=int, default=default_limit)
        f.add_argument("--start", type=int, default=None, help="Unix start timestamp")
        f.add_argument("--end", type=int, default=None, help="Unix end timestamp")

    raw = sub.add_parser("call", help="Call any MCP tool by name (advanced)")
    raw.add_argument("tool")
    raw.add_argument(
        "--args",
        default="{}",
        help='JSON object of arguments, e.g. \'{"query":"张三"}\'',
    )
    return p


async def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "status":
        return await _run("get_runtime_status")
    if args.cmd == "whoami":
        return await _run("get_wechat_user_info")
    if args.cmd == "tools":
        for name in await list_tools():
            print(name)
        return 0
    if args.cmd == "contacts":
        return await _run(
            "search_contacts",
            {"query": args.query, "limit": args.limit},
        )
    if args.cmd == "contact":
        return await _run("get_contact_detail", {"contact_name": args.name})
    if args.cmd == "chats":
        return await _run("get_recent_chats", {"limit": args.limit})
    if args.cmd == "messages":
        return await _run(
            "get_recent_messages",
            {"contact_name": args.name, "limit": args.limit},
        )
    if args.cmd == "search":
        payload: dict[str, Any] = {
            "contact_name": args.name,
            "limit": args.limit,
        }
        if args.keyword:
            payload["query"] = args.keyword
        if args.start is not None:
            payload["start_timestamp"] = args.start
        if args.end is not None:
            payload["end_timestamp"] = args.end
        return await _run("search_text_messages", payload)
    if args.cmd == "summary":
        return await _run(
            "get_chat_summary_context",
            {"contact_name": args.name, "limit": args.limit},
        )
    if args.cmd in ("moments", "biz"):
        payload = {"limit": args.limit}
        if args.name:
            payload["contact_name"] = args.name
        if args.start is not None:
            payload["start_timestamp"] = args.start
        if args.end is not None:
            payload["end_timestamp"] = args.end
        tool = "get_moments" if args.cmd == "moments" else "get_official_account_messages"
        return await _run(tool, payload)
    if args.cmd == "call":
        try:
            payload = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"Invalid --args JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(payload, dict):
            print("--args must be a JSON object", file=sys.stderr)
            return 2
        return await _run(args.tool, payload)
    print(f"Unknown command: {args.cmd}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - surface MCP/network errors to CLI
        print(f"error: {exc}", file=sys.stderr)
        return 1
