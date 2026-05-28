"""ContractWatch Engine. Configuration and constants."""

import os
from datetime import date, timedelta


# --- .env loader ---
# Reads optional KEY=VALUE overrides from a repo-root .env file. The codebase
# itself calls no external services that require secrets; USASpending's public
# API is unauthenticated. .env is only useful for setting the optional
# CONTRACTWATCH_* knobs documented in .env.example without exporting them in
# the shell. See .env.example for the full list.
def _load_dotenv():
    """Load KEY=VALUE pairs from a repo-root .env file into the environment.
    Shell environment values take precedence."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv()


# --- API Endpoints ---
USASPENDING_BASE = "https://api.usaspending.gov/api/v2"
USASPENDING_AWARDS = f"{USASPENDING_BASE}/search/spending_by_award/"
USASPENDING_AWARD_DETAIL = f"{USASPENDING_BASE}/awards"  # /{award_id}/

# --- Thresholds ---
MIN_OBLIGATION = 1_000_000             # $1M minimum to scan
CRITICAL_SOLE_SOURCE_MIN = 10_000_000  # F01: sole-source dollar floor
FIRST_LARGE_AWARD_MIN = 25_000_000     # F03: first-ever federal contract dollar floor

# --- Established-entity gate (used by structural_filter.is_established_entity) ---
# Reserved for any structural rules added back in the future.
ESTABLISHED_ENTITY_MIN_OBLIG = 50_000_000
ESTABLISHED_ENTITY_MIN_AWARDS = 50

# --- Flag Definitions (3 CRITICAL procurement-decision flags) ---
# F02's threshold lives in engine.flags as ONE_OFFER_MIN_OBLIGATION (kept
# next to its flag function). F01 and F03 use the constants above. Labels
# below are derived from those constants so a threshold change in this
# file propagates to the dashboard automatically.
_F01_M = CRITICAL_SOLE_SOURCE_MIN // 1_000_000
_F02_M = 25                                       # mirrors flags.ONE_OFFER_MIN_OBLIGATION
_F03_M = FIRST_LARGE_AWARD_MIN // 1_000_000

FLAG_META = {
    "F01_NO_HISTORY_SOLE_SOURCE":  {"severity": "CRITICAL", "label": f"No prior federal contracts plus sole-source above ${_F01_M}M"},
    "F02_NO_HISTORY_ONE_OFFER":    {"severity": "CRITICAL", "label": f"No prior federal contracts plus competitive solicitation with one offer above ${_F02_M}M"},
    "F03_FIRST_LARGE_AWARD":       {"severity": "CRITICAL", "label": f"No prior federal contracts plus first contract above ${_F03_M}M"},
}

# --- Date Defaults ---
def yesterday():
    return (date.today() - timedelta(days=1)).isoformat()

def today():
    return date.today().isoformat()

# --- Paths ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "contractwatch.db")
WEB_DIR = os.path.join(PROJECT_ROOT, "web")
DATA_DIR = os.path.join(WEB_DIR, "data")


# --- Scan and deploy settings (env-overridable) ---
def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


# Pipe-delimited list of awarding agencies to skip at ingestion. Applied
# both at scan time (engine.scanner) and at reflag time (via the
# excluded_agency structural rule). Default empty (no exclusions).
# Example value: "Department of Defense|Department of Justice"
EXCLUDED_AWARDING_AGENCIES = [
    a.strip() for a in os.environ.get("CONTRACTWATCH_EXCLUDED_AGENCIES", "").split("|")
    if a.strip()
]

BACKFILL_DAYS = _env_int("CONTRACTWATCH_BACKFILL_DAYS", 2)
SCAN_INTERVAL_HOURS = _env_int("CONTRACTWATCH_SCAN_INTERVAL_HOURS", 6)
CLOUDFLARE_PROJECT = os.environ.get("CONTRACTWATCH_CF_PROJECT", "contractwatch")
