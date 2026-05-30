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

# Source local .env if present so CONTRACTWATCH_NOTIFY_PHONE and other
# user-set vars are available. .env is gitignored.
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$SCRIPT_DIR/.env"
  set +a
fi

STATUS=/tmp/contractwatch.status.json
LOCKFILE=/tmp/contractwatch.lock
PHASE=startup
EXIT_CODE=0

write_status() {
  cat > "$STATUS" <<EOF
{"finished_at": "$(date -u +%FT%TZ)", "phase": "$1", "exit_code": $2, "host": "$(hostname -s)"}
EOF
}

notify_imessage() {
  # Send an iMessage to CONTRACTWATCH_NOTIFY_PHONE via macOS Messages.app.
  # Silent no-op if the env var is not set or osascript is unavailable
  # (i.e. not on macOS). Send failures are logged but never abort the script.
  local body="$1"
  if [ -z "${CONTRACTWATCH_NOTIFY_PHONE:-}" ]; then
    return 0
  fi
  if ! command -v osascript >/dev/null 2>&1; then
    return 0
  fi
  local escaped
  escaped=$(printf '%s' "$body" | python3 -c 'import sys; s=sys.stdin.read(); print(s.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n"), end="")' 2>/dev/null) || escaped="$body"
  osascript <<APPLESCRIPT >/dev/null 2>&1 || echo "[$(date -u +%FT%TZ)] notify_imessage send failed (non-fatal)"
tell application "Messages"
  set targetService to id of (1st service whose service type is iMessage)
  set targetBuddy to buddy "$CONTRACTWATCH_NOTIFY_PHONE" of service id targetService
  send "$escaped" to targetBuddy
end tell
APPLESCRIPT
}

