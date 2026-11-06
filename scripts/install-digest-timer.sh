#!/usr/bin/env bash
# Install (or update) the twice-daily WeChat email briefing as a systemd user timer.
# Persistent=true makes runs missed during suspend/poweroff fire on wake/boot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCHEDULE="${DIGEST_ONCALENDAR:-*-*-* 08,18:00:00}"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR" "$ROOT/logs"

cat > "$UNIT_DIR/wechat-digest.service" <<EOF
[Unit]
Description=WeChat email digest

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=/bin/sh -c 'exec "$ROOT/scripts/digest.sh" >> "$ROOT/logs/digest.log" 2>&1'
EOF

cat > "$UNIT_DIR/wechat-digest.timer" <<EOF
[Unit]
Description=WeChat email digest schedule

[Timer]
OnCalendar=$SCHEDULE
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now wechat-digest.timer
# Keep the user manager alive after logout so the timer still fires.
loginctl enable-linger "$USER"

# Drop the legacy cron entry so the digest isn't sent twice.
if crontab -l 2>/dev/null | grep -q "# wechat-digest"; then
  { crontab -l | grep -v "# wechat-digest" || true; } | crontab -
  echo "Removed legacy crontab entry."
fi

systemctl --user list-timers wechat-digest.timer --no-pager
