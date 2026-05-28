"""Headless scan engine for ContractWatch.

Wraps the per-award pipeline and the scan loop so the FastAPI service and the
CLI can both drive scans without terminal output.

Every award is scored in a single pass against the three USASpending-only
flags (F01/F02/F03). No SAM lookups.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from engine.config import (
    MIN_OBLIGATION, EXCLUDED_AWARDING_AGENCIES, today,
)
from engine import db
from engine.usaspending import fetch_new_awards, fetch_award_detail
from engine.normalize import normalize_award
from engine.flags import run_all_flags

log = logging.getLogger("contractwatch")


def _now():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@dataclass
class ScanResult:
    """Outcome of a scan run. flagged holds every entry with at least one
    surviving flag."""
    start_date: str = ""
    end_date: str = ""
    awards_scanned: int = 0
    awards_flagged: int = 0
    flagged: list = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    cancelled: bool = False
    error: str = None


def _scan_award_pass_a(search_result):
    """Process one award using USASpending data only.
    Returns an entry dict, or None if the award could not be processed."""
    internal_id = search_result.get("internal_id") or search_result.get("generated_internal_id")
    if not internal_id:
        return None

    detail = fetch_award_detail(internal_id)
    if not detail:
        return None

    award_data = normalize_award(search_result, detail)
    if not award_data:
        return None

    # Skip awarding agencies the operator has configured to exclude.
    if EXCLUDED_AWARDING_AGENCIES and (award_data.get("awarding_agency") or "").strip() in EXCLUDED_AWARDING_AGENCIES:
        return None

    db.upsert_award(award_data)

    flags = run_all_flags({"award": award_data})
    db.store_flags(award_data["award_id"], flags, today())

    return {
        "search_result": search_result,
        "award": award_data,
        "flags": flags,
    }


def run_scan_job(start_date, end_date, *, min_amount=MIN_OBLIGATION,
                 progress=None, should_cancel=None):
    """Run a government-wide scan for a date window.

    progress, if given, is called with a dict of status fields. should_cancel,
    if given, is polled to allow early termination.
    """
    result = ScanResult(start_date=start_date, end_date=end_date, started_at=_now())

    def report(**fields):
        if progress:
            try:
                progress(fields)
            except Exception:
                pass

    def cancelled():
        return bool(should_cancel and should_cancel())

    db.init_db()

    # Phase 1: page through every matching award search result.
    report(phase="fetching", awards_total=0, awards_scanned=0, flags_found=0)
    search_results = []
    page = 1
    while True:
        if cancelled():
            result.cancelled = True
            result.finished_at = _now()
            return result
        awards, has_next = fetch_new_awards(start_date, end_date, min_amount, page=page)
        search_results.extend(awards)
        report(phase="fetching", awards_total=len(search_results))
        if not awards or not has_next:
            break
        page += 1

    total = len(search_results)
    report(phase="scanning", awards_total=total, awards_scanned=0, flags_found=0)

    # Phase 2: run every award through the flag pipeline. Any entry with at
    # least one surviving flag counts as "flagged".
    entries = []
    flagged_count = 0
    for search_result in search_results:
        if cancelled():
            result.cancelled = True
            break
        entry = _scan_award_pass_a(search_result)
        result.awards_scanned += 1
        if entry:
            entries.append(entry)
            if entry["flags"]:
                flagged_count += 1
        report(phase="scanning", awards_scanned=result.awards_scanned, flags_found=flagged_count)

    flagged = sorted((e for e in entries if e["flags"]),
                     key=lambda e: e["award"].get("action_date") or "", reverse=True)
    result.flagged = flagged
    result.awards_flagged = len(flagged)
    result.finished_at = _now()
    report(phase="done", awards_scanned=result.awards_scanned, flags_found=len(flagged))
    return result


def run_direct_scan_job(award_ids, *, progress=None, should_cancel=None):
    """Scan specific awards by generated_unique_award_id, bypassing the search
    endpoint. Used by the CLI demo and --award-ids modes."""
    result = ScanResult(started_at=_now())

    def report(**fields):
        if progress:
            try:
                progress(fields)
            except Exception:
                pass

    db.init_db()
    report(phase="scanning", awards_total=len(award_ids), awards_scanned=0, flags_found=0)

    entries = []
    for gen_id in award_ids:
        if should_cancel and should_cancel():
            result.cancelled = True
            break
        fake_search = {
            "internal_id": None,
            "generated_internal_id": gen_id,
            "Award ID": gen_id.split("_")[2] if "_" in gen_id else gen_id,
        }
        entry = _scan_award_pass_a(fake_search)
        result.awards_scanned += 1
        if entry:
            entries.append(entry)
        report(phase="scanning", awards_scanned=result.awards_scanned)

    flagged = sorted((e for e in entries if e["flags"]),
                     key=lambda e: e["award"].get("action_date") or "", reverse=True)
    result.flagged = flagged
    result.awards_flagged = len(flagged)
    result.finished_at = _now()
    report(phase="done", awards_scanned=result.awards_scanned, flags_found=len(flagged))
    return result
