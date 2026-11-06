#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r agent/requirements.txt
fi

# Prefer project .env; fall back to vendor runtime .env
if [[ ! -f .env && -f vendor/wechat-ai/.env ]]; then
  TOKEN="$(grep -E '^WECHAT_AI_MCP_TOKEN=' vendor/wechat-ai/.env | cut -d= -f2-)"
  cat > .env <<EOF
WECHAT_AI_MCP_URL=http://127.0.0.1:8000/mcp
WECHAT_AI_MCP_TOKEN=${TOKEN}
EOF
fi

exec .venv/bin/python -m agent "$@"
