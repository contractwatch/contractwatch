#!/usr/bin/env python3
"""
Build the per-cycle review queue: newly flagged awards in this monthly
snapshot that have not been previously investigated.

Workflow (runs after monthly_scan.sh deploys the new latest.json):

  1. Find the most recent baseline snapshot in logs/snapshots/.
  2. Read current web/data/latest.json.
  3. Diff by usaspending_url to find awards new in this cycle.
  4. Cross-reference against logs/agent_verdicts.json. Drop any award whose
     recipient name was investigated in a prior cycle (any verdict). The
     structural filter already strips prior "safelist" verdicts before they
     reach latest.json, so this filter is mainly catching prior "keep"
     verdicts that were intentionally left on the dashboard.
  5. Write logs/review_queue_<snapshot_date>.json. Clean, agent-feedable.
  6. Save current latest.json as logs/snapshots/latest_<snapshot_date>.json
     so the next cycle has a baseline to diff against.

First-run behavior: if no prior snapshot exists, save current as the
baseline and exit with a "first cycle, no queue produced" message.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "web" / "data" / "latest.json"
STATS = ROOT / "web" / "data" / "stats.json"
SNAPSHOT_DIR = ROOT / "logs" / "snapshots"
VERDICTS = ROOT / "logs" / "agent_verdicts.json"


def find_latest_baseline():
    """Return Path of the most recent snapshot, or None if no snapshots exist."""
    if not SNAPSHOT_DIR.exists():
        return None
    snaps = sorted(SNAPSHOT_DIR.glob("latest_*.json"))
    return snaps[-1] if snaps else None


def award_key(award):
    """Unique-per-award identifier. usaspending_url is canonical."""
    url = award.get("usaspending_url")
    if url:
        return url
    return f"{award.get('piid','?')}|{award.get('uei','?')}"


def load_prior_recipients():
    """Set of recipient names that have ever been investigated by an agent."""
    if not VERDICTS.exists():
        return set()
    with open(VERDICTS) as f:
        data = json.load(f)
    return {v["recipient"].upper() for v in data.get("verdicts", [])}


def main():
    if not LATEST.exists():
        print(f"error: {LATEST} not found. Run export_json.py first.")
        return 1
    if not STATS.exists():
        print(f"error: {STATS} not found. Run export_json.py first.")
        return 1

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with open(LATEST) as f:
        current = json.load(f)
    with open(STATS) as f:
        stats = json.load(f)

    current_snap_date = stats.get("bulk_archive_snapshot_date", "unknown")
    current_awards = current.get("awards", [])

    baseline_path = find_latest_baseline()

    if baseline_path is None:
        # First cycle: save current as baseline, no diff possible.
        out = SNAPSHOT_DIR / f"latest_{current_snap_date}.json"
        with open(out, "w") as f:
            json.dump(current, f, indent=2)
        print(f"first cycle: saved baseline at {out.relative_to(ROOT)}")
        print(f"no review queue produced (need at least one prior snapshot to diff)")
        return 0

    with open(baseline_path) as f:
        baseline = json.load(f)
    baseline_awards = baseline.get("awards", [])
    baseline_snap_date = baseline_path.stem.replace("latest_", "")

    if baseline_snap_date == current_snap_date:
        print(f"baseline and current both at snapshot {current_snap_date}, nothing to diff")
        return 0

    baseline_keys = {award_key(a) for a in baseline_awards}
    new_awards = [a for a in current_awards if award_key(a) not in baseline_keys]

    prior_recipients = load_prior_recipients()
    queue = [
        a for a in new_awards
        if a.get("recipient", "").upper() not in prior_recipients
    ]
    skipped_due_to_prior_verdict = len(new_awards) - len(queue)

    queue.sort(key=lambda a: a.get("obligation", 0) or 0, reverse=True)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline_snapshot_date": baseline_snap_date,
        "current_snapshot_date": current_snap_date,
        "baseline_award_count": len(baseline_awards),
        "current_award_count": len(current_awards),
        "new_in_current": len(new_awards),
        "already_investigated_skipped": skipped_due_to_prior_verdict,
        "review_queue_count": len(queue),
        "review_queue": queue,
    }

    queue_path = ROOT / "logs" / f"review_queue_{current_snap_date}.json"
    with open(queue_path, "w") as f:
        json.dump(output, f, indent=2)

    # Roll the baseline forward for next cycle.
    next_baseline = SNAPSHOT_DIR / f"latest_{current_snap_date}.json"
    with open(next_baseline, "w") as f:
        json.dump(current, f, indent=2)

    print(f"baseline:  {baseline_snap_date} ({len(baseline_awards)} awards)")
    print(f"current:   {current_snap_date} ({len(current_awards)} awards)")
    print(f"new this cycle:                    {len(new_awards)}")
    print(f"already investigated (skipped):    {skipped_due_to_prior_verdict}")
    print(f"queued for review:                 {len(queue)}")
    print(f"")
    print(f"review queue: {queue_path.relative_to(ROOT)}")
    print(f"next baseline: {next_baseline.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
