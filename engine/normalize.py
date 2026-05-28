"""Award normalization for ContractWatch.

Turns a raw USASpending search-result + award-detail pair into the
normalized dict that gets stored in the awards table. All defensive
validation lives here: anything that fails the obligation, date, or
identifier checks is logged and dropped before it can reach the DB.

Kept separate from engine.usaspending (which owns HTTP transport) so the
two concerns do not entangle.
"""

import logging
from datetime import datetime

from engine.config import MIN_OBLIGATION

log = logging.getLogger("contractwatch")

# Defensive ingestion caps. Anything outside these bounds is treated as
# poisoned data from upstream and dropped at normalize_award.
OBLIGATION_FLOOR = MIN_OBLIGATION              # $1M, same as scanner threshold
OBLIGATION_CEIL = 10_000_000_000               # $10B, above this is almost always
                                               # an IDIQ ceiling, not a real obligation
ACTION_DATE_MIN = "2010-01-01"                 # USASpending coverage starts ~2008
                                               # but FY18 is our practical floor
RECIPIENT_NAME_MAX_LEN = 500                   # defends against unicode bombs
DESCRIPTION_MAX_LEN = 5000                     # description fields are unbounded upstream

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")

# USASpending uses two different code vocabularies depending on the source:
# the search/detail API returns NNS/ONO/SP1/SP2/SS, while the bulk-download
# CSV uses single-letter codes B (Not Available for Competition), C (Not
# Competed), G (Not Competed Under SAP), plus NDO (Non-Competitive Delivery
# Order). Include both so sole-source detection fires regardless of which
# loader populated the award.
_SOLE_SOURCE_CODES = {"NNS", "ONO", "SP1", "SP2", "SS", "B", "C", "G", "NDO"}


def _parse_award_date(date_str):
    """Parse an action_date / date_signed string from USASpending. Returns a
    datetime, or None if the input is missing or in an unrecognized format."""
    if not date_str:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def normalize_award(search_result, detail):
    """Combine search result and detail into a normalized award dict for the
    database. Returns None if the award fails defensive validation (bad
    obligation, missing/out-of-range date, missing required identifier)."""
    if not detail:
        return None

    recipient = detail.get("recipient", {}) or {}
    latest_txn = detail.get("latest_transaction_contract_data", {}) or {}
    period = detail.get("period_of_performance", {}) or {}

    competition_type = latest_txn.get("extent_competed")
    number_of_offers = latest_txn.get("number_of_offers_received")
    sole_source = 1 if competition_type in _SOLE_SOURCE_CODES else 0
    type_of_contract = latest_txn.get("type_of_contract_pricing") or ""

    obligation_raw = detail.get("total_obligation")
    try:
        obligation = float(obligation_raw) if obligation_raw is not None else None
    except (TypeError, ValueError):
        obligation = None

    if obligation is None or obligation < OBLIGATION_FLOOR or obligation > OBLIGATION_CEIL:
        log.warning("normalize_award rejected: obligation=%r outside [%s, %s]",
                    obligation_raw, OBLIGATION_FLOOR, OBLIGATION_CEIL)
        return None

    action_date_raw = detail.get("date_signed") or search_result.get("Start Date")
    parsed_date = _parse_award_date(action_date_raw)
    if parsed_date is None:
        log.warning("normalize_award rejected: action_date=%r unparseable or missing",
                    action_date_raw)
        return None
    if parsed_date > datetime.utcnow() or parsed_date < datetime.fromisoformat(ACTION_DATE_MIN):
        log.warning("normalize_award rejected: action_date=%r outside [%s, today]",
                    action_date_raw, ACTION_DATE_MIN)
        return None

    uei = recipient.get("uei") or recipient.get("recipient_uei") or ""
    if not uei:
        log.warning("normalize_award rejected: missing recipient_uei (piid=%r)",
                    detail.get("piid"))
        return None

    recipient_name = recipient.get("recipient_name") or search_result.get("Recipient Name") or ""
    if not recipient_name:
        log.warning("normalize_award rejected: missing recipient_name (piid=%r, uei=%r)",
                    detail.get("piid"), uei)
        return None

    if len(recipient_name) > RECIPIENT_NAME_MAX_LEN:
        recipient_name = recipient_name[:RECIPIENT_NAME_MAX_LEN]
    description = detail.get("description") or ""
    if len(description) > DESCRIPTION_MAX_LEN:
        description = description[:DESCRIPTION_MAX_LEN]

    location = recipient.get("location") or {}
    awarding = detail.get("awarding_agency") or {}

    return {
        "award_id": str(detail.get("id", search_result.get("internal_id", ""))),
        "generated_unique_award_id": detail.get("generated_unique_award_id", ""),
        "piid": detail.get("piid") or search_result.get("Award ID"),
        "recipient_name": recipient_name,
        "recipient_uei": uei,
        "recipient_address": location.get("address_line1", ""),
        "recipient_state": location.get("state_code", ""),
        "awarding_agency": (awarding.get("toptier_agency") or {}).get("name", ""),
        "awarding_office": awarding.get("office_agency_name", ""),
        "naics_code": latest_txn.get("naics") or "",
        "psc_code": latest_txn.get("product_or_service_code") or "",
        "type_of_contract": type_of_contract,
        "competition_type": competition_type or "",
        "number_of_offers": number_of_offers,
        "current_total_value_of_award": obligation,
        "action_date": action_date_raw,
        "start_date": period.get("start_date"),
        "end_date": period.get("end_date"),
        "description": description,
        "sole_source": sole_source,
    }
