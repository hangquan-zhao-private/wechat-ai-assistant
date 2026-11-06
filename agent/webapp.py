"""Local web UI for reading WeChat, sending messages, and posting the group news and Moments.

Usage: python -m agent.webapp  (serves http://127.0.0.1:8800)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from agent import group_post, moments_post
from agent.mcp_client import call_tool

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
HOST, PORT = "127.0.0.1", int(os.environ.get("WEB_UI_PORT", "8800"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
# Moments RPA drives the shared WeChat GUI; never run two at once.
MOMENTS_LOCK = asyncio.Lock()


async def _tool(name: str, args: dict[str, Any] | None = None) -> Any:
    raw = await call_tool(name, args or {})
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "error", "message": raw[:500]}


def _limit(request: Request, default: int, cap: int = 500) -> int:
    try:
        return max(1, min(int(request.query_params.get("limit", default)), cap))
    except ValueError:
        return default


async def index(_: Request) -> Response:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


async def me(_: Request) -> Response:
    # User info is redacted; every File Transfer message is from me, so take identity from there.
    data = await _tool("get_recent_messages", {"contact_name": "文件传输助手", "limit": 1})
    msg = (data.get("messages") or [{}])[0]
    return JSONResponse({"nickname": msg.get("sender_display", ""), "account": msg.get("sender_username", "")})


async def chats(request: Request) -> Response:
    return JSONResponse(await _tool("get_recent_chats", {"limit": _limit(request, 50)}))


async def contacts(request: Request) -> Response:
    q = request.query_params.get("q", "").strip()
    if not q:
        return JSONResponse({"contacts": []})
    return JSONResponse(await _tool("search_contacts", {"query": q, "limit": _limit(request, 30, 100)}))


async def messages(request: Request) -> Response:
    name = request.query_params.get("name", "").strip()
    if not name:
        return JSONResponse({"status": "error", "message": "name required"}, status_code=400)
    return JSONResponse(await _tool("get_recent_messages", {"contact_name": name, "limit": _limit(request, 80)}))


async def moments(request: Request) -> Response:
    return JSONResponse(await _tool("get_moments", {"limit": _limit(request, 50)}))


async def biz(request: Request) -> Response:
    return JSONResponse(await _tool("get_official_account_messages", {"limit": _limit(request, 60)}))


async def post_preview(_: Request) -> Response:
    load_dotenv(ROOT / ".env", override=True)
    group = group_post._group_name()
    text = await group_post.generate(group, time.time())
    return JSONResponse({"group": group, "text": text})


async def post_send(request: Request) -> Response:
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text or len(text) > 2000:
        return JSONResponse({"status": "error", "message": "text must be 1-2000 chars"}, status_code=400)
    group = group_post._group_name()
    return JSONResponse(await group_post.deliver(group, text, time.time()))


async def send_text(request: Request) -> Response:
    body = await request.json()
    name = str(body.get("name") or "").strip()
    text = str(body.get("text") or "").strip()
    if not name or not text or len(text) > 2000:
        return JSONResponse({"status": "error", "message": "name and 1-2000 chars of text required"}, status_code=400)
    return JSONResponse(await group_post.send(name, text))


async def send_file(request: Request) -> Response:
    body = await request.json()
    name = str(body.get("name") or "").strip()
    data = str(body.get("data") or "")
    file_name = str(body.get("file_name") or "file.bin")[:120]
    mime = str(body.get("mime") or "")
    if not name or not data or len(data) > 14_500_000:
        return JSONResponse({"status": "error", "message": "name and a file up to 10 MB required"}, status_code=400)
    if mime.startswith("image/"):
        tool, args = "send_image_msg", {"image_base64": data, "mime_type": mime, "file_name": file_name}
    else:
        tool, args = "send_file_msg", {"file_base64": data, "file_name": file_name}
    return JSONResponse(await _tool(tool, {"recipient_name": name, "wait_seconds": 30, **args}))


async def moments_topics(_: Request) -> Response:
    return JSONResponse({"topics": [{"id": k, "title": v["title"]} for k, v in moments_post.TOPICS.items()]})


async def moments_preview(request: Request) -> Response:
    topic = str((await request.json()).get("topic") or "")
    if topic not in moments_post.TOPICS:
        return JSONResponse({"status": "error", "message": "unknown topic"}, status_code=400)
    load_dotenv(ROOT / ".env", override=True)
    text = await asyncio.to_thread(moments_post.generate, topic, time.time())
    return JSONResponse({"topic": topic, "text": text})


async def moments_send(request: Request) -> Response:
    body = await request.json()
    topic = str(body.get("topic") or "")
    text = str(body.get("text") or "").strip()
    mode = "publish" if body.get("mode") == "publish" else "test"
    if not text or len(text) > 1500:
        return JSONResponse({"status": "error", "message": "text must be 1-1500 chars"}, status_code=400)
    if MOMENTS_LOCK.locked():
        return JSONResponse({"status": "error", "message": "另一条朋友圈正在发送"}, status_code=409)
    async with MOMENTS_LOCK:
        result = await asyncio.to_thread(moments_post.publish, text, mode)
    if mode == "publish" and topic in moments_post.TOPICS and result.get("status") in {"published", "unconfirmed"}:
        moments_post.save_state(topic, text, result["status"], time.time())
    return JSONResponse(result)


class LocalOnly:
    """Reject DNS-rebinding hosts and cross-site writes (custom header forces a CORS preflight)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            bad_host = headers.get("host") not in ALLOWED_HOSTS
            bad_write = scope["method"] != "GET" and headers.get("x-wechat-ui") != "1"
            if bad_host or bad_write:
                await JSONResponse({"status": "forbidden"}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = LocalOnly(
    Starlette(
        routes=[
            Route("/", index),
            Route("/api/me", me),
            Route("/api/chats", chats),
            Route("/api/contacts", contacts),
            Route("/api/messages", messages),
            Route("/api/moments", moments),
            Route("/api/biz", biz),
            Route("/api/send", send_text, methods=["POST"]),
            Route("/api/send-file", send_file, methods=["POST"]),
            Route("/api/group-post/preview", post_preview, methods=["POST"]),
            Route("/api/group-post/send", post_send, methods=["POST"]),
            Route("/api/moments-post/topics", moments_topics),
            Route("/api/moments-post/preview", moments_preview, methods=["POST"]),
            Route("/api/moments-post/send", moments_send, methods=["POST"]),
        ]
    )
)


def main() -> None:
    print(f"WeChat UI: http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
