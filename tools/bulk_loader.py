"""Bulk loader for USASpending fiscal-year contract archives.

USASpending publishes annual "All Contracts Full" archive ZIPs at
https://files.usaspending.gov/award_data_archive/ . Each ZIP contains the
transaction-level history for one fiscal year. This script reads a jobs JSON
file listing archive URLs, downloads each one (with retries and a real
browser User-Agent), parses the CSVs, dedupes to one row per unique award by
keeping the LATEST action_date, and bulk INSERT-OR-REPLACEs into the awards
table in contractwatch.db.

This is how the project's local database is populated. The DB itself is not
checked into git, so anyone forking the repo needs to run this once to
rebuild it from USASpending's public archives.

Live status:
    While the loader runs it writes progress to web/data/loader_status.json
    via atomic temp-file + os.replace (no partial reads). Open
    web/loader.html in any browser (`python -m http.server 8000 -d web` and
    visit http://localhost:8000/loader.html) for a live progress dashboard
    that polls the status file every 2 seconds.

Usage (evergreen modes, no jobs file needed):
    python tools/bulk_loader.py --mode initial   # FY15 through current FY (one-time setup / full rebuild)
    python tools/bulk_loader.py --mode monthly   # prev FY + current FY (what monthly_scan.sh runs)

Advanced (explicit jobs file, overrides --mode):
    python tools/bulk_loader.py /path/to/your_jobs.json

Jobs file format (list of dicts), for the advanced path only:
    [
      {"label": "FY18",
       "file_url": "https://files.usaspending.gov/award_data_archive/FY2018_All_Contracts_Full_20260506.zip"},
      ...
    ]

In --mode initial/monthly the loader computes the current fiscal year from
today's date (federal FY starts Oct 1) and discovers the latest USASpending
bulk archive snapshot date via HEAD probes against a known-stable FY URL.
URLs are then generated internally. No file edits required month over month
or year over year.

Schema convention:
    Each unique contract_award_unique_key gets ONE row in the awards table,
    tagged with the LATEST action_date seen across all processed archives.
    A multi-year IDV that had transactions in FY18 through FY26 will have
    its row tagged with the FY26 action_date. This is intentional: the
    flag pipeline keys off "is this entity new" (has_prior_awards), not
    "when did this contract start", so latest-action-date carries the
    signal we care about and keeps row counts honest (one row per award,
    no per-FY duplication).
"""
import argparse
import csv
import io
import json
import os
import queue
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from collections import deque
from datetime import date, datetime, timedelta

import requests

# Allow imports of engine.* when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.config import DB_PATH, MIN_OBLIGATION, PROJECT_ROOT


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}
BACKOFF_SCHEDULE = [60, 120, 240, 480, 960]  # cumulative ~30 min
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 900
CHUNK_SIZE = 1 << 20  # 1 MB
INSERT_BATCH = 5000

# extent_competed_code values that indicate non-competitive procurement
SOLE_SOURCE_CODES = {"NNS", "ONO", "SP1", "SP2", "SS", "B", "C", "G", "NDO"}

# Columns the loader expects in the USASpending bulk CSV. If any are missing,
# the archive format has changed and the loader will fail loudly instead of
# silently dropping fields.
REQUIRED_COLUMNS = {
    "contract_award_unique_key",
    "award_id_piid",
    "recipient_name",
    "recipient_uei",
    "current_total_value_of_award",
    "action_date",
    "awarding_agency_name",
    "naics_code",
    "product_or_service_code",
    "award_type_code",
    "extent_competed_code",
}

# --- Live status JSON ---
# web/loader.html polls this file every 2 seconds. Writes are atomic via
# temp-file + os.replace so the page never sees a half-written document.
STATUS_PATH = os.path.join(PROJECT_ROOT, "web", "data", "loader_status.json")
_status_lock = threading.Lock()
_status = {}              # full document; mutated by helpers
_log_tail = deque(maxlen=40)