cleanup() {
  rm -f "$LOCKFILE"
  write_status "$PHASE" "$EXIT_CODE"
  if [ "$EXIT_CODE" -ne 0 ] && [ "$PHASE" != "concurrent_skip" ]; then
    notify_imessage "ContractWatch refresh FAILED. phase=$PHASE exit=$EXIT_CODE host=$(hostname -s)"
  fi
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

# Disk space guard. --mode monthly downloads ~3-4 GB across 2 archives and
# holds them in memory during parse. 10 GB headroom covers that with margin
# for the WAL-mode DB write amplification. Use --mode initial for a full
# rebuild, which needs ~25 GB.
FREE_GB=$(df -g "$SCRIPT_DIR" | awk 'NR==2 {print $4}')
if [ -z "$FREE_GB" ] || [ "$FREE_GB" -lt 10 ]; then
  echo "[$(date -u +%FT%TZ)] less than 10GB free on volume, aborting bulk refresh"
  PHASE=disk_full
  EXIT_CODE=2
  exit 2
fi

PHASE=bulk_load
echo "[$(date -u +%FT%TZ)] bulk load starting (--mode monthly: prev FY + current FY)"
uv run python tools/bulk_loader.py --mode monthly
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
# Prefer a direct wrangler binary if available. Homebrew's node@24 keg-only
# install puts npx at /opt/homebrew/opt/node@24/bin/npx, which is NOT in
# launchd's default PATH. wrangler itself (when installed via `npm install -g`)
# lands in /opt/homebrew/bin and IS in launchd's PATH. Falling back to npx
# only when a direct wrangler binary isn't present keeps the scheduled job
# working under launchd's stripped environment.
if command -v wrangler >/dev/null 2>&1; then
  echo "[$(date -u +%FT%TZ)] deploying to Cloudflare Pages (project=$CF_PROJECT) via wrangler"
  wrangler pages deploy web --project-name="$CF_PROJECT" --commit-dirty=true
  EXIT_CODE=$?
elif command -v npx >/dev/null 2>&1; then
  echo "[$(date -u +%FT%TZ)] deploying to Cloudflare Pages (project=$CF_PROJECT) via npx wrangler"
  npx --yes wrangler pages deploy web --project-name="$CF_PROJECT" --commit-dirty=true
  EXIT_CODE=$?
else
  echo "[$(date -u +%FT%TZ)] neither wrangler nor npx available, skipping Cloudflare deploy"
  EXIT_CODE=0
fi
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "[$(date -u +%FT%TZ)] deploy failed (exit $EXIT_CODE)"
  exit "$EXIT_CODE"
fi

PHASE=review_queue
echo "[$(date -u +%FT%TZ)] building review queue (new awards vs prior snapshot)"
uv run python tools/build_review_queue.py
RQ_EXIT=$?
if [ "$RQ_EXIT" -ne 0 ]; then
  echo "[$(date -u +%FT%TZ)] review queue build failed (exit $RQ_EXIT), non-fatal"
fi

PHASE=git_sync
# Sync the two tracked dashboard JSON files (web/data/latest.json and
# web/data/stats.json) to GitHub so the repo stays in lockstep with what
# Cloudflare Pages just deployed. Without this step, every monthly refresh
# silently drifts the repo away from the live site. Fail-soft: a git
# failure here logs and notifies but does NOT set EXIT_CODE, because the
# product is already live and correct on Cloudflare; this is bookkeeping.
echo "[$(date -u +%FT%TZ)] syncing tracked dashboard JSON to git"
if [ -n "$(git status --porcelain web/data/latest.json web/data/stats.json 2>/dev/null)" ]; then
  SNAP_DATE=$(python3 -c 'import json; print(json.load(open("web/data/stats.json")).get("bulk_archive_snapshot_date","unknown"))' 2>/dev/null || echo unknown)
  FLAGGED=$(python3 -c 'import json; print(json.load(open("web/data/stats.json")).get("total_awards_flagged",0))' 2>/dev/null || echo 0)
  git add web/data/latest.json web/data/stats.json
  if git commit -m "Monthly refresh: $FLAGGED flagged awards (snapshot $SNAP_DATE)" >/dev/null 2>&1; then
    if git push origin main >/dev/null 2>&1; then
      echo "[$(date -u +%FT%TZ)] git sync complete: pushed snapshot $SNAP_DATE ($FLAGGED flagged)"
    else
      echo "[$(date -u +%FT%TZ)] git push FAILED. site is live on Cloudflare but repo is out of sync"
      notify_imessage "ContractWatch git push FAILED. Site is live but repo is out of sync. Snapshot $SNAP_DATE. Investigate: cd $SCRIPT_DIR && git status"
    fi
  else
    echo "[$(date -u +%FT%TZ)] git commit FAILED. site is live but repo is out of sync"
    notify_imessage "ContractWatch git commit FAILED. Site is live but repo is out of sync. Snapshot $SNAP_DATE. Investigate: cd $SCRIPT_DIR && git status"
  fi
else
  echo "[$(date -u +%FT%TZ)] no JSON changes to commit"
fi

PHASE=done
EXIT_CODE=0
# Touch the success marker. External monitors can alert if this file goes
# stale (any phase failure leaves it untouched).
date -u +%FT%TZ > /tmp/contractwatch.success.txt
echo "[$(date -u +%FT%TZ)] monthly refresh complete"

# Send a success summary via iMessage. Pull headline stats from the freshly
# regenerated stats.json. Silent no-op if CONTRACTWATCH_NOTIFY_PHONE unset.
STATS_FILE="$SCRIPT_DIR/web/data/stats.json"
if [ -f "$STATS_FILE" ]; then
  SUCCESS_MSG=$(python3 - <<PY 2>/dev/null
import json
try:
    with open("$STATS_FILE") as f:
        d = json.load(f)
    flagged = d.get("total_awards_flagged", 0)
    obl = d.get("displayed_obligation_total", 0) or 0
    scanned = d.get("total_awards_scanned", 0)
    snap = d.get("bulk_archive_snapshot_date", "?")
    queue_file = f"logs/review_queue_{snap}.json"
    queue_count = ""
    try:
        with open(queue_file) as q:
            qd = json.load(q)
        queue_count = f" {qd.get('review_queue_count', 0)} new to review."
    except Exception:
        pass
    print(f"ContractWatch refresh OK. {flagged} flagged across \${obl/1e9:.2f}B from {scanned:,} scanned. Snapshot {snap}.{queue_count}")
except Exception as e:
    print(f"ContractWatch refresh OK. Stats unreadable: {e}")
PY
)
  notify_imessage "$SUCCESS_MSG"
else
  notify_imessage "ContractWatch refresh OK. Host=$(hostname -s)."
fi
