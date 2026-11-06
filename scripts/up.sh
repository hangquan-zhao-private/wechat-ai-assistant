#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$ROOT/vendor/wechat-ai"

if [[ ! -f "$RUNTIME/.env" ]]; then
  echo "Missing $RUNTIME/.env — copy .env.example and set secrets first."
  exit 1
fi

mkdir -p "$RUNTIME/config" "$RUNTIME/plugins"
if [[ ! -f "$RUNTIME/config/config.yaml" ]]; then
  cp "$RUNTIME/config.example.yaml" "$RUNTIME/config/config.yaml"
fi

TOKEN="$(grep -E '^WECHAT_AI_MCP_TOKEN=' "$RUNTIME/.env" | cut -d= -f2-)"
# Only refresh the MCP lines; keep digest/OpenAI keys already in .env.
touch "$ROOT/.env"
sed -i -E '/^WECHAT_AI_MCP_(URL|TOKEN)=/d' "$ROOT/.env"
cat >> "$ROOT/.env" <<EOF
WECHAT_AI_MCP_URL=http://127.0.0.1:8000/mcp
WECHAT_AI_MCP_TOKEN=${TOKEN}
EOF

cd "$RUNTIME"
echo "Starting wechat-ai..."
docker compose up -d --build

echo "Starting panel..."
PANEL_UID="$(id -u)" PANEL_GID="$(id -g)" docker compose -f "$ROOT/docker-compose.yml" up -d --build

LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"

echo
echo "Services:"
echo "  WeChat desktop: http://localhost:3000  /  https://localhost:3001"
echo "  Dashboard:      http://localhost:8000/dashboard"
echo "  Panel:          http://127.0.0.1:8800"
echo "  MCP:            http://localhost:8000/mcp"
echo "  Import port:    host TCP ${WECHAT_IMPORT_PORT:-8015} -> container 8015"
if [[ -n "${LAN_IP}" ]]; then
  echo "  LAN IP:         ${LAN_IP}"
fi
echo
echo "Phone must be on the same Wi-Fi. Open import UI so WeChat listens on 8015."
