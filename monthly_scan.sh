#!/bin/bash
# Monthly ContractWatch refresh. Reference implementation.
#
# Pulls the latest USASpending bulk archives for FY15-FY26, rebuilds the
# local SQLite database, runs the bulk reflag, regenerates web/data/latest.json
# and stats.json, and (optionally) deploys web/ to Cloudflare Pages via
# wrangler. Path-agnostic: invoked from any directory; resolves its own
# SCRIPT_DIR.
#
# Designed to be fired by launchd once a month. See
# launchd/com.contractwatch.plist.example for an installable template.
# Default schedule is the 8th of each month at 07:00 local (USASpending
# typically publishes the monthly archive snapshot on the 5th-6th, so the
# 8th gives a buffer for upstream data to settle).
#
# The codebase still ships scan.py (live USASpending API catch-up) for
# manual use when you want to pull a recent date range outside the monthly
# cadence; this scheduled job does not use it.
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

# Concurrent-run guard. If the previous refresh is still running, exit quietly.
if [ -e "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE" 2>/dev/null)" 2>/dev/null; then
  echo "[$(date -u +%FT%TZ)] another refresh is already running (PID $(cat "$LOCKFILE")), exiting"
  PHASE=concurrent_skip
  EXIT_CODE=0
  exit 0
fi
echo $$ > "$LOCKFILE"

# Disk space guard. Full bulk rebuild holds ~20 GB of archives in memory and
# writes a ~400 MB SQLite database. 25 GB headroom is comfortable.
FREE_GB=$(df -g "$SCRIPT_DIR" | awk 'NR==2 {print $4}')
if [ -z "$FREE_GB" ] || [ "$FREE_GB" -lt 25 ]; then
  echo "[$(date -u +%FT%TZ)] less than 25GB free on volume, aborting bulk refresh"
  PHASE=disk_full
  EXIT_CODE=2
  exit 2
fi

PHASE=bulk_load
echo "[$(date -u +%FT%TZ)] bulk load starting (FY15-FY26, pipelined)"
uv run python tools/bulk_loader.py tools/jobs.example.json
EXIT_CODE=$?
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "[$(date -u +%FT%TZ)] bulk load failed (exit $EXIT_CODE)"
  exit "$EXIT_CODE"
fi

PHASE=reflag
echo "[$(date -u +%FT%TZ)] reflagging full DB"
uv run python tools/reflag_all.py
EXIT_CODE=$?
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "[$(date -u +%FT%TZ)] reflag failed (exit $EXIT_CODE)"
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
echo "[$(date -u +%FT%TZ)] monthly refresh complete"
