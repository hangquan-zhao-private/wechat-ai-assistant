#!/usr/bin/env bash
# Write a local (gitignored) Cursor MCP config with your real token.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/vendor/wechat-ai/.env"
OUT="$ROOT/.cursor/mcp.json"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE"
  exit 1
fi

TOKEN="$(grep -E '^WECHAT_AI_MCP_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
if [[ -z "$TOKEN" || "$TOKEN" == "wechat" ]]; then
  echo "Invalid WECHAT_AI_MCP_TOKEN in $ENV_FILE"
  exit 1
fi

mkdir -p "$ROOT/.cursor"
cat > "$OUT" <<EOF
{
  "mcpServers": {
    "wechat-ai": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://127.0.0.1:8000/mcp",
        "--header",
        "Authorization:\${AUTH_HEADER}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer ${TOKEN}"
      }
    }
  }
}
EOF

# Ensure .cursor/mcp.json is ignored
if ! grep -q '^\.cursor/mcp\.json$' "$ROOT/.gitignore" 2>/dev/null; then
  printf '\n.cursor/mcp.json\n' >> "$ROOT/.gitignore"
fi

echo "Wrote $OUT"
echo "Reload Cursor MCP servers, then try: get_runtime_status"
