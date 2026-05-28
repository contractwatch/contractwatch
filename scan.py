#!/usr/bin/env python3
"""ContractWatch Engine. Command line interface.

Scans federal contract awards and surfaces statistical anomalies against
patterns observed in public USASpending.gov data.

Usage:
    python scan.py                       # Scan yesterday's awards above $1M
    python scan.py --date 2025-03-15     # Scan a specific date
    python scan.py --days 7              # Scan the last 7 days
    python scan.py --min-amount 5000000  # Override the minimum threshold
    python scan.py --verbose             # Also list below-threshold flagged awards
    python scan.py --demo                # Scan a known set of award IDs

The scan engine lives in engine/scanner.py; this file is the terminal front end.
"""

import argparse
import logging
import sys
from datetime import date, timedelta

from engine.config import MIN_OBLIGATION, today, yesterday
from engine import db
from engine.scanner import run_scan_job, run_direct_scan_job

log = logging.getLogger("contractwatch")


# --- Terminal colors ---
class C:
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def severity_color(severity):
    return {
        "CRITICAL": C.RED,
        "HIGH": C.YELLOW,
        "MEDIUM": C.CYAN,
    }.get(severity, C.WHITE)


# --- Output formatting ---

def print_banner():
    print(f"""
{C.RED}{C.BOLD}  ContractWatch Engine
  Federal Contract Anomaly Detection Scanner{C.RESET}
""")


_SEV_RANK = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}


def _entry_severity(entry):
    return max((f.get("severity") for f in entry["flags"]),
               key=lambda s: _SEV_RANK.get(s, 0), default="HIGH")


def print_award_header(award, severity, rank):
    obligation = award.get("current_total_value_of_award", 0) or 0
    name = award.get("recipient_name", "UNKNOWN")
    piid = award.get("piid", "N/A")
    agency = award.get("awarding_agency", "N/A")
    sev_color = severity_color(severity)

    print(f"\n{C.BOLD}{'-' * 72}{C.RESET}")
    print(f"  {C.BOLD}#{rank}{C.RESET}  {sev_color}{C.BOLD}{severity}{C.RESET}  "
          f"{C.WHITE}{C.BOLD}${obligation:>15,.0f}{C.RESET}")
    print(f"  {C.BOLD}{name}{C.RESET}")
    print(f"  {C.DIM}PIID: {piid}  |  Agency: {agency}{C.RESET}")

    desc = award.get("description", "")
    if desc:
        desc_display = desc[:100] + "..." if len(desc) > 100 else desc
        print(f"  {C.DIM}{desc_display}{C.RESET}")

    uei = award.get("recipient_uei", "")
    state = award.get("recipient_state", "")
    award_type = award.get("type_of_contract", "")
    competition = award.get("competition_type", "")
    print(f"  {C.DIM}UEI: {uei}  |  State: {state}  |  Type: {award_type}  |  Competition: {competition}{C.RESET}")


def print_flags(flags):
    for f in sorted(flags, key=lambda x: -_SEV_RANK.get(x.get("severity"), 0)):
        color = severity_color(f["severity"])
        print(f"  {color}[{f['severity']:>8}]{C.RESET}  {f.get('label', f['code'])}")
        print(f"  {C.DIM}           {f['detail']}{C.RESET}")


def print_summary(result):
    flagged = result.flagged
    print(f"\n{C.BOLD}{'=' * 72}{C.RESET}")
    print(f"  {C.BOLD}SCAN COMPLETE{C.RESET}")
    print(f"  Awards scanned: {result.awards_scanned}")
    print(f"  Awards flagged: {C.RED}{C.BOLD}{len(flagged)}{C.RESET}")
    if flagged:
        total_dollars = sum(e["award"].get("current_total_value_of_award", 0) or 0 for e in flagged)
        print(f"  Flagged obligation total: {C.BOLD}${total_dollars:,.0f}{C.RESET}")
        crit = sum(1 for e in flagged for f in e["flags"] if f["severity"] == "CRITICAL")
        high = sum(1 for e in flagged for f in e["flags"] if f["severity"] == "HIGH")
        med = sum(1 for e in flagged for f in e["flags"] if f["severity"] == "MEDIUM")
        print(f"  Flag breakdown: {C.RED}CRITICAL: {crit}{C.RESET}  "
              f"{C.YELLOW}HIGH: {high}{C.RESET}  {C.CYAN}MEDIUM: {med}{C.RESET}")
    print(f"{'=' * 72}\n")


# --- Progress callback ---

