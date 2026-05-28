"""HTTP client for the USASpending.gov public API.

Owns transport concerns only: session/UA setup, retries, rate-limit
backoff, and pagination of /search/spending_by_award/ and
/awards/{id}/. Data transformation lives in engine.normalize.
"""

import time
import logging
import requests

from engine.config import (
    USASPENDING_AWARDS, USASPENDING_AWARD_DETAIL,
    MIN_OBLIGATION,
)

log = logging.getLogger("contractwatch")

# Retry config
MAX_RETRIES = 5
RETRY_DELAY = 3
REQUEST_TIMEOUT = 45

# Rate limiting: USASpending is sensitive to burst requests.
_last_request_time = 0
MIN_REQUEST_INTERVAL = 0.4  # seconds between requests

_session = None


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "ContractWatch/1.0 (federal-contract-oversight)",
            "Accept": "application/json",
        })
    return _session


def _request(method, url, **kwargs):
    """HTTP request with retries, rate-limit backoff, and pacing."""
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    session = _get_session()

    for attempt in range(MAX_RETRIES):
        try:
            _last_request_time = time.time()
            resp = session.request(method, url, **kwargs)
            if resp.status_code == 429:
                wait = RETRY_DELAY * (attempt + 2)
                log.warning(f"Rate limited on {url}, waiting {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAY * (attempt + 1)
                log.warning(f"Server error {resp.status_code} on {url}, retry in {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            log.error(f"HTTP {resp.status_code} on {url}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            log.error(f"Request failed for {url}: {e}")
            return None
    return None


def fetch_new_awards(start_date, end_date, min_obligation=MIN_OBLIGATION,
                     page=1, limit=100, date_type="last_modified_date"):
    """Pull contract awards from USASpending for a date range.

    Uses the /api/v2/search/spending_by_award/ endpoint. date_type selects which
    date the window filters on. "last_modified_date" (the default) scans by when
    USASpending published or updated each record. This is required to capture
    Department of Defense awards: DOD contract data carries a mandatory 90-day
    publication delay, so an action_date window never sees it. Scanning by
    last_modified_date catches a DOD award on the day it finally publishes,
    regardless of how old its action date is.
    Returns list of award dicts and whether there are more pages.
    """
    # USASpending's search endpoint 500s when expensive fields like
    # "Total Outlays", "Description (Award)", "Contract Award Type", and
    # "Last Date to Order" are requested in combination with 100-record pages.
    # Keep the field list minimal here; full metadata is fetched per-award
    # in fetch_award_detail().
    payload = {
        "filters": {
            "time_period": [{
                "start_date": start_date,
                "end_date": end_date,
                "date_type": date_type,
            }],
            "award_type_codes": ["A", "B", "C", "D"],  # contracts only
            "award_amounts": [{"lower_bound": min_obligation}],
        },
        "fields": [
            "Award ID", "Recipient Name", "Awarding Agency", "Award Amount",
        ],
        "page": page,
        "limit": limit,
        "sort": "Award Amount",
        "order": "desc",
    }
    data = _request("POST", USASPENDING_AWARDS, json=payload)
    if not data:
        return [], False

    results = data.get("results", [])
    has_next = data.get("page_metadata", {}).get("hasNext", False)
    return results, has_next


def fetch_award_detail(award_id):
    """Get full award detail. Uses /api/v2/awards/{generated_internal_id}/."""
    url = f"{USASPENDING_AWARD_DETAIL}/{award_id}/"
    data = _request("GET", url)
    if not data:
        return None
    return data
