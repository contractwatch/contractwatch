"""Export historical aggregations from contractwatch.db for the historical
trends dashboard. Re-run any time after the bulk loader finishes a new FY.

Writes: web/data/historical.json
"""
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.config import DB_PATH, PROJECT_ROOT

OUT_PATH = os.path.join(PROJECT_ROOT, "web", "data", "historical.json")

# USASpending extent_competed_code mapping
COMPETITION_LABELS = {
    "A": "Full and open competition",
    "B": "Not available for competition",
    "C": "Not competed (sole source)",
    "D": "Full and open after exclusions",
    "E": "Follow-on to competed action",
    "F": "Competed under SAP",
    "G": "Not competed under SAP",
    "CDO": "Competitive delivery order",
    "NDO": "Non-competitive delivery order",
}

# Codes that count as sole-source / non-competed
SOLE_SOURCE_CODES = {"C", "G", "NDO"}
NON_COMPETED_CODES = {"B", "C", "G", "NDO"}

PRICING_BUCKETS = {
    "FFP": ["FIRM FIXED PRICE", "FIXED PRICE WITH ECONOMIC PRICE ADJUSTMENT",
            "FIXED PRICE INCENTIVE", "J", "U", "Y"],
    "T&M / LH": ["TIME AND MATERIALS", "LABOR HOURS"],
    "Cost-reimbursement": ["COST PLUS FIXED FEE", "COST NO FEE",
                           "COST PLUS AWARD FEE", "COST PLUS INCENTIVE FEE",
                           "COST SHARING", "R", "S", "K"],
    "Other": ["Z"],
}
PRICING_LOOKUP = {}
for bucket, terms in PRICING_BUCKETS.items():
    for t in terms:
        PRICING_LOOKUP[t] = bucket


def fy_of(action_date):
    """Convert YYYY-MM-DD to federal FY year."""
    if not action_date or len(action_date) < 7:
        return None
    try:
        y, m = int(action_date[:4]), int(action_date[5:7])
        return y + 1 if m >= 10 else y
    except (ValueError, TypeError):
        return None


