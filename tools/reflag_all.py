#!/usr/bin/env python3
"""Re-run all flags and structural filter against every award already in the DB.

No API calls. Local SQLite only. Strategy:

  1. One bulk SQL query per flag pulls the candidate award rows. The "no prior
     federal contracts for this UEI" check is a NOT EXISTS subquery against the
     awards table, served by idx_awards_uei.
  2. The structural filter runs in Python against the in-memory context for
     each candidate. No DB calls in the inner loop.
  3. Flags table is replaced inside a single transaction.

Backs up the flags table first.

Usage:
    uv run python reflag_all.py
    uv run python reflag_all.py --dry-run   # candidate counts only, no writes
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import (
    DB_PATH, today,
    CRITICAL_SOLE_SOURCE_MIN, FIRST_LARGE_AWARD_MIN, FLAG_META,
)
from engine.flags import (
    ONE_OFFER_MIN_OBLIGATION, COMPETITIVE_CODES,
    f01_detail, f02_detail, f03_detail,
)
from engine.structural_filter import apply_structural_filter

# Shared NOT EXISTS subquery: this UEI has no awards before action_date.
NO_PRIOR_AWARDS = """
  NOT EXISTS (
    SELECT 1 FROM awards b
    WHERE b.recipient_uei = a.recipient_uei
      AND b.action_date < a.action_date
  )
"""

F01_SQL = f"""
SELECT a.* FROM awards a
WHERE a.current_total_value_of_award >= ?
  AND a.sole_source = 1
  AND a.recipient_uei IS NOT NULL
  AND a.action_date IS NOT NULL
  AND {NO_PRIOR_AWARDS}
"""

F02_SQL = f"""
SELECT a.* FROM awards a
WHERE a.current_total_value_of_award >= ?
  AND (a.sole_source IS NULL OR a.sole_source = 0)
  AND a.number_of_offers = 1
  AND TRIM(COALESCE(a.competition_type, '')) IN ({",".join("?" * len(COMPETITIVE_CODES))})
  AND a.recipient_uei IS NOT NULL
  AND a.action_date IS NOT NULL
  AND {NO_PRIOR_AWARDS}
"""

F03_SQL = f"""
SELECT a.* FROM awards a
WHERE a.current_total_value_of_award >= ?
  AND a.recipient_uei IS NOT NULL
  AND a.action_date IS NOT NULL
  AND {NO_PRIOR_AWARDS}
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def backup_flags(conn):
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    table = f"flags_backup_{ts}"
    conn.execute(f"CREATE TABLE {table} AS SELECT * FROM flags")
    conn.commit()
    print(f"Backed up flags -> {table}")
    return table


def _build_flag(code, detail):
    """Construct a flag dict consistent with engine.flags._flag()."""
    meta = FLAG_META[code]
    return {
        "code": code,
        "severity": meta["severity"],
        "detail": detail,
        "label": meta["label"],
    }


def fetch_candidates(conn):
    """Return {award_id: [flag_dict, ...]} for every award that triggers at
    least one of F01/F02/F03 by raw SQL conditions (before structural filter).

    Also returns {award_id: award_row_dict} for the unique candidate set so the
    structural filter can be applied without re-querying."""
    candidates_by_award = defaultdict(list)
    award_rows = {}

    f01_rows = conn.execute(F01_SQL, (CRITICAL_SOLE_SOURCE_MIN,)).fetchall()
    for r in f01_rows:
        a = dict(r)
        award_rows[a["award_id"]] = a
        candidates_by_award[a["award_id"]].append(
            _build_flag("F01_NO_HISTORY_SOLE_SOURCE", f01_detail(a)))

    f02_rows = conn.execute(F02_SQL,
        (ONE_OFFER_MIN_OBLIGATION, *sorted(COMPETITIVE_CODES))).fetchall()
    for r in f02_rows:
        a = dict(r)
        award_rows[a["award_id"]] = a
        candidates_by_award[a["award_id"]].append(
            _build_flag("F02_NO_HISTORY_ONE_OFFER", f02_detail(a)))

    f03_rows = conn.execute(F03_SQL, (FIRST_LARGE_AWARD_MIN,)).fetchall()
    for r in f03_rows:
        a = dict(r)
        award_rows[a["award_id"]] = a
        candidates_by_award[a["award_id"]].append(
            _build_flag("F03_FIRST_LARGE_AWARD", f03_detail(a)))

    return candidates_by_award, award_rows


def apply_filter_to_candidates(candidates_by_award, award_rows):
    """Run apply_structural_filter on each candidate.
    Returns {award_id: [surviving_flag_dict, ...]} with empty entries dropped."""
    survivors = {}
    filtered_count = 0
    for award_id, triggered in candidates_by_award.items():
        ctx = {"award": award_rows[award_id]}
        kept, _filter_log = apply_structural_filter(triggered, ctx)
        if kept:
            survivors[award_id] = kept
        elif triggered:
            filtered_count += 1
    return survivors, filtered_count


def replace_flags(conn, survivors, scan_date):
    """Single transaction: clear flags, insert every surviving flag."""
    conn.execute("DELETE FROM flags")
    rows = []
    for award_id, flags in survivors.items():
        for f in flags:
            rows.append((award_id, f["code"], f["severity"], f["detail"], scan_date))
    conn.executemany(
        "INSERT INTO flags (award_id, flag_code, severity, detail, scan_date) "
        "VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Bulk SQL re-flag of all awards in the DB.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print candidate and survivor counts; do not write.")
    args = parser.parse_args()

    started = datetime.utcnow()
    conn = _connect()

    print("Pulling candidates (bulk SQL)...")
    candidates_by_award, award_rows = fetch_candidates(conn)
    print(f"  Candidates (pre-filter): {len(candidates_by_award):,} distinct awards, "
          f"{sum(len(v) for v in candidates_by_award.values()):,} flag triggers")

    print("Applying structural filter...")
    survivors, filtered_count = apply_filter_to_candidates(
        candidates_by_award, award_rows)
    print(f"  Survivors: {len(survivors):,} distinct awards, "
          f"{sum(len(v) for v in survivors.values()):,} flag rows. "
          f"{filtered_count:,} candidates stripped by structural filter.")

    if args.dry_run:
        print("(dry run: no writes made)")
    else:
        backup_flags(conn)
        n_inserted = replace_flags(conn, survivors, today())
        print(f"Replaced flags table. {n_inserted:,} rows inserted.")

    elapsed = (datetime.utcnow() - started).total_seconds()
    print(f"Done in {elapsed:.1f}s.")


if __name__ == "__main__":
    main()
