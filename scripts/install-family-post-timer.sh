#!/usr/bin/env bash
# Install (or update) the family news post at 07:30 and 19:30 Beijing time as a systemd user timer.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCHEDULE="${FAMILY_POST_ONCALENDAR:-*-*-* 07,19:30:00 Asia/Shanghai}"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR" "$ROOT/logs"

cat > "$UNIT_DIR/wechat-family-post.service" <<EOF
[Unit]
Description=WeChat family group news post

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=/bin/sh -c 'exec "$ROOT/scripts/family_post.sh" >> "$ROOT/logs/family_post.log" 2>&1'
EOF

cat > "$UNIT_DIR/wechat-family-post.timer" <<EOF
[Unit]
Description=WeChat family news post schedule

[Timer]
OnCalendar=$SCHEDULE
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now wechat-family-post.timer
loginctl enable-linger "$USER"
systemctl --user list-timers wechat-family-post.timer --no-pager
