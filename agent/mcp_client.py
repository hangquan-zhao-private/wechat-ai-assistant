"""Minimal Streamable HTTP MCP client for wechat-ai."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import load_dotenv


def load_settings(project_root: Path | None = None) -> dict[str, str]:
    root = project_root or Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    vendor_env = root / "vendor" / "wechat-ai" / ".env"
    if vendor_env.exists():
        load_dotenv(vendor_env, override=False)

    url = os.getenv("WECHAT_AI_MCP_URL", "http://127.0.0.1:8000/mcp").rstrip("/")
    token = os.getenv("WECHAT_AI_MCP_TOKEN", "").strip()
    if not token or token == "wechat":
        raise SystemExit(
            "WECHAT_AI_MCP_TOKEN missing or still placeholder. "
            "Run ./scripts/up.sh or set it in .env / vendor/wechat-ai/.env"
        )
    return {"url": url, "token": token}


def _content_to_text(result: Any) -> str:
    chunks: list[str] = []
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            chunks.append(text)
            continue
        data = getattr(item, "data", None)
        if data is not None:
            chunks.append(json.dumps(data, ensure_ascii=False, indent=2))
            continue
        chunks.append(str(item))
    if not chunks and getattr(result, "structuredContent", None) is not None:
        return json.dumps(result.structuredContent, ensure_ascii=False, indent=2)
    return "\n".join(chunks) if chunks else json.dumps(
        getattr(result, "model_dump", lambda: {"raw": str(result)})(),
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@asynccontextmanager
async def mcp_session(settings: dict[str, str] | None = None) -> AsyncIterator[Any]:
    settings = settings or load_settings()
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    headers = {"Authorization": f"Bearer {settings['token']}"}
    http_client = create_mcp_http_client(headers=headers)
    async with http_client:
        async with streamable_http_client(settings["url"], http_client=http_client) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> str:
    async with mcp_session() as session:
        result = await session.call_tool(name, arguments or {})
        return _content_to_text(result)


async def list_tools() -> list[str]:
    async with mcp_session() as session:
        listed = await session.list_tools()
        return sorted(t.name for t in listed.tools)
