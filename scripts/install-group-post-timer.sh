#!/usr/bin/env bash
# Install (or update) the twice-daily (08:00, 20:00) group news post as a systemd user timer.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCHEDULE="${GROUP_POST_ONCALENDAR:-*-*-* 08,20:00:00}"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR" "$ROOT/logs"

cat > "$UNIT_DIR/wechat-group-post.service" <<EOF
[Unit]
Description=WeChat group tech + NBA news post

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=/bin/sh -c 'exec "$ROOT/scripts/group_post.sh" >> "$ROOT/logs/group_post.log" 2>&1'
EOF

cat > "$UNIT_DIR/wechat-group-post.timer" <<EOF
[Unit]
Description=WeChat group news post schedule

[Timer]
OnCalendar=$SCHEDULE
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now wechat-group-post.timer
loginctl enable-linger "$USER"
systemctl --user list-timers wechat-group-post.timer --no-pager