def query(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_fy_summary(conn):
    """Per-FY: count, total obligation, sole-source count, single-offer count,
    avg offers (where reported)."""
    rows = query(conn, """
        SELECT
            substr(action_date, 1, 4) AS year,
            substr(action_date, 6, 2) AS month,
            COUNT(*) AS n,
            SUM(COALESCE(current_total_value_of_award, 0)) AS oblig,
            SUM(CASE WHEN competition_type IN ('C','G','NDO') THEN 1 ELSE 0 END) AS sole,
            SUM(CASE WHEN competition_type IN ('B','C','G','NDO') THEN 1 ELSE 0 END) AS noncomp,
            SUM(CASE WHEN number_of_offers = 1 THEN 1 ELSE 0 END) AS single_offer,
            SUM(CASE WHEN number_of_offers IS NOT NULL AND number_of_offers > 0 THEN 1 ELSE 0 END) AS offered,
            SUM(COALESCE(number_of_offers, 0)) AS total_offers
        FROM awards
        WHERE action_date IS NOT NULL AND action_date != ''
        GROUP BY year, month
    """)

    fy_data = defaultdict(lambda: {"n": 0, "oblig": 0.0, "sole": 0,
                                    "noncomp": 0, "single_offer": 0,
                                    "offered": 0, "total_offers": 0})
    for r in rows:
        fy = fy_of(f"{r['year']}-{r['month']}-01")
        if fy is None or fy < 2008 or fy > 2027:
            continue
        d = fy_data[fy]
        d["n"] += r["n"]
        d["oblig"] += r["oblig"] or 0
        d["sole"] += r["sole"] or 0
        d["noncomp"] += r["noncomp"] or 0
        d["single_offer"] += r["single_offer"] or 0
        d["offered"] += r["offered"] or 0
        d["total_offers"] += r["total_offers"] or 0

    out = []
    for fy in sorted(fy_data.keys()):
        d = fy_data[fy]
        n = d["n"]
        out.append({
            "fy": fy,
            "award_count": n,
            "total_obligation": round(d["oblig"], 2),
            "sole_source_count": d["sole"],
            "non_competed_count": d["noncomp"],
            "single_offer_count": d["single_offer"],
            "sole_source_rate": round(d["sole"] / n * 100, 2) if n else 0,
            "non_competed_rate": round(d["noncomp"] / n * 100, 2) if n else 0,
            "single_offer_rate": round(d["single_offer"] / d["offered"] * 100, 2)
                                  if d["offered"] else 0,
            "avg_offers": round(d["total_offers"] / d["offered"], 2)
                          if d["offered"] else 0,
        })
    return out


def build_top_vendors(conn, limit=25):
    rows = query(conn, """
        SELECT recipient_name AS name,
               recipient_uei AS uei,
               COUNT(*) AS award_count,
               SUM(COALESCE(current_total_value_of_award, 0)) AS total_obligation,
               SUM(CASE WHEN competition_type IN ('C','G','NDO') THEN 1 ELSE 0 END) AS sole_source_count
        FROM awards
        WHERE recipient_name IS NOT NULL AND recipient_name != ''
        GROUP BY recipient_name, recipient_uei
        ORDER BY total_obligation DESC
        LIMIT ?
    """, (limit,))
    for r in rows:
        r["total_obligation"] = round(r["total_obligation"] or 0, 2)
        r["sole_source_rate"] = round(
            (r["sole_source_count"] or 0) / r["award_count"] * 100, 2
        ) if r["award_count"] else 0
    return rows


def build_top_agencies(conn, limit=25):
    rows = query(conn, """
        SELECT awarding_agency AS name,
               COUNT(*) AS award_count,
               SUM(COALESCE(current_total_value_of_award, 0)) AS total_obligation,
               SUM(CASE WHEN competition_type IN ('C','G','NDO') THEN 1 ELSE 0 END) AS sole_source_count
        FROM awards
        WHERE awarding_agency IS NOT NULL AND awarding_agency != ''
        GROUP BY awarding_agency
        ORDER BY total_obligation DESC
        LIMIT ?
    """, (limit,))
    for r in rows:
        r["total_obligation"] = round(r["total_obligation"] or 0, 2)
        r["sole_source_rate"] = round(
            (r["sole_source_count"] or 0) / r["award_count"] * 100, 2
        ) if r["award_count"] else 0
    return rows


def build_top_naics(conn, limit=20):
    rows = query(conn, """
        SELECT naics_code AS code,
               COUNT(*) AS award_count,
               SUM(COALESCE(current_total_value_of_award, 0)) AS total_obligation
        FROM awards
        WHERE naics_code IS NOT NULL AND naics_code != ''
        GROUP BY naics_code
        ORDER BY total_obligation DESC
        LIMIT ?
    """, (limit,))
    for r in rows:
        r["total_obligation"] = round(r["total_obligation"] or 0, 2)
    return rows


def build_top_psc(conn, limit=20):
    rows = query(conn, """
        SELECT psc_code AS code,
               COUNT(*) AS award_count,
               SUM(COALESCE(current_total_value_of_award, 0)) AS total_obligation
        FROM awards
        WHERE psc_code IS NOT NULL AND psc_code != ''
        GROUP BY psc_code
        ORDER BY total_obligation DESC
        LIMIT ?
    """, (limit,))
    for r in rows:
        r["total_obligation"] = round(r["total_obligation"] or 0, 2)
    return rows


def build_competition_by_fy(conn):
    """Stacked bar: competition_type distribution by FY."""
    rows = query(conn, """
        SELECT substr(action_date, 1, 4) AS year,
               substr(action_date, 6, 2) AS month,
               competition_type AS code,
               COUNT(*) AS n,
               SUM(COALESCE(current_total_value_of_award, 0)) AS oblig
        FROM awards
        WHERE action_date IS NOT NULL AND action_date != ''
        GROUP BY year, month, code
    """)
    by_fy = defaultdict(lambda: defaultdict(lambda: {"n": 0, "oblig": 0.0}))
    for r in rows:
        fy = fy_of(f"{r['year']}-{r['month']}-01")
        if fy is None or fy < 2008 or fy > 2027:
            continue
        code = (r["code"] or "OTHER").strip() or "OTHER"
        d = by_fy[fy][code]
        d["n"] += r["n"]
        d["oblig"] += r["oblig"] or 0

    out = []
    for fy in sorted(by_fy.keys()):
        entry = {"fy": fy, "by_code": {}}
        for code, d in by_fy[fy].items():
            entry["by_code"][code] = {
                "label": COMPETITION_LABELS.get(code, code),
                "count": d["n"],
                "obligation": round(d["oblig"], 2),
            }
        out.append(entry)
    return out


def build_pricing_by_fy(conn):
    """Stacked: pricing-type bucket by FY."""
    rows = query(conn, """
        SELECT substr(action_date, 1, 4) AS year,
               substr(action_date, 6, 2) AS month,
               type_of_contract AS pricing,
               COUNT(*) AS n,
               SUM(COALESCE(current_total_value_of_award, 0)) AS oblig
        FROM awards
        WHERE action_date IS NOT NULL AND action_date != ''
        GROUP BY year, month, pricing
    """)
    by_fy = defaultdict(lambda: defaultdict(lambda: {"n": 0, "oblig": 0.0}))
    for r in rows:
        fy = fy_of(f"{r['year']}-{r['month']}-01")
        if fy is None or fy < 2008 or fy > 2027:
            continue
        bucket = PRICING_LOOKUP.get((r["pricing"] or "").strip(), "Other")
        d = by_fy[fy][bucket]
        d["n"] += r["n"]
        d["oblig"] += r["oblig"] or 0

    buckets = ["FFP", "T&M / LH", "Cost-reimbursement", "Other"]
    out = []
    for fy in sorted(by_fy.keys()):
        entry = {"fy": fy}
        for b in buckets:
            d = by_fy[fy].get(b, {"n": 0, "oblig": 0.0})
            entry[b] = {"count": d["n"], "obligation": round(d["oblig"], 2)}
        out.append(entry)
    return out


def main():
    print(f"opening {DB_PATH}...", flush=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    t0 = time.time()
    total_awards = conn.execute("SELECT COUNT(*) FROM awards").fetchone()[0]
    print(f"  awards in DB: {total_awards:,}", flush=True)

    print("building FY summary...", flush=True)
    fy_summary = build_fy_summary(conn)

    print("building top vendors...", flush=True)
    top_vendors = build_top_vendors(conn)

    print("building top agencies...", flush=True)
    top_agencies = build_top_agencies(conn)

    print("building top NAICS / PSC...", flush=True)
    top_naics = build_top_naics(conn)
    top_psc = build_top_psc(conn)

    print("building competition by FY...", flush=True)
    competition_by_fy = build_competition_by_fy(conn)

    print("building pricing by FY...", flush=True)
    pricing_by_fy = build_pricing_by_fy(conn)

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_awards_in_db": total_awards,
        "fy_summary": fy_summary,
        "top_vendors": top_vendors,
        "top_agencies": top_agencies,
        "top_naics": top_naics,
        "top_psc": top_psc,
        "competition_by_fy": competition_by_fy,
        "pricing_by_fy": pricing_by_fy,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"\nwrote {OUT_PATH} ({size_kb:.1f} KB) in {time.time()-t0:.1f}s",
          flush=True)
    print(f"  FY rows: {len(fy_summary)}, vendors: {len(top_vendors)}, "
          f"agencies: {len(top_agencies)}", flush=True)


if __name__ == "__main__":
    main()
