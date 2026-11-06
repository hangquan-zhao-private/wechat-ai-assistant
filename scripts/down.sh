#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker compose -f "$ROOT/docker-compose.yml" down
cd "$ROOT/vendor/wechat-ai"
docker compose down
echo "wechat-ai and panel stopped."
