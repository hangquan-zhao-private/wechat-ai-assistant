#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/vendor/wechat-ai"

echo "== docker =="
docker compose ps || true
echo
echo "== import port 8015 =="
if ss -lntp 2>/dev/null | grep -q ':8015'; then
  echo "host listening on 8015"
  ss -lntp | grep ':8015' || true
else
  echo "8015 not on host yet (open WeChat chat-history import UI first)"
fi
echo
echo "== urls =="
echo "  Desktop:   http://localhost:3000  /  https://localhost:3001"
echo "  Dashboard: http://localhost:8000/dashboard"
echo "  MCP:       http://localhost:8000/mcp"