def _f(v, default=0.0):
    try:
        return float(v) if v not in (None, "", " ") else default
    except (ValueError, TypeError):
        return default


def _i(v, default=None):
    try:
        s = (v or "").strip()
        return int(s) if s.isdigit() else default
    except (ValueError, TypeError, AttributeError):
        return default


def _s(v):
    return (v or "").strip() if isinstance(v, str) else ""


def _now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_status():
    """Snapshot the in-memory status under lock and atomically replace the
    on-disk file. Safe for the polling page to read at any moment."""
    with _status_lock:
        _status["generated_at"] = _now_iso()
        _status["log_tail"] = list(_log_tail)
        payload = json.dumps(_status, indent=2, default=str)
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".loader_status.", dir=os.path.dirname(STATUS_PATH))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp, STATUS_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _say(msg):
    """Print to stdout AND append to the rolling log tail visible on the
    loader-status page. Use instead of bare print() throughout the loader."""
    line = f"[{_now_iso()}] {msg}"
    print(line, flush=True)
    with _status_lock:
        _log_tail.append(line)


def _init_status(jobs):
    """Seed the status document at loader start."""
    with _status_lock:
        _status.clear()
        _status.update({
            "loader_pid": os.getpid(),
            "loader_alive": True,
            "pipeline_started": True,
            "pipeline_complete": False,
            "final_msg": "",
            "db_total_awards": 0,
            "fys": [
                {
                    "label": j.get("label") or j["file_url"].split("/")[-1].split(".")[0],
                    "status": "queued",
                    "attempt": 0,
                    "downloaded_mb": 0,
                    "total_mb": 0,
                    "pct": 0,
                    "speed_mbps": 0,
                    "download_sec": 0,
                    "parse_min": 0,
                    "rows_scanned": 0,
                    "awards_kept": 0,
                    "upserted": 0,
                    "upsert_total": 0,
                    "awards_final": 0,
                    "db_awards": 0,
                    "error": None,
                }
                for j in jobs
            ],
        })
    _atomic_write_status()


def _update_fy(label, **fields):
    """Merge fields into the FY entry matching `label` and re-write status."""
    with _status_lock:
        for fy in _status.get("fys", []):
            if fy["label"] == label:
                fy.update(fields)
                break
    _atomic_write_status()


def _finish(success, msg):
    """Mark the loader done."""
    with _status_lock:
        _status["loader_alive"] = False
        _status["pipeline_complete"] = bool(success)
        _status["final_msg"] = msg
        try:
            _status["db_total_awards"] = sqlite3.connect(DB_PATH).execute(
                "SELECT COUNT(*) FROM awards").fetchone()[0]
        except sqlite3.Error:
            pass
    _atomic_write_status()


