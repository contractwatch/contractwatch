"""Anomaly detection flags for ContractWatch.

Three CRITICAL procurement-decision flags. Each receives the award context dict
and returns a flag result or None if not triggered.

Flag result shape:
    {"code": "F0X_...", "severity": "CRITICAL", "detail": "...", "label": "..."}

The flags:
    F01  No prior federal contracts + sole-source above $10M
    F02  No prior federal contracts + competitive solicitation with
         one offer above $25M
    F03  No prior federal contracts + first contract above $25M
"""

import logging
from datetime import datetime

from engine.config import (
    FLAG_META,
    CRITICAL_SOLE_SOURCE_MIN, FIRST_LARGE_AWARD_MIN,
)
from engine import db

log = logging.getLogger("contractwatch")

# F02 threshold: nominally competitive, single offer, no prior history.
# Set above the routine niche-market band where one-offer competition is
# explainable by genuine market reality.
ONE_OFFER_MIN_OBLIGATION = 25_000_000

# USASpending competition codes that count as nominally competitive.
# A = Full and Open; D = Full and Open After Exclusion of Sources;
# E = Followon to Competed Action; F = Competed Under SAP;
# CDO = Competitive Delivery Order.
COMPETITIVE_CODES = {"A", "D", "E", "F", "CDO"}


def _flag(code, detail):
    """Build a flag result dict."""
    meta = FLAG_META[code]
    return {
        "code": code,
        "severity": meta["severity"],
        "detail": detail,
        "label": meta["label"],
    }


# --- Detail-string formatters ---
# Single source of truth for the human-readable detail text attached to each
# flag. Used by the live scanner via the flag functions below AND by the
# bulk reflag (reflag_all.py) so the wording stays in sync across both paths.
# Each accepts a dict (works for ctx['award'] in the live path and a SQLite
# row dict in the bulk path).

def f01_detail(award):
    """F01 detail string: no-history sole-source."""
    return (f"No prior federal contracts for {award.get('recipient_name') or 'unknown'} "
            f"(UEI: {award.get('recipient_uei')}), "
            f"sole-source ${award.get('current_total_value_of_award') or 0:,.0f}")


def f02_detail(award):
    """F02 detail string: no-history one-offer competitive."""
    return (f"No prior federal contracts for {award.get('recipient_name') or 'unknown'} "
            f"(UEI: {award.get('recipient_uei')}); "
            f"competitive solicitation received 1 offer; "
            f"obligation ${award.get('current_total_value_of_award') or 0:,.0f}")


def f03_detail(award):
    """F03 detail string: no-history first large award."""
    return (f"No prior federal contracts for {award.get('recipient_name') or 'unknown'} "
            f"(UEI: {award.get('recipient_uei')}); "
            f"first federal contract ${award.get('current_total_value_of_award') or 0:,.0f}")


def _parse_date(date_str):
    """Parse a date string, trying common formats."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


# ==========================================================================
# F01: No prior federal contracts + sole-source above $10M
# ==========================================================================

def f01_no_history_sole_source(ctx):
    """F01: First-time federal contractor receiving a large sole-source award.

    Structural filter applied centrally: major_prime_structural,
    structural_pre_existing_relationship, joint_venture_structural,
    anc_tribal_subsidiary.
    """
    award = ctx["award"]
    obligation = award.get("current_total_value_of_award", 0) or 0
    if obligation < CRITICAL_SOLE_SOURCE_MIN:
        return None

    if not award.get("sole_source"):
        return None

    uei = award.get("recipient_uei")
    action_date = award.get("action_date")
    if not uei or not action_date:
        return None

    has_prior = db.has_prior_awards(uei, action_date)
    if not has_prior:
        return _flag("F01_NO_HISTORY_SOLE_SOURCE", f01_detail(award))
    return None


# ==========================================================================
# F02: No prior federal contracts + competitive one-offer above $25M
# ==========================================================================

def f02_no_history_one_offer(ctx):
    """F02: First-time federal contractor on a nominally competitive
    solicitation that received only one offer.

    Catches the wired-solicitation pattern: SOW tailored to a specific
    vendor, competition technically open, single offer received.

    Structural filter applied centrally: major_prime_structural,
    structural_pre_existing_relationship, joint_venture_structural,
    anc_tribal_subsidiary.
    """
    award = ctx["award"]
    obligation = award.get("current_total_value_of_award", 0) or 0
    if obligation < ONE_OFFER_MIN_OBLIGATION:
        return None

    if award.get("sole_source"):
        return None  # sole-source pattern is F01's job

    competition = (award.get("competition_type") or "").strip()
    if competition not in COMPETITIVE_CODES:
        return None

    if award.get("number_of_offers") != 1:
        return None

    uei = award.get("recipient_uei")
    action_date = award.get("action_date")
    if not uei or not action_date:
        return None

    has_prior = db.has_prior_awards(uei, action_date)
    if not has_prior:
        return _flag("F02_NO_HISTORY_ONE_OFFER", f02_detail(award))
    return None


# ==========================================================================
# F03: First-ever federal contract above $25M
# ==========================================================================

def f03_first_large_award(ctx):
    """F03: An entity's first federal contract is above the threshold.

    Catches the aged-shell pattern: an entity sits in SAM for years with broad
    NAICS coverage, then activates with a single large award.

    Structural filter applied centrally: major_prime_structural,
    structural_pre_existing_relationship, joint_venture_structural,
    anc_tribal_subsidiary.
    """
    award = ctx["award"]
    obligation = award.get("current_total_value_of_award", 0) or 0
    if obligation < FIRST_LARGE_AWARD_MIN:
        return None

    uei = award.get("recipient_uei")
    action_date = award.get("action_date")
    if not uei or not action_date:
        return None

    has_prior = db.has_prior_awards(uei, action_date)
    if not has_prior:
        return _flag("F03_FIRST_LARGE_AWARD", f03_detail(award))
    return None


# ==========================================================================
# Runner
# ==========================================================================

ALL_FLAGS = [
    f01_no_history_sole_source,
    f02_no_history_one_offer,
    f03_first_large_award,
]


def run_all_flags(ctx):
    """Run all registered flags against an award context.

    Pipeline:
      1. Run every flag function against the context.
      2. Apply STRUCTURAL_RULES to strip flags that match a structural rule.
      3. Stash the filter audit log in ctx['_filtered_flags'].

    Returns the surviving list of flag dicts.
    """
    from engine.structural_filter import apply_structural_filter

    triggered = []
    for flag_fn in ALL_FLAGS:
        try:
            result = flag_fn(ctx)
            if result:
                triggered.append(result)
        except Exception as e:
            log.error(f"Error running {flag_fn.__name__}: {e}")

    survivors, filter_log = apply_structural_filter(triggered, ctx)
    ctx["_filtered_flags"] = filter_log
    return survivors