def _cli_progress(info):
    phase = info.get("phase")
    if phase == "fetching":
        total = info.get("awards_total", 0)
        print(f"\r  {C.DIM}Fetching awards... {total} found{C.RESET}      ", end="", flush=True)
    elif phase == "scanning":
        scanned = info.get("awards_scanned")
        if scanned is not None:
            print(f"\r  {C.DIM}Scanning awards... {scanned} processed{C.RESET}      ", end="", flush=True)


def print_results(result, verbose):
    print()  # close the progress line
    flagged = result.flagged
    if flagged:
        print(f"\n{C.RED}{C.BOLD}  FLAGGED AWARDS{C.RESET}")
        for rank, entry in enumerate(flagged, 1):
            print_award_header(entry["award"], _entry_severity(entry), rank)
            print_flags(entry["flags"])
    elif result.awards_scanned > 0:
        print(f"\n  {C.GREEN}{C.BOLD}No flags fired on the scanned awards.{C.RESET}")
    print_summary(result)


# --- CLI ---

def parse_args():
    parser = argparse.ArgumentParser(
        description="ContractWatch. Federal contract anomaly detection scanner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", type=str, help="Scan a specific date (YYYY-MM-DD)")
    parser.add_argument("--start", type=str, help="Scan window start date (YYYY-MM-DD); use with --end")
    parser.add_argument("--end", type=str, help="Scan window end date (YYYY-MM-DD); use with --start")
    parser.add_argument("--days", type=int, default=1, help="Number of days to scan back (default: 1)")
    parser.add_argument("--min-amount", type=float, default=MIN_OBLIGATION,
                        help=f"Minimum obligation threshold (default: ${MIN_OBLIGATION:,.0f})")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose CLI output")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--demo", action="store_true",
                        help="Run against known award IDs (bypasses the search endpoint)")
    parser.add_argument("--award-ids", type=str, nargs="+",
                        help="Scan specific generated_unique_award_id(s)")
    parser.add_argument("--award-id-file", type=str,
                        help="Scan generated_unique_award_id(s) listed one per line in a file")
    parser.add_argument("--catch-up", action="store_true",
                        help="Scan from the day after the latest action_date in the DB through today")
    return parser.parse_args()


def _catch_up_window():
    """Read MAX(action_date) from awards and return (start, end) covering from
    the day after that through today. Falls back to a 30-day window if the DB
    is empty."""
    with db._connect() as conn:
        row = conn.execute("SELECT MAX(action_date) FROM awards").fetchone()
    last = row[0] if row else None
    if not last:
        start_dt = date.fromisoformat(today()) - timedelta(days=30)
        return start_dt.isoformat(), today()
    start_dt = date.fromisoformat(last) + timedelta(days=1)
    return start_dt.isoformat(), today()


DEMO_AWARD_IDS = [
    # DOE Sandia
    "CONT_AWD_DENA0003525_8900_-NONE-_-NONE-",
    # DOE Hanford / Battelle
    "CONT_AWD_DEAC0576RL01830_8900_-NONE-_-NONE-",
    # DOE Oak Ridge / UT-Battelle
    "CONT_AWD_DEAC0500OR22725_8900_-NONE-_-NONE-",
    # USAID Columbia University
    "CONT_AWD_75A50122C00012_7505_-NONE-_-NONE-",
]


def main():
    args = parse_args()

    level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print_banner()
    db.init_db()
    print(f"  {C.DIM}Database: {db.DB_PATH}{C.RESET}\n")

    if args.demo:
        print(f"  {C.YELLOW}{C.BOLD}DEMO MODE{C.RESET} - scanning known award IDs\n")
        result = run_direct_scan_job(DEMO_AWARD_IDS, progress=_cli_progress)
    elif args.award_ids:
        result = run_direct_scan_job(args.award_ids, progress=_cli_progress)
    elif args.award_id_file:
        with open(args.award_id_file) as fh:
            ids = [line.strip() for line in fh if line.strip()]
        print(f"  {C.DIM}Re-running {len(ids)} award(s) from {args.award_id_file}{C.RESET}")
        result = run_direct_scan_job(ids, progress=_cli_progress)
    else:
        if args.catch_up:
            start_date, end_date = _catch_up_window()
        elif args.start and args.end:
            start_date, end_date = args.start, args.end
        elif args.date:
            start_date = end_date = args.date
        else:
            end_date = yesterday()
            start_dt = date.fromisoformat(end_date) - timedelta(days=args.days - 1)
            start_date = start_dt.isoformat()
        print(f"  {C.DIM}Scanning {start_date} to {end_date}, minimum ${args.min_amount:,.0f}{C.RESET}")
        result = run_scan_job(start_date, end_date, min_amount=args.min_amount,
                              progress=_cli_progress)

    print_results(result, args.verbose)
    if result.error:
        print(f"\n  {C.RED}{C.BOLD}ABORTED:{C.RESET} {result.error}\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