def open_tuned_conn(path=DB_PATH):
    """SQLite connection with PRAGMAs tuned for bulk inserts."""
    c = sqlite3.connect(path, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=OFF")
    c.execute("PRAGMA cache_size=-500000")
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("PRAGMA mmap_size=2147483648")
    return c


def polite_download(url, label="", session=None):
    """Stream a ZIP with exponential backoff and a real browser User-Agent.
    USASpending will sometimes reject default requests UAs with 403."""
    session = session or requests.Session()
    session.headers.update(HEADERS)
    last_exc = None
    for attempt, delay in enumerate([0] + BACKOFF_SCHEDULE, start=1):
        if delay:
            _say(f"[{label}] sleeping {delay}s before attempt {attempt}")
            time.sleep(delay)
        try:
            _say(f"[{label}] download attempt {attempt}/{len(BACKOFF_SCHEDULE) + 1}")
            _update_fy(label, status="downloading", attempt=attempt)
            buf = io.BytesIO()
            t0 = time.time()
            with session.get(url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                got = 0
                last_log = time.time()
                for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        buf.write(chunk)
                        got += len(chunk)
                        if time.time() - last_log >= 15:
                            pct = (got / total * 100) if total else 0
                            mbps = got / 1e6 / (time.time() - t0)
                            _say(f"[{label}] {got / 1e6:.0f}/{total / 1e6:.0f} MB "
                                 f"({pct:.1f}%) at {mbps:.1f} MB/s")
                            _update_fy(label,
                                downloaded_mb=round(got / 1e6, 1),
                                total_mb=round(total / 1e6, 1),
                                pct=round(pct, 1),
                                speed_mbps=round(mbps, 2),
                            )
                            last_log = time.time()
            elapsed = time.time() - t0
            _say(f"[{label}] download complete: {got / 1e6:.1f} MB in {elapsed:.0f}s "
                 f"({got / 1e6 / elapsed:.1f} MB/s)")
            _update_fy(label,
                downloaded_mb=round(got / 1e6, 1),
                total_mb=round(got / 1e6, 1),
                pct=100.0,
                download_sec=int(elapsed),
            )
            return buf.getvalue()
        except Exception as exc:
            last_exc = exc
            _say(f"[{label}] attempt {attempt} failed: {exc}")
    _update_fy(label, status="failed", error=str(last_exc))
    raise last_exc


def smoke_test_columns(zip_bytes, label):
    """Verify the CSV inside the ZIP has the columns we depend on. Returns
    (ok, missing_columns)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            return False, REQUIRED_COLUMNS
        with zf.open(csv_names[0]) as f:
            text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
            reader = csv.DictReader(text)
            cols = reader.fieldnames or []
    missing = REQUIRED_COLUMNS - set(cols)
    return (len(missing) == 0), missing


def row_to_award_tuple(row):
    """Convert one CSV row to an awards-table INSERT tuple. Returns None if
    the row is missing a key or under the obligation floor."""
    key = _s(row.get("contract_award_unique_key"))
    if not key:
        return None
    obligation = _f(row.get("current_total_value_of_award"))
    if obligation < MIN_OBLIGATION:
        return None
    competition_code = _s(row.get("extent_competed_code"))
    return (
        key,                                                   # award_id (PK)
        key,                                                   # generated_unique_award_id
        _s(row.get("award_id_piid")),
        _s(row.get("recipient_name")),
        _s(row.get("recipient_uei")),
        _s(row.get("recipient_address_line_1")),
        _s(row.get("recipient_state_code")),
        _s(row.get("awarding_agency_name")),
        _s(row.get("awarding_office_name")),
        _s(row.get("naics_code")),
        _s(row.get("product_or_service_code")),
        _s(row.get("type_of_contract_pricing")),
        competition_code,
        _i(row.get("number_of_offers_received")),
        obligation,                                            # current_total_value_of_award
        _s(row.get("action_date")),
        _s(row.get("period_of_performance_start_date")),
        _s(row.get("period_of_performance_current_end_date")),
        _s(row.get("prime_award_base_transaction_description"))
            or _s(row.get("transaction_description")),
        1 if competition_code in SOLE_SOURCE_CODES else 0,
    )


def process_zip(zip_bytes, label):
    """Parse one archive ZIP, dedupe to one row per award_id keeping the
    LATEST action_date. Returns list of award tuples ready for INSERT."""
    awards = {}  # award_id -> tuple
    rows = 0
    ACTION_DATE_IDX = 16
    t0 = time.time()
    _update_fy(label, status="parsing", rows_scanned=0, awards_kept=0)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            _say(f"[{label}] parsing {name}")
            with zf.open(name) as f:
                text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                reader = csv.DictReader(text)
                for row in reader:
                    rows += 1
                    if rows % 100000 == 0:
                        _say(f"[{label}] {rows:,} rows scanned, "
                             f"{len(awards):,} unique awards kept")
                        _update_fy(label,
                            rows_scanned=rows,
                            awards_kept=len(awards),
                        )
                    tup = row_to_award_tuple(row)
                    if not tup:
                        continue
                    key = tup[0]
                    action_date = tup[ACTION_DATE_IDX]
                    prior = awards.get(key)
                    if not prior or (action_date or "") > (prior[ACTION_DATE_IDX] or ""):
                        awards[key] = tup
    _say(f"[{label}] parse done: {rows:,} rows scanned, "
         f"{len(awards):,} unique awards retained")
    _update_fy(label,
        rows_scanned=rows,
        awards_kept=len(awards),
        parse_min=round((time.time() - t0) / 60, 1),
    )
    return list(awards.values())


def bulk_upsert_awards(conn, award_tuples, label):
    """INSERT OR REPLACE awards in batches. Replace semantics let later
    archives overwrite earlier ones, naturally producing the latest-
    action-date convention across multi-FY rebuilds."""
    sql = """INSERT OR REPLACE INTO awards (
        award_id, generated_unique_award_id, piid,
        recipient_name, recipient_uei,
        recipient_address, recipient_state,
        awarding_agency, awarding_office,
        naics_code, psc_code, type_of_contract,
        competition_type, number_of_offers,
        current_total_value_of_award,
        action_date, start_date, end_date,
        description, sole_source
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

    total = len(award_tuples)
    n = 0
    _update_fy(label, status="loading", upserted=0, upsert_total=total)
    for i in range(0, total, INSERT_BATCH):
        batch = award_tuples[i:i + INSERT_BATCH]
        conn.execute("BEGIN")
        conn.executemany(sql, batch)
        conn.execute("COMMIT")
        n += len(batch)
        _say(f"[{label}] upserted {n:,}/{total:,} awards")
        _update_fy(label, upserted=n, upsert_total=total)


def _job_label(job):
    return job.get("label") or job["file_url"].split("/")[-1].split(".")[0]


def _download_worker(jobs, q):
    """Producer thread. Downloads each archive sequentially in jobs-file order
    and pushes (job, zip_bytes, exc) onto the queue. Pushes a None sentinel
    when done.

    Queue has maxsize=1, so the producer blocks once it has one zip waiting
    for the consumer. Peak memory is bounded at roughly:
        1 zip being downloaded + 1 zip waiting in queue + 1 zip being parsed
    which is ~5 GB at USASpending's typical 1.5 GB-per-FY archive size.

    Order is preserved: parser receives archives in the same chronological
    order they appear in the jobs file. This keeps the INSERT OR REPLACE
    dedup-by-latest-action-date semantics correct (later FYs overwrite
    earlier FYs for multi-year IDV rows).
    """
    for job in jobs:
        label = _job_label(job)
        file_url = job["file_url"]
        _say(f"[{label}] download starting (pipelined)")
        try:
            zip_bytes = polite_download(file_url, label=label)
            # Download is done but the parser may still be busy on a previous
            # archive. Mark this FY as "ready" so the loader page does not
            # mislead viewers into thinking we have multiple parallel
            # downloads in flight.
            _update_fy(label, status="ready")
            q.put((job, zip_bytes, None))
        except Exception as exc:
            _say(f"[{label}] download FAILED: {exc}")
            q.put((job, None, exc))
    q.put(None)


def parse_and_upsert(job, zip_bytes, conn):
    """Consumer half. Schema-checks columns, parses+dedupes, bulk upserts.
    Returns count of awards upserted (0 on schema-mismatch failure)."""
    label = _job_label(job)
    _say(f"=== JOB {label} ===")
    _say(f"[{label}] url: {job['file_url']}")

    ok, missing = smoke_test_columns(zip_bytes, label)
    if not ok:
        _say(f"[{label}] SCHEMA MISMATCH: missing {sorted(missing)}")
        _update_fy(label, status="failed", error=f"missing columns: {sorted(missing)}")
        return 0

    award_tuples = process_zip(zip_bytes, label)
    # Free the raw zip bytes before the upsert phase. Reduces peak memory
    # while the upsert holds the awards list in RAM.
    del zip_bytes

    bulk_upsert_awards(conn, award_tuples, label)

    db_total = sqlite3.connect(DB_PATH).execute(
        "SELECT COUNT(*) FROM awards").fetchone()[0]
    _say(f"[{label}] DONE: {len(award_tuples):,} awards upserted (DB total {db_total:,})")
    _update_fy(label,
        status="done",
        awards_final=len(award_tuples),
        db_awards=db_total,
    )
    with _status_lock:
        _status["db_total_awards"] = db_total
    _atomic_write_status()
    return len(award_tuples)


def current_fy():
    """Current US federal fiscal year. FY starts Oct 1.

    Example: today 2026-05-29 -> FY26. today 2026-10-01 -> FY27.
    """
    today = date.today()
    return today.year + 1 if today.month >= 10 else today.year


def discover_snapshot_date(required_fys, max_months_back=3):
    """Discover the most recent date where ALL required fiscal-year archives
    are published.

    USASpending publishes monthly. The snapshot date is embedded in archive
    filenames as YYYYMMDD. This function HEAD-probes likely publish dates
    (typically days 5-8 of each month) walking backward from today through
    each required FY, returning the most recent date at which every FY in
    `required_fys` returns 200. This avoids the partial-publish trap where
    one FY is live at the latest date but another (often the current FY) is
    not yet, which would silently produce a stale-data run.

    Returns the discovered YYYYMMDD string. Raises if no recent date has
    all required FYs available within max_months_back months.
    """
    today = date.today()
    template = (
        "https://files.usaspending.gov/award_data_archive/"
        "FY{fy}_All_Contracts_Full_{date}.zip"
    )
    for months_back in range(0, max_months_back + 1):
        target_month = today.month - months_back
        target_year = today.year
        while target_month < 1:
            target_month += 12
            target_year -= 1
        # Try most-likely publish days first.
        for day in [6, 5, 7, 4, 8, 3, 9, 10]:
            try:
                candidate = date(target_year, target_month, day)
            except ValueError:
                continue
            if candidate > today:
                continue
            date_str = candidate.strftime("%Y%m%d")
            all_present = True
            for fy in required_fys:
                url = template.format(fy=fy, date=date_str)
                try:
                    r = requests.head(
                        url, headers=HEADERS, timeout=15, allow_redirects=True
                    )
                    if r.status_code != 200:
                        all_present = False
                        break
                except requests.RequestException:
                    all_present = False
                    break
            if all_present:
                return date_str
    raise RuntimeError(
        f"no recent USASpending snapshot date has all of "
        f"{['FY' + str(f) for f in required_fys]} published "
        f"(probed back {max_months_back} months from {today})"
    )


def generate_jobs(start_fy, end_fy, snapshot_date):
    """Build the loader job list for a fiscal-year range at a snapshot date."""
    return [
        {
            "label": f"FY{fy % 100:02d}",
            "file_url": (
                "https://files.usaspending.gov/award_data_archive/"
                f"FY{fy}_All_Contracts_Full_{snapshot_date}.zip"
            ),
        }
        for fy in range(start_fy, end_fy + 1)
    ]


def jobs_for_mode(mode):
    """Resolve a --mode keyword into a jobs list plus the snapshot date.

    Snapshot date is discovered only after the required FY set is known so
    that the chosen date is one at which every FY in the set is actually
    published (avoiding partial-publish runs).
    """
    fy = current_fy()
    if mode == "initial":
        required_fys = list(range(2015, fy + 1))
    elif mode == "monthly":
        # Prev closed FY captures late-reported stragglers; current FY is
        # the active fiscal year absorbing nearly all the meaningful change.
        required_fys = [fy - 1, fy]
    else:
        raise ValueError(f"unknown mode: {mode}")
    snapshot = discover_snapshot_date(required_fys)
    return generate_jobs(required_fys[0], required_fys[-1], snapshot), snapshot


def main():
    parser = argparse.ArgumentParser(
        description="Download and ingest USASpending bulk archive ZIPs."
    )
    parser.add_argument(
        "--mode",
        choices=["initial", "monthly"],
        help="initial: FY15 through current FY (one-time setup). "
             "monthly: prev FY + current FY (scheduled refresh). "
             "URLs are generated from today's date and a discovered snapshot.",
    )
    parser.add_argument(
        "jobs_file",
        nargs="?",
        help="legacy: explicit JSON jobs file path (overrides --mode).",
    )
    args = parser.parse_args()
    if not args.mode and not args.jobs_file:
        parser.error("either --mode initial/monthly or a jobs_file path is required")
    if args.mode and args.jobs_file:
        parser.error("--mode and an explicit jobs_file are mutually exclusive")

    if args.jobs_file:
        with open(args.jobs_file) as f:
            jobs = json.load(f)
        _say(f"BULK LOADER: {len(jobs)} jobs from {args.jobs_file}")
    else:
        _say(f"BULK LOADER: --mode {args.mode}: discovering current USASpending snapshot date...")
        jobs, snapshot = jobs_for_mode(args.mode)
        labels = ",".join(j["label"] for j in jobs)
        _say(f"BULK LOADER: snapshot {snapshot}, {len(jobs)} jobs ({labels})")

    _say(f"DB: {DB_PATH}")
    _say(f"MIN_OBLIGATION floor: ${MIN_OBLIGATION:,}")
    _say(f"Live status: open web/loader.html in a browser pointed at this checkout")

    # Ensure the awards table exists before loading.
    from engine import db as _db
    _db.init_db(DB_PATH)

    _init_status(jobs)
    conn = open_tuned_conn(DB_PATH)
    total = 0
    failed = 0
    t_all = time.time()

    # Pipeline: a background thread downloads the next archive while the main
    # thread parses and upserts the current one. Queue depth 1 keeps the
    # producer at most one archive ahead of the consumer.
    download_queue = queue.Queue(maxsize=1)
    downloader = threading.Thread(
        target=_download_worker, args=(jobs, download_queue), daemon=True
    )
    downloader.start()

    try:
        while True:
            item = download_queue.get()
            if item is None:
                break
            job, zip_bytes, exc = item
            label = _job_label(job)
            if exc is not None:
                _update_fy(label, status="failed", error=str(exc))
                failed += 1
                continue
            try:
                total += parse_and_upsert(job, zip_bytes, conn)
            except Exception as exc:
                failed += 1
                _say(f"!! JOB FAILED: {label}: {exc}")
                continue
    finally:
        downloader.join(timeout=5)
        conn.close()

    elapsed_min = (time.time() - t_all) / 60
    n_final = sqlite3.connect(DB_PATH).execute("SELECT COUNT(*) FROM awards").fetchone()[0]
    summary = (f"LOAD COMPLETE in {elapsed_min:.1f} min: "
               f"{total:,} awards upserted, {failed} jobs failed, "
               f"awards table now has {n_final:,} rows")
    _say(f"=== {summary} ===")

    # Capture the bulk-archive snapshot date from the first job's URL filename.
    # USASpending names archives like FY2026_All_Contracts_Full_20260506.zip,
    # where 20260506 is the snapshot publish date. This is the "data current
    # through" date the dashboard surfaces as "Last refresh" so readers know
    # how fresh the upstream data is, distinct from when monthly_scan.sh ran.
    import re as _re
    snapshot_iso = ""
    for j in jobs:
        m = _re.search(r"_(\d{8})\.zip", j.get("file_url", ""))
        if m:
            yyyymmdd = m.group(1)
            snapshot_iso = f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
            break
    if snapshot_iso:
        from engine import db as _db
        _db.set_scan_state("bulk_archive_snapshot_date", snapshot_iso)
        _say(f"Bulk archive snapshot date: {snapshot_iso}")

    _say("Next steps:")
    _say("  uv run python reflag_all.py     # apply F01/F02/F03 + structural filter")
    _say("  uv run python export_json.py    # regenerate web/data/latest.json")
    _finish(success=(failed == 0), msg=summary)


if __name__ == "__main__":
    main()
