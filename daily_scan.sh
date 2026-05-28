#!/bin/bash
# Daily ContractWatch update. Reference implementation.
#
# Catches up new awards from USASpending into the local SQLite DB,
# regenerates web/data/latest.json and stats.json, and (optionally)
# deploys web/ to Cloudflare Pages via wrangler. Path-agnostic: invoked
# from any directory; resolves its own SCRIPT_DIR.
#
# Designed to be fired by launchd once a day. See
# launchd/com.contractwatch.plist.example for an installable template.
# Edit at will; forkers may want to drop the wrangler step entirely.
#
# Status reporting: writes /tmp/contractwatch.status.json after every run
# with the phase that finished and the exit code, plus a success marker at
# /tmp/contractwatch.success.txt on full success. External monitoring can
# alert when either file goes stale.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

STATUS=/tmp/contractwatch.status.json
LOCKFILE=/tmp/contractwatch.lock
PHASE=startup
EXIT_CODE=0

write_status() {
  cat > "$STATUS" <<EOF
{"finished_at": "$(date -u +%FT%TZ)", "phase": "$1", "exit_code": $2, "host": "$(hostname -s)"}
EOF
}

cleanup() {
  rm -f "$LOCKFILE"
  write_status "$PHASE" "$EXIT_CODE"
}
trap cleanup EXIT

# Concurrent-run guard. If the previous scan is still running, exit quietly.
if [ -e "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE" 2>/dev/null)" 2>/dev/null; then
  echo "[$(date -u +%FT%TZ)] another scan is already running (PID $(cat "$LOCKFILE")), exiting"
  PHASE=concurrent_skip
  EXIT_CODE=0
  exit 0
fi
echo $$ > "$LOCKFILE"

# Disk space guard. DB grows over time, leave headroom.
FREE_GB=$(df -g "$SCRIPT_DIR" | awk 'NR==2 {print $4}')
if [ -z "$FREE_GB" ] || [ "$FREE_GB" -lt 5 ]; then
  echo "[$(date -u +%FT%TZ)] less than 5GB free on volume, aborting"
  PHASE=disk_full
  EXIT_CODE=2
  exit 2
fi

PHASE=scan
echo "[$(date -u +%FT%TZ)] catch-up scan starting"
uv run python scan.py --catch-up
EXIT_CODE=$?
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "[$(date -u +%FT%TZ)] scan failed (exit $EXIT_CODE)"
  exit "$EXIT_CODE"
fi

PHASE=export
echo "[$(date -u +%FT%TZ)] regenerating static JSON"
uv run python export_json.py
EXIT_CODE=$?
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "[$(date -u +%FT%TZ)] export failed (exit $EXIT_CODE)"
  exit "$EXIT_CODE"
fi

PHASE=deploy
CF_PROJECT="${CONTRACTWATCH_CF_PROJECT:-contractwatch}"
if command -v wrangler >/dev/null 2>&1 || command -v npx >/dev/null 2>&1; then
  echo "[$(date -u +%FT%TZ)] deploying to Cloudflare Pages (project=$CF_PROJECT)"
  npx --yes wrangler pages deploy web --project-name="$CF_PROJECT" --commit-dirty=true
  EXIT_CODE=$?
  if [ "$EXIT_CODE" -ne 0 ]; then
    echo "[$(date -u +%FT%TZ)] deploy failed (exit $EXIT_CODE)"
    exit "$EXIT_CODE"
  fi
else
  echo "[$(date -u +%FT%TZ)] wrangler not available, skipping Cloudflare deploy"
fi

PHASE=done
EXIT_CODE=0
# Touch the success marker. External monitors can alert if this file goes
# stale (any phase failure leaves it untouched).
date -u +%FT%TZ > /tmp/contractwatch.success.txt
echo "[$(date -u +%FT%TZ)] daily update complete"
