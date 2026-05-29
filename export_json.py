#!/usr/bin/env python3
"""Export ContractWatch flag results from contractwatch.db to static JSON.

Produces, under web/data/:
    latest.json          - currently flagged awards (one record per surviving flag set)
    stats.json           - running totals and flag distribution
    history/{date}.json  - dated archive of latest.json

export_all() is importable; main() is the standalone CLI.

Usage:
    python3 export_json.py
    python3 export_json.py --limit 100
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime

from engine.config import DB_PATH, DATA_DIR


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


SEV_RANK = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}


def build_award_rows(conn, limit):
    """Assemble flagged award rows with surviving flags attached. Any award
    with at least one surviving flag is included. Awards that fail the
    publish filter (e.g., action_date before FY18 cutoff) are dropped before
    the rest of the row is assembled."""
    from engine.structural_filter import should_publish
    cur = conn.cursor()
    awards = cur.execute("""
        SELECT a.award_id, a.piid, a.generated_unique_award_id,
               a.recipient_name, a.recipient_uei, a.recipient_state,
               a.awarding_agency, a.awarding_office,
               a.current_total_value_of_award, a.action_date, a.start_date, a.end_date,
               a.type_of_contract, a.competition_type, a.description,
               a.naics_code, a.psc_code
        FROM awards a
        WHERE EXISTS (SELECT 1 FROM flags f WHERE f.award_id = a.award_id)
    """).fetchall()

    rows = []
    for a in awards:
        # PUBLISH_FILTERS — drop awards that pass the flag pipeline but should
        # not appear in the published dataset (e.g., pre-FY18 action_date).
        if not should_publish({"award": dict(a)}):
            continue
        flags = cur.execute("""
            SELECT flag_code, severity, detail
            FROM flags
            WHERE award_id = ?
              AND scan_date = (SELECT MAX(scan_date) FROM flags WHERE award_id = ?)
            ORDER BY severity DESC
        """, (a["award_id"], a["award_id"])).fetchall()
        if not flags:
            continue

        rows.append({
            "piid": a["piid"] or "",
            "recipient": a["recipient_name"] or "",
            "uei": a["recipient_uei"] or "",
            "state": a["recipient_state"] or "",
            "agency": a["awarding_agency"] or "",
            "office": a["awarding_office"] or "",
            "obligation": a["current_total_value_of_award"] or 0,
            "action_date": a["action_date"] or "",
            "start_date": a["start_date"] or "",
            "end_date": a["end_date"] or "",
            "contract_type": a["type_of_contract"] or "",
            "competition": a["competition_type"] or "",
            "naics": a["naics_code"] or "",
            "psc": a["psc_code"] or "",
            "description": a["description"] or "",
            "flag_codes": [f["flag_code"] for f in flags],
            "flag_details": [
                {"code": f["flag_code"], "severity": f["severity"], "detail": f["detail"]}
                for f in flags
            ],
            "usaspending_url": (
                f"https://www.usaspending.gov/award/{a['generated_unique_award_id']}"
                if a["generated_unique_award_id"] else ""
            ),
        })

    def _row_rank(r):
        details = r.get("flag_details") or []
        max_sev = max((SEV_RANK.get(d.get("severity"), 0) for d in details), default=0)
        return (-max_sev, r.get("action_date") or "")
    rows.sort(key=_row_rank)
    return rows[:limit] if limit else rows


def _scan_state(conn, key):
    try:
        row = conn.execute("SELECT value FROM scan_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""
    except sqlite3.OperationalError:
        return ""


def build_stats(conn, rows):
    """Aggregate stats for stats.json."""
    cur = conn.cursor()
    total_awards = cur.execute("SELECT COUNT(*) FROM awards").fetchone()[0]
    total_flagged = cur.execute("SELECT COUNT(DISTINCT award_id) FROM flags").fetchone()[0]
    flag_dist = cur.execute("""
        SELECT flag_code, severity, COUNT(*) as n
        FROM flags GROUP BY flag_code, severity
        ORDER BY n DESC
    """).fetchall()

    severity = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0}
    for r in rows:
        for f in r["flag_details"]:
            if f["severity"] in severity:
                severity[f["severity"]] += 1

    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "total_awards_scanned": total_awards,
        "total_awards_flagged": total_flagged,
        "displayed_awards": len(rows),
        "displayed_obligation_total": sum(r["obligation"] for r in rows),
        "count_critical": severity["CRITICAL"],
        "count_high": severity["HIGH"],
        "count_medium": severity["MEDIUM"],
        "last_scan_window": _scan_state(conn, "last_scan_window"),
        "last_scan_finished_at": _scan_state(conn, "last_scan_finished_at"),
        "bulk_archive_snapshot_date": _scan_state(conn, "bulk_archive_snapshot_date"),
        "flag_distribution": [
            {"code": row["flag_code"], "severity": row["severity"], "count": row["n"]}
            for row in flag_dist
        ],
    }


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return os.path.getsize(path)


def export_all(limit=None, data_dir=DATA_DIR):
    """Regenerate latest.json, stats.json, and the dated history file.
    Every award with at least one surviving flag is included."""
    conn = _connect()
    try:
        rows = build_award_rows(conn, limit)
        stats = build_stats(conn, rows)
        last_action_row = conn.execute("SELECT MAX(action_date) FROM awards").fetchone()
        last_action_date = last_action_row[0] if last_action_row and last_action_row[0] else ""
        latest = {
            "generated_at": stats["generated_at"],
            "last_action_date": last_action_date,
            "count": len(rows),
            "awards": rows,
        }
        write_json(os.path.join(data_dir, "latest.json"), latest)
        write_json(os.path.join(data_dir, "stats.json"), stats)
        if rows:
            scan_date = datetime.utcnow().strftime("%Y-%m-%d")
            write_json(os.path.join(data_dir, "history", f"{scan_date}.json"), latest)
        return {"count": len(rows), "total_flagged": stats["total_awards_flagged"]}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Export contractwatch.db to static JSON")
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum rows to export (default: all)")
    args = parser.parse_args()

    print(f"Reading {DB_PATH}")
    summary = export_all(limit=args.limit)
    print(f"Exported {summary['count']} flagged award(s) to {DATA_DIR}")
    print(f"Total flagged in database: {summary['total_flagged']}")


if __name__ == "__main__":
    main()
