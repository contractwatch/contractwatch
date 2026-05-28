#!/usr/bin/env python3
"""Generate operators_guide.html, the visual companion to README.md.

Pulls live data from engine.structural_filter so the rendered filter
sections never drift from the code. Output is a single self-contained
HTML file at the repo root.

Usage:
    uv run python tools/build_readme.py
"""

import html
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config import (
    CRITICAL_SOLE_SOURCE_MIN, FIRST_LARGE_AWARD_MIN, MIN_OBLIGATION,
    BACKFILL_DAYS, CLOUDFLARE_PROJECT,
)
from engine.structural_filter import (
    STRUCTURAL_RULES, CURATED_SAFE_RECIPIENT_NAMES,
    MAJOR_PRIME_NAME_PREFIXES, MAJOR_COMMERCIAL_BRAND_PREFIXES,
    ANC_TRIBAL_NAME_PREFIXES, US_UTILITY_NAME_PATTERNS,
    UTILITY_DESCRIPTION_PATTERNS, GOVERNMENT_RECIPIENT_PATTERNS,
    GOVERNMENT_RECIPIENT_CONTAINS, FOREIGN_ENTITY_PATTERNS,
    FOREIGN_DESCRIPTION_PATTERNS, HEALTHCARE_PROVIDER_PATTERNS,
    NATIONAL_LAB_OPERATOR_PATTERNS,
    PUBLISH_FILTERS,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Data-array lists that have been hand-audited and cleared. Rendered with a
# green border + checkmark in the HTML so the operator can see at a glance
# which categories still need review.
AUDITED_LISTS = {
    "NATIONAL_LAB_OPERATOR_PATTERNS",
    "ANC_TRIBAL_NAME_PREFIXES",
    "MAJOR_COMMERCIAL_BRAND_PREFIXES",
    "MAJOR_PRIME_NAME_PREFIXES",
    "CURATED_SAFE_RECIPIENT_NAMES",
    "FOREIGN_ENTITY_PATTERNS",
    "FOREIGN_DESCRIPTION_PATTERNS",
    "US_UTILITY_NAME_PATTERNS",
    "UTILITY_DESCRIPTION_PATTERNS",
    "GOVERNMENT_RECIPIENT_PATTERNS",
    "GOVERNMENT_RECIPIENT_CONTAINS",
    "HEALTHCARE_PROVIDER_PATTERNS",
}

# Structural rules and publish filters that have been audited. Rule cards
# matching these IDs get the same green-cleared treatment as audited lists.
AUDITED_RULES = {
    "action_date_too_old",
    "major_prime_structural",
    "anc_tribal_subsidiary",
    "national_lab_operator",
    "curated_safe_recipient",
    "structural_pre_existing_relationship",
    "joint_venture_structural",
    "government_facility_operator",
    "excluded_agency",
    "doe_remediation",
    "heavy_construction",
    "obligation_out_of_band",
    "obligation_placeholder_ceiling",
    "bridge_contract_extension",
}

# Plain-language match logic per rule. Lets the README show the actual
# condition each rule evaluates instead of forcing the reader to parse a
# prose rationale to figure out what fires it.
RULE_LOGIC = {
    "major_prime_structural":
        "recipient_name.startswith(any prefix in MAJOR_PRIME_NAME_PREFIXES)",
    "structural_pre_existing_relationship":
        "is_major_commercial_brand(award)\n  OR is_us_utility(award)\n  OR is_government_recipient(award)\n  OR is_foreign_entity(award)\n  OR is_healthcare_provider(award)",
    "bridge_contract_extension":
        "any pattern in BRIDGE_CONTRACT_DESCRIPTION_PATTERNS is a substring of description",
    "joint_venture_structural":
        "recipient_name matches regex:  \\b(JV|JOINT VENTURE)\\b  |  \\bTEAM,?\\s+LLC\\b",
    "anc_tribal_subsidiary":
        "recipient_name.startswith(any prefix in ANC_TRIBAL_NAME_PREFIXES)",
    "government_facility_operator":
        "psc_code.startswith('M')\n  AND current_total_value_of_award >= $100,000,000",
    "national_lab_operator":
        "any pattern in NATIONAL_LAB_OPERATOR_PATTERNS is a substring of recipient_name",
    "excluded_agency":
        "awarding_agency in CONTRACTWATCH_EXCLUDED_AGENCIES  (env var, default empty)",
    "doe_remediation":
        "naics_code == '562910'\n  AND awarding_agency == 'Department of Energy'",
    "heavy_construction":
        "naics_code.startswith('236')  OR  naics_code.startswith('237')",
    "obligation_out_of_band":
        "current_total_value_of_award < $1,000,000  OR  current_total_value_of_award > $10,000,000,000",
    "obligation_placeholder_ceiling":
        "$999,000,000 <= current_total_value_of_award <= $1,000,100,000",
    "curated_safe_recipient":
        "recipient_name.upper() in CURATED_SAFE_RECIPIENT_NAMES  (exact match)",
    "action_date_too_old":
        "action_date < '2017-10-01'",
}


def esc(s):
    return html.escape(str(s))


def m(n):
    return f"${n // 1_000_000}M"


def list_block(title, items, sample_size=None):
    """Render a collapsible <details> matching the .flag-card / awardsfold
    pattern on contractwatch.org. Cards for lists in AUDITED_LISTS get a
    green left border + checkmark badge so cleared categories are visually
    distinct from those still pending review."""
    items = sorted(items, key=lambda x: x.upper())
    count = len(items)
    rows = "".join(f'<li>{esc(it)}</li>' for it in items)
    # Title may include a parenthetical suffix; the base name is the lookup key
    base_name = title.split(" (")[0].strip()
    audited = base_name in AUDITED_LISTS
    extra_class = " audited" if audited else ""
    badge = '<span class="audited-badge" title="Reviewed and cleared">&check; audited</span>' if audited else ""
    return f"""
<details class="flag-card list-card{extra_class}">
  <summary>
    <span class="afchev" aria-hidden="true">&#9656;</span>
    <span class="list-title">{esc(title)}</span>
    {badge}
    <span class="count">{count}&nbsp;entries</span>
    <span class="aftoggle-label">[expand]</span>
  </summary>
  <div class="flag-card-body">
    <ul class="entity-list">{rows}</ul>
  </div>
</details>"""


def rule_card(rule):
    """Expandable card per StructuralRule or PublishFilter. Leads with the
    actual match logic so the reader can see at a glance what fires the rule;
    the prose rationale is shown below as supporting context."""
    applies = ", ".join(rule.applies_to_flags) if hasattr(rule, "applies_to_flags") else "export layer"
    audited = rule.id in AUDITED_RULES
    extra_class = " audited" if audited else ""
    badge = '<span class="audited-badge" title="Reviewed and cleared">&check; audited</span>' if audited else ""
    logic = RULE_LOGIC.get(rule.id, "(see source)")
    action = "Strips flags" if hasattr(rule, "applies_to_flags") else "Drops award from latest.json"
    return f"""
<details class="flag-card{extra_class}">
  <summary>
    <span class="afchev" aria-hidden="true">&#9656;</span>
    <span class="rule-id">{esc(rule.id)}</span>
    <span class="rule-applies">{esc(applies)}</span>
    {badge}
    <span class="aftoggle-label">[expand]</span>
  </summary>
  <div class="flag-card-body">
    <p class="rule-desc">{esc(rule.description)}</p>
    <div class="rule-logic-block">
      <div class="rule-logic-label">{esc(action)} when</div>
      <pre class="rule-logic"><code>{esc(logic)}</code></pre>
    </div>
    <details class="rule-rationale-fold">
      <summary>Why this rule exists</summary>
      <p class="rule-rationale">{esc(rule.rationale)}</p>
      <div class="rule-meta">added {esc(rule.added_date)}</div>
    </details>
  </div>
</details>"""


def repo_tree():
    """Render the file tree, excluding gitignored paths."""
    # Mirrors README.md project layout but renders as a styled tree.
    tree = [
        ("contractwatch/", "repo root", True),
        ("├── README.md", "human-readable overview", False),
        ("├── operators_guide.html", "this file: visual companion to README.md", False),
        ("├── LICENSE", "MIT", False),
        ("├── pyproject.toml", "uv-managed deps (only `requests`)", False),
        ("├── uv.lock", "pinned dependency lockfile", False),
        ("├── .env.example", "optional env-var overrides; all keys optional", False),
        ("├── .gitignore", "blocks DB, archives, .env, .venv, cache, web/data", False),
        ("├── wrangler.toml", "Cloudflare Pages config", False),
        ("│", "", False),
        ("├── scan.py", "CLI: live scan via USASpending API", False),
        ("├── export_json.py", "build web/data/latest.json and stats.json", False),
        ("├── daily_scan.sh", "launchd-friendly catch-up + export + deploy", False),
        ("│", "", False),
        ("├── engine/", "core flag pipeline", True),
        ("│   ├── __init__.py", "", False),
        ("│   ├── config.py", "thresholds, paths, .env loader", False),
        ("│   ├── db.py", "SQLite schema + helpers", False),
        ("│   ├── usaspending.py", "USASpending HTTP client (transport only)", False),
        ("│   ├── normalize.py", "award normalization (business logic)", False),
        ("│   ├── scanner.py", "live scan engine", False),
        ("│   ├── flags.py", "F01/F02/F03 definitions + detail formatters", False),
        ("│   └── structural_filter.py", "rules + curated safe-recipient list", False),
        ("│", "", False),
        ("├── tools/", "operator scripts", True),
        ("│   ├── bulk_loader.py", "load USASpending archive ZIPs into the DB", False),
        ("│   ├── jobs.example.json", "example jobs file for bulk_loader.py", False),
        ("│   ├── reflag_all.py", "bulk SQL re-flag of the full DB (~1.5s)", False),
        ("│   └── build_readme.py", "generate this operators_guide.html", False),
        ("│", "", False),
        ("├── launchd/", "macOS launchd templates", True),
        ("│   └── com.contractwatch.plist.example", "launchd job template (rename, edit paths, then `launchctl load`)", False),
        ("│", "", False),
        ("└── web/", "static dashboard (served by Cloudflare Pages)", True),
        ("    ├── index.html", "main flagged-awards view", False),
        ("    ├── loader.html", "live bulk-loader status (polls loader_status.json)", False),
        ("    ├── llms.txt", "machine-readable site description", False),
        ("    ├── robots.txt, sitemap.xml", "SEO", False),
        ("    ├── favicons, social-preview.png", "branding", False),
        ("    └── data/", "generated JSON (gitignored; regenerate via export_json.py)", True),
    ]
    rows = []
    for line, note, is_dir in tree:
        cls = "tree-dir" if is_dir else "tree-file"
        note_html = f'<span class="tree-note">{esc(note)}</span>' if note else ""
        rows.append(f'<div class="tree-row"><span class="{cls}">{esc(line)}</span>{note_html}</div>')
    return "\n".join(rows)


def gitignored_block():
    items = [
        (".env", "your local env (gitignored; .env.example is the template)"),
        (".venv/", "Python virtualenv from `uv sync`"),
        ("__pycache__/", "Python bytecode cache"),
        ("contractwatch.db, *.db-*", "SQLite database, ~9 GB after a full build (rebuild with tools/bulk_loader.py)"),
        ("data/archives/", "Bulk USASpending FY archive zips (download fresh from files.usaspending.gov)"),
        ("web/data/", "Generated dashboard JSON (regenerate with export_json.py)"),
        (".wrangler/", "Cloudflare Wrangler local cache"),
        ("launchd/com.contractwatch.plist", "Your activated launchd job (the .example version IS tracked)"),
        ("cache/", "HTTP response cache (regenerated by scanner on demand)"),
    ]
    rows = "".join(
        f'<div class="ignored-row"><code>{esc(name)}</code><span class="ignored-note">{esc(note)}</span></div>'
        for name, note in items
    )
    return rows


def main():
    generated = date.today().isoformat()

    rules_html = "\n".join(rule_card(r) for r in STRUCTURAL_RULES if r.enabled)

    arrays_html = "\n".join([
        list_block("CURATED_SAFE_RECIPIENT_NAMES", CURATED_SAFE_RECIPIENT_NAMES),
        list_block("MAJOR_PRIME_NAME_PREFIXES", MAJOR_PRIME_NAME_PREFIXES),
        list_block("MAJOR_COMMERCIAL_BRAND_PREFIXES", MAJOR_COMMERCIAL_BRAND_PREFIXES),
        list_block("ANC_TRIBAL_NAME_PREFIXES", ANC_TRIBAL_NAME_PREFIXES),
        list_block("NATIONAL_LAB_OPERATOR_PATTERNS", NATIONAL_LAB_OPERATOR_PATTERNS),
        list_block("US_UTILITY_NAME_PATTERNS", US_UTILITY_NAME_PATTERNS),
        list_block("UTILITY_DESCRIPTION_PATTERNS", UTILITY_DESCRIPTION_PATTERNS),
        list_block("FOREIGN_ENTITY_PATTERNS", FOREIGN_ENTITY_PATTERNS),
        list_block("FOREIGN_DESCRIPTION_PATTERNS", FOREIGN_DESCRIPTION_PATTERNS),
        list_block("GOVERNMENT_RECIPIENT_PATTERNS (startswith)", GOVERNMENT_RECIPIENT_PATTERNS),
        list_block("GOVERNMENT_RECIPIENT_CONTAINS (substring)", GOVERNMENT_RECIPIENT_CONTAINS),
        list_block("HEALTHCARE_PROVIDER_PATTERNS", HEALTHCARE_PROVIDER_PATTERNS),
    ])

    publish_html = "\n".join(rule_card(pf) for pf in PUBLISH_FILTERS if pf.enabled)

    tree_html = repo_tree()
    ignored_html = gitignored_block()

    total_safelist = (len(CURATED_SAFE_RECIPIENT_NAMES) + len(MAJOR_PRIME_NAME_PREFIXES)
                      + len(MAJOR_COMMERCIAL_BRAND_PREFIXES) + len(ANC_TRIBAL_NAME_PREFIXES))

    html_out = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ContractWatch. Operator's guide.</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --bg: #f5f5f5;          /* page background: true neutral light gray */
  --bg2: #e5e5e5;         /* slightly stronger neutral gray */
  --panel: #ffffff;       /* cards / boxes: PURE WHITE, contrasts khaki bg */
  --panel2: #ffffff;      /* secondary panel: also pure white (no cream tint) */
  --line: #d4d4d8;        /* borders: cool neutral gray, breaks the warm bg */
  --line-strong: #a1a1aa; /* stronger neutral gray for box edges */
  --text: #1a1612;        /* dark warm text */
  --muted: #6b6055;       /* secondary text (warm gray) */
  --accent: #b91c1c;      /* red, brand continuity */
  --accent2: #1e3a5f;     /* navy */
  --bad: #991b1b;
  --high: #b45309;
  --med: #1e40af;
  --good: #14532d;
  --shadow: 0 1px 3px rgba(0, 0, 0, 0.07), 0 4px 12px rgba(0, 0, 0, 0.05);
  --serif: "Charter", "Source Serif Pro", Georgia, "Times New Roman", serif;
  --sans: -apple-system, BlinkMacSystemFont, "Inter", "SF Pro Text", system-ui, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}}
* {{ box-sizing: border-box; }}
html, body {{ background: var(--bg); color: var(--text); margin: 0; padding: 0; }}
body {{ font-family: var(--sans); font-size: 15.5px; line-height: 1.55; }}
a {{ color: var(--accent); text-decoration: underline; text-underline-offset: 2px; }}
a:hover {{ color: var(--bad); }}
code, .mono {{
  font-family: var(--mono); font-size: 0.92em;
  background: #dbeafe;        /* light blue */
  border: 1px solid #93c5fd;  /* matching blue border */
  color: #1e3a8a;             /* dark blue text */
  padding: 1px 6px;
  border-radius: 3px;
}}
pre code {{ background: none; border: none; padding: 0; color: inherit; }}

/* --- Header: matches contractwatch.org exactly --- */
header.top {{
  background: var(--bg);
  padding: 26px 40px 18px;
}}
header.top .topinner {{
  max-width: 1080px; margin: 0 auto;
  display: flex; align-items: center; justify-content: space-between; gap: 24px;
}}
h1.brand {{
  font-family: var(--serif);
  font-size: 38px;
  font-weight: 700;
  letter-spacing: -0.5px;
  line-height: 0.95;
  color: var(--text);
  margin: 0;
}}
h1.brand span {{ color: var(--accent); font-style: italic; }}
.brand-tagline {{
  margin-top: 6px;
  font-family: var(--mono);
  font-size: 12.5px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}
.one-liner {{
  max-width: 1080px; margin: 14px auto 0;
  padding: 0 40px;
  font-family: var(--serif);
  font-size: 17px;
  color: var(--text);
}}

/* --- Stat strip: now a connected metric grid like the site --- */
.metric-grid {{
  max-width: 1080px; margin: 22px auto 0;
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 0;
  border: 1px solid var(--line-strong);
  box-shadow: var(--shadow);
}}
.metric-grid .card:nth-child(3n) {{ border-right: none; }}
.metric-grid .card:nth-last-child(-n+3) {{ border-bottom: none; }}
@media (max-width: 720px) {{
  .metric-grid {{ grid-template-columns: repeat(2, 1fr); }}
  .metric-grid .card:nth-child(3n) {{ border-right: 1px solid var(--line); }}
  .metric-grid .card:nth-child(2n) {{ border-right: none; }}
  .metric-grid .card:nth-last-child(-n+3) {{ border-bottom: 1px solid var(--line-strong); }}
  .metric-grid .card:nth-last-child(-n+2) {{ border-bottom: none; }}
}}
.metric-grid .card {{
  background: var(--panel);
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line-strong);
  padding: 18px 22px;
  transition: background 0.15s;
}}
.metric-grid .card:last-child {{ border-right: none; }}
.metric-grid .card:hover {{ background: var(--panel2); }}
.metric-grid .card .label {{
  font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px;
  color: var(--muted); font-weight: 700;
}}
.metric-grid .card .num {{
  font-family: var(--serif); font-size: 36px; font-weight: 700;
  margin-top: 6px; letter-spacing: -1px; line-height: 1;
  color: var(--text);
}}
.metric-grid .card .num.small {{ font-size: 24px; letter-spacing: -0.5px; }}

nav.tabs {{
  position: sticky;
  top: 0;
  z-index: 10;
  background: var(--bg);
  padding: 0 40px;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  justify-content: center;
  max-width: 1080px;
  margin: 0 auto;
}}
nav.tabs button {{
  background: transparent;
  border: none;
  border-bottom: 3px solid transparent;
  padding: 14px 18px 12px;
  cursor: pointer;
  font-family: var(--sans);
  font-size: 14px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.2px;
  white-space: nowrap;
}}
nav.tabs button:hover {{ color: var(--text); }}
nav.tabs button.active {{ color: var(--accent); border-bottom-color: var(--accent); }}

main {{ padding: 28px 40px 80px; max-width: 1080px; margin: 0 auto; }}
section.tab {{ display: none; }}
section.tab.active {{ display: block; }}

h2.sec {{
  font-family: var(--serif);
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.3px;
  color: var(--text);
  margin: 0 0 22px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line-strong);
  text-align: left;
}}
h2.sec::before {{
  content: ""; display: inline-block; width: 4px; height: 22px;
  background: var(--accent); margin-right: 12px; vertical-align: -3px;
}}
h3 {{
  font-family: var(--serif);
  font-size: 19px;
  font-weight: 700;
  margin: 28px 0 10px;
  color: var(--text);
}}
p {{ margin: 8px 0; max-width: 820px; }}

table {{
  border-collapse: collapse;
  width: 100%;
  margin: 14px 0;
  background: var(--panel);
  box-shadow: var(--shadow);
}}
th, td {{
  border: 1px solid var(--line);
  padding: 9px 11px;
  text-align: left;
  vertical-align: top;
  font-size: 14.5px;
}}
th {{ background: var(--panel); font-family: var(--serif); font-weight: 700; border-bottom: 2px solid var(--line-strong); }}
td.flag-code {{ font-family: var(--mono); font-weight: 700; color: var(--bad); white-space: nowrap; }}
td.dollar {{ font-family: var(--mono); white-space: nowrap; }}

pre {{
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  padding: 16px 18px;
  overflow-x: hidden;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-wrap: anywhere;
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.55;
  box-shadow: var(--shadow);
}}

/* Tree */
.tree {{
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  padding: 18px 20px;
  font-family: var(--mono);
  font-size: 13px;
  box-shadow: var(--shadow);
}}
.tree-row {{
  display: flex;
  gap: 14px;
  align-items: baseline;
  padding: 1px 0;
}}
.tree-dir {{ color: var(--accent2); font-weight: 700; }}
.tree-file {{ color: var(--text); }}
.tree-note {{ color: var(--muted); font-family: var(--sans); font-size: 13px; font-style: italic; }}

.ignored-block {{
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  padding: 16px 18px;
  margin-top: 8px;
  box-shadow: var(--shadow);
}}
.ignored-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: baseline;
  padding: 4px 0;
  border-bottom: 1px dotted var(--line);
}}
.ignored-row:last-child {{ border-bottom: none; }}
.ignored-row code {{ flex: 0 0 auto; font-size: 12.5px; }}
.ignored-note {{ color: var(--muted); font-size: 13.5px; }}

/* Expand/collapse cards: matches contractwatch.org .flag-card pattern */
details.flag-card {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-left: 4px solid var(--bad);
  border-radius: 4px;
  margin: 10px 0;
  padding: 16px 22px;
  box-shadow: var(--shadow);
}}
details.flag-card.list-card {{ border-left-color: var(--accent2); }}
details.flag-card.audited {{
  border-left-color: #15803d;        /* green-700 */
  background: #f0fdf4;                /* green-50 tinted card to read as cleared */
}}
.audited-badge {{
  display: inline-block;
  font-family: var(--mono);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.4px;
  text-transform: uppercase;
  color: #166534;                     /* green-800 */
  background: #dcfce7;                /* green-100 */
  border: 1px solid #86efac;          /* green-300 */
  padding: 2px 7px;
  border-radius: 3px;
  margin-right: 6px;
  flex: none;
}}
details.flag-card > summary {{
  list-style: none; cursor: pointer; padding: 0;
  display: flex; align-items: center; gap: 12px;
}}
details.flag-card > summary::-webkit-details-marker {{ display: none; }}
details.flag-card > summary .afchev {{
  display: inline-block; color: var(--text); font-size: 22px;
  line-height: 1; transition: transform 0.2s ease;
  flex: none;
}}
details.flag-card[open] > summary .afchev {{ transform: rotate(90deg); }}
details.flag-card > summary .rule-id,
details.flag-card > summary .list-title {{
  font-family: var(--mono); font-size: 14.5px; font-weight: 700;
  color: var(--text); letter-spacing: 0.3px;
}}
details.flag-card > summary .rule-applies {{
  font-family: var(--mono); font-size: 11.5px; color: var(--muted);
  flex: 1;
}}
details.flag-card > summary .count {{
  font-family: var(--mono); font-size: 12px; color: var(--muted);
  flex: 1; text-align: right; padding-right: 8px;
}}
details.flag-card > summary .aftoggle-label {{
  font-family: var(--mono); font-size: 12.5px; font-weight: 500;
  color: var(--muted); letter-spacing: 0.3px; margin-left: auto;
  flex: none;
}}
details.flag-card[open] > summary .aftoggle-label::before {{ content: "[collapse]"; }}
details.flag-card[open] > summary .aftoggle-label {{ font-size: 0; }}
details.flag-card[open] > summary .aftoggle-label::before {{ font-size: 12.5px; }}
details.flag-card > .flag-card-body {{ margin-top: 14px; }}
.rule-desc {{
  font-family: var(--serif); font-size: 16px; font-weight: 700;
  color: var(--text); margin: 0 0 10px;
}}
.rule-rationale {{
  font-family: var(--serif); font-size: 14.5px; line-height: 1.6;
  color: var(--text); margin: 0 0 10px;
}}
.rule-meta {{ font-family: var(--mono); font-size: 11.5px; color: var(--muted); margin-top: 6px; }}

/* Match-logic block inside a rule card. Leads the body so readers see
   the actual condition before the prose rationale. */
.rule-logic-block {{ margin: 6px 0 10px; }}
.rule-logic-label {{
  font-family: var(--mono); font-size: 11px; font-weight: 700;
  letter-spacing: 0.5px; text-transform: uppercase;
  color: var(--muted); margin-bottom: 6px;
}}
pre.rule-logic {{
  margin: 0;
  padding: 12px 14px;
  background: #1e3a5f;        /* navy accent2: code stands out */
  color: #f8fafc;
  border: none;
  border-radius: 4px;
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-wrap: anywhere;
  box-shadow: none;
}}
pre.rule-logic code {{ background: none; color: inherit; border: none; padding: 0; }}

/* Rationale folded inside the card so it's optional, not the lead. */
details.rule-rationale-fold {{
  margin-top: 8px;
  padding: 0;
  border: 1px dashed var(--line-strong);
  border-radius: 4px;
  background: #fafafa;
}}
details.rule-rationale-fold > summary {{
  cursor: pointer;
  list-style: none;
  padding: 8px 12px;
  font-family: var(--mono);
  font-size: 11.5px;
  font-weight: 700;
  letter-spacing: 0.4px;
  text-transform: uppercase;
  color: var(--muted);
}}
details.rule-rationale-fold > summary::-webkit-details-marker {{ display: none; }}
details.rule-rationale-fold > summary::before {{
  content: "▸ ";
  color: var(--muted);
  font-size: 10px;
  margin-right: 4px;
  transition: transform 0.15s ease;
  display: inline-block;
}}
details.rule-rationale-fold[open] > summary::before {{ transform: rotate(90deg); }}
details.rule-rationale-fold p.rule-rationale {{
  margin: 0;
  padding: 0 14px 12px;
  font-family: var(--serif);
  font-size: 14px;
  line-height: 1.55;
  color: var(--text);
}}
details.rule-rationale-fold .rule-meta {{ padding: 0 14px 12px; }}

/* Explainer callout sitting above the structural rules. */
.rules-explainer {{
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-left: 4px solid var(--accent2);
  border-radius: 4px;
  padding: 16px 20px;
  margin: 12px 0 20px;
  box-shadow: var(--shadow);
}}
.rules-explainer h4 {{
  margin: 0 0 8px;
  font-family: var(--serif);
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
}}
.rules-explainer p {{
  margin: 6px 0;
  font-family: var(--serif);
  font-size: 14.5px;
  line-height: 1.55;
  color: var(--text);
}}
.rules-explainer code {{ font-size: 12.5px; }}
.entity-list {{
  max-height: 420px;
  overflow-y: auto;
  overflow-x: hidden;
  margin: 0;
  padding: 10px 18px 10px 28px;
  list-style: square;
  font-family: var(--mono);
  font-size: 12.5px;
  color: var(--text);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 3px;
}}
.entity-list li {{ padding: 1px 0; word-break: break-word; }}

.callout {{
  background: var(--panel);
  border-left: 3px solid var(--accent);
  padding: 12px 16px;
  margin: 16px 0;
  font-family: var(--serif);
  font-size: 15px;
}}

.footer {{
  margin-top: 60px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
  font-family: var(--mono);
  font-size: 11.5px;
  color: var(--muted);
}}

@media (max-width: 720px) {{
  header {{ padding: 22px 18px 16px; }}
  nav.tabs {{ padding: 0 18px; }}
  main {{ padding: 22px 18px 60px; }}
  .headline h1 {{ font-size: 32px; }}
  .entity-list {{ column-count: 1; }}
}}
</style>
</head>
<body>

<header class="top">
  <div class="topinner">
    <div>
      <h1 class="brand">contract<span>watch</span></h1>
      <div class="brand-tagline">Operator's guide</div>
    </div>
  </div>
</header>

<div class="one-liner">
  Screens every federal prime contract above ${MIN_OBLIGATION // 1_000_000}M against three patterns and surfaces the survivors of a structural filter. Source data is USASpending.gov. Output is a static dashboard at <a href="https://contractwatch.org">contractwatch.org</a>.
</div>

<div class="metric-grid">
  <div class="card"><div class="label">Active flags</div><div class="num">3</div></div>
  <div class="card"><div class="label">Structural rules</div><div class="num">{len(STRUCTURAL_RULES)}</div></div>
  <div class="card"><div class="label">Curated safe names</div><div class="num">{len(CURATED_SAFE_RECIPIENT_NAMES):,}</div></div>
  <div class="card"><div class="label">Total filter entries</div><div class="num">{total_safelist:,}</div></div>
  <div class="card"><div class="label">F01 floor</div><div class="num small">{m(CRITICAL_SOLE_SOURCE_MIN)}</div></div>
  <div class="card"><div class="label">F02 / F03 floor</div><div class="num small">{m(FIRST_LARGE_AWARD_MIN)}</div></div>
</div>

<nav class="tabs">
  <button class="active" data-tab="overview">Overview</button>
  <button data-tab="repo">Repo layout</button>
  <button data-tab="loader">Loader &amp; data</button>
  <button data-tab="filter">Structural filter</button>
  <button data-tab="run">Setup &amp; run</button>
</nav>

<main>

<section class="tab active" id="tab-overview">
  <h2 class="sec">What ContractWatch does</h2>
  <p>
    ContractWatch reads federal prime-contract awards from USASpending.gov, the
    federal government's authoritative public spending dataset, and flags awards
    that match three anomaly patterns. Anything that fires a flag is then run
    through a structural filter that strips noise: defense-prime subsidiaries,
    joint ventures, ANC and tribal 8(a) firms, FFRDC operators, foreign-government
    recipients, US utilities, state and local governments, and a hand-curated list
    of recipients verified safe through review.
  </p>
  <p>
    Survivors of the filter are exported to a static JSON file and rendered by a
    Cloudflare Pages site. No accounts, no API keys, no closed-source services.
    The full pipeline runs locally on a laptop in under three minutes after the
    one-time database build.
  </p>

  <h3>The three flags</h3>
  <table>
    <thead><tr><th>Code</th><th>Pattern</th><th>Dollar floor</th></tr></thead>
    <tbody>
      <tr>
        <td class="flag-code">F01</td>
        <td>No prior federal contracts for this UEI plus sole-source award</td>
        <td class="dollar">{m(CRITICAL_SOLE_SOURCE_MIN)}</td>
      </tr>
      <tr>
        <td class="flag-code">F02</td>
        <td>No prior federal contracts plus competitive solicitation that received only one offer</td>
        <td class="dollar">{m(FIRST_LARGE_AWARD_MIN)}</td>
      </tr>
      <tr>
        <td class="flag-code">F03</td>
        <td>No prior federal contracts plus first contract (regardless of competition)</td>
        <td class="dollar">{m(FIRST_LARGE_AWARD_MIN)}</td>
      </tr>
    </tbody>
  </table>
  <p style="color: var(--muted); font-size: 14px;">
    A flag is descriptive, not accusatory. It marks an award that matches a
    pattern uncommon at the dollar threshold. Many flagged awards have routine
    explanations, which is exactly what the structural filter is for.
  </p>

  <h3>How a single award flows through the pipeline</h3>
<pre><code>USASpending bulk archives (annual FY zips)
        |
        v
  tools/bulk_loader.py          (one-time / monthly rebuild)
        |
        v
  contractwatch.db              (SQLite, single file, ~9 GB)
        |
        v
  tools/reflag_all.py           (1.5 sec full re-flag)
   uses engine/flags.py + engine/structural_filter.py
        |
        v
  flags table                   (only structural-filter survivors)
        |
        v
  export_json.py                ->  web/data/latest.json, stats.json
        |
        v
  web/index.html                ->  Cloudflare Pages  ->  contractwatch.org</code></pre>
  <p>
    Daily catch-up runs through <code>scan.py</code> and <code>daily_scan.sh</code>,
    pulling only newly modified awards from USASpending's API. The bulk archives
    are touched once at initial build and again roughly monthly to refresh.
  </p>

  <h3>Design principles</h3>
  <ul>
    <li><strong>A fired flag is descriptive, not a finding.</strong> Many flagged awards have routine explanations and are best read as worth a closer look. Every flagged award links to its USASpending record so readers can review the underlying contract directly.</li>
    <li><strong>Structural patterns are stripped before publication.</strong> Awards that fire the flags by program design (8(a) sole-source, M&amp;O contracts, FFRDC operators, named JVs) are removed by the structural filter so the dashboard surfaces only the residual signal.</li>
    <li><strong>Ambiguous patterns stay visible.</strong> The filter only strips a flag when the structural explanation is clear. Anything not clearly mechanical remains on the dashboard for human review.</li>
    <li><strong>One SQLite file, no services.</strong> The full pipeline runs locally. The dashboard is regenerated as static HTML and JSON. No login, no backend, no external service beyond USASpending's public API.</li>
  </ul>

  <h3>Dashboard features</h3>
  <p>
    The dashboard at <a href="https://contractwatch.org">contractwatch.org</a> reads the static JSON files generated by <code>export_json.py</code> and renders them with the following features:
  </p>
  <ul>
    <li><strong>Sort and filter.</strong> Awards can be sorted by dollars, action date, or recipient name, and filtered by agency, state, NAICS, PSC, fiscal year, or flag code.</li>
    <li><strong>Per-award detail.</strong> Click any award to expand the full record: PIID, period of performance, contract type, competition mechanism, awarding office, the full description, and the list of flags that fired on it with their rationale text.</li>
    <li><strong>USASpending link.</strong> Every award includes a direct link to its public USASpending record so readers can verify the underlying contract data.</li>
    <li><strong>Export CSV.</strong> The "Export CSV" button in the dashboard header generates a CSV of the currently visible awards (respects active filters and sort order) and triggers a browser download. The file is named <code>contractwatch-awards-MMDDYY.csv</code> and contains 18 columns: <code>piid, recipient, uei, state, agency, office, obligation, action_date, start_date, end_date, contract_type, competition, naics, psc, description, flag_codes, flag_details, usaspending_url</code>. The CSV is generated client-side in the browser from the loaded JSON; no server round-trip, no separate export script to run.</li>
  </ul>
</section>

<section class="tab" id="tab-repo">
  <h2 class="sec">What you get when you clone the repo</h2>
  <p>
    The tree below is everything that lands in your checkout after
    <code>git clone</code>. Generated artifacts and your local database are
    rebuilt locally (see the <em>What is NOT in the repo</em> block at the
    bottom).
  </p>

  <div class="tree">
{tree_html}
  </div>

  <h3>What is NOT in the repo</h3>
  <p>
    The <code>.gitignore</code> blocks generated artifacts and machine-local
    state. Everything below is regenerated by the scripts in <code>tools/</code>
    or by the live scanner; you do not commit any of it.
  </p>
  <div class="ignored-block">
{ignored_html}
  </div>
</section>

<section class="tab" id="tab-loader">
  <h2 class="sec">Loader &amp; data</h2>
  <p>
    ContractWatch loads federal contract data from USASpending.gov in two paths: <code>tools/bulk_loader.py</code> (one-time / monthly rebuild from annual archive ZIPs) and <code>scan.py</code> (daily catch-up via USASpending API). Both paths write into the same <code>contractwatch.db</code> SQLite file using the schema below.
  </p>

  <h3>Why 19 columns out of 297</h3>
  <p>
    USASpending bulk CSV exposes 297 columns per row. ContractWatch loads <strong>19</strong>, plus 1 derived column (<code>sole_source</code>). This is the minimum set required by the three flags (F01/F02/F03). Loading the full 297 would bloat the database from ~10 GB to ~100-150 GB and triple the load time for no functional benefit.
  </p>
  <p>
    Forkers who want to add flags that need other USASpending fields edit <code>tools/bulk_loader.py</code> directly and rebuild. The dropped fields include 50+ socioeconomic flags, 30+ reps and certs fields, 15+ duplicate funding-hierarchy fields, free-text JOFOC and FAR-clause fields, etc. — useful for some flag ideas, but inert for the current three-flag design.
  </p>

  <h3>Schema (one row per <code>contract_award_unique_key</code>)</h3>
  <table>
    <thead><tr><th>DB column</th><th>Source field</th><th>Purpose</th></tr></thead>
    <tbody>
      <tr><td><code>award_id</code></td><td><code>contract_award_unique_key</code></td><td>Primary key</td></tr>
      <tr><td><code>generated_unique_award_id</code></td><td>same</td><td>Mirror, for export linking</td></tr>
      <tr><td><code>piid</code></td><td><code>award_id_piid</code></td><td>Procurement Instrument Identifier</td></tr>
      <tr><td><code>recipient_name</code></td><td><code>recipient_name</code></td><td>Vendor display + structural filter name matching</td></tr>
      <tr><td><code>recipient_uei</code></td><td><code>recipient_uei</code></td><td>Unique Entity Identifier (the no-prior-history pivot)</td></tr>
      <tr><td><code>recipient_address</code></td><td><code>recipient_address_line_1</code></td><td>Display only (no address-network flags exist)</td></tr>
      <tr><td><code>recipient_state</code></td><td><code>recipient_state_code</code></td><td>Display only</td></tr>
      <tr><td><code>awarding_agency</code></td><td><code>awarding_agency_name</code></td><td>Structural filter (excluded-agency rule + DOE remediation rule)</td></tr>
      <tr><td><code>awarding_office</code></td><td><code>awarding_office_name</code></td><td>Display only</td></tr>
      <tr><td><code>naics_code</code></td><td><code>naics_code</code></td><td>Structural filter (heavy construction, DOE remediation rules)</td></tr>
      <tr><td><code>psc_code</code></td><td><code>product_or_service_code</code></td><td>Structural filter (PSC-M operator rule)</td></tr>
      <tr><td><code>type_of_contract</code></td><td><code>type_of_contract_pricing</code></td><td>Display only</td></tr>
      <tr><td><code>competition_type</code></td><td><code>extent_competed_code</code></td><td>F02 competitive check + sole_source derivation</td></tr>
      <tr><td><code>number_of_offers</code></td><td><code>number_of_offers_received</code></td><td>F02 one-offer check</td></tr>
      <tr><td><code>current_total_value_of_award</code></td><td><code>current_total_value_of_award</code></td><td>The dollar threshold all three flags test against. See note below.</td></tr>
      <tr><td><code>action_date</code></td><td><code>action_date</code></td><td>No-prior-history pivot date + publish filter (FY18 cutoff)</td></tr>
      <tr><td><code>start_date</code></td><td><code>period_of_performance_start_date</code></td><td>Display only</td></tr>
      <tr><td><code>end_date</code></td><td><code>period_of_performance_current_end_date</code></td><td>Display only</td></tr>
      <tr><td><code>description</code></td><td><code>prime_award_base_transaction_description</code> (fallback: <code>transaction_description</code>)</td><td>Structural filter (bridge contract, foreign description, utility description rules)</td></tr>
      <tr><td><code>sole_source</code></td><td><strong>derived</strong> from <code>extent_competed_code</code></td><td>F01 sole-source check. Computed as <code>1</code> if code in <code>{{B, C, G, NDO, NNS, ONO, SP1, SP2, SS}}</code> else <code>0</code>.</td></tr>
    </tbody>
  </table>

  <div class="callout">
    <strong>Important: the dollar threshold tests filter on contract ceiling, not actual obligated dollars.</strong>
    <p>
      USASpending exposes two distinct dollar fields per award: <code>federal_action_obligation</code> (actual dollars obligated by this transaction) and <code>current_total_value_of_award</code> (the latest agreed total value of the award, i.e., the ceiling). ContractWatch loads only the ceiling and runs F01/F02/F03 against that value.
    </p>
    <p>
      <strong>Why ceiling, not obligation?</strong> The ceiling represents the government's commitment to a vendor. A fresh-UEI vendor winning a $697M-ceiling award is the structural pattern the three flags surface, even when initial obligation is much smaller. Filtering on actual obligation would weaken the no-prior-history signal because high-ceiling, low-initial-obligation awards are the dominant case the flags are designed to catch.
    </p>
    <p>
      <strong>Forking note:</strong> to build obligation-based flags, edit <code>tools/bulk_loader.py</code> to add <code>federal_action_obligation</code> as an additional column, rebuild the database, and reference the new field from any new flag function.
    </p>
  </div>

  <h3>Daily catch-up vs bulk rebuild</h3>
  <ul>
    <li><strong>Bulk rebuild</strong> (<code>tools/bulk_loader.py</code>): downloads annual FY archive ZIPs from USASpending, parses ~5M rows total, dedupes to one row per <code>contract_award_unique_key</code> keeping the latest <code>action_date</code>, and bulk-inserts. ~45 minutes, ~15 GB download, ~10 GB DB. Run once initially and roughly monthly to refresh.</li>
    <li><strong>Daily catch-up</strong> (<code>scan.py</code> / <code>daily_scan.sh</code>): hits the USASpending search API for awards whose <code>last_modified_date</code> falls within the trailing <code>CONTRACTWATCH_BACKFILL_DAYS</code> window (default 2). Upserts each into the same schema. Cheap, fast, no archive download.</li>
  </ul>

  <h3>MIN_OBLIGATION floor</h3>
  <p>
    <code>engine/config.py:MIN_OBLIGATION = 1_000_000</code>. Both load paths drop awards below this floor before they reach the database. This keeps the database focused on materially-sized awards. ~95% of federal contract actions are below $1M and get discarded at ingestion. If you want micro-purchase or purchase-card flags, lower this floor and rebuild.
  </p>

  <h3>Adding a column</h3>
  <ol>
    <li>Edit <code>tools/bulk_loader.py</code> <code>LOAD_COLUMNS</code> list and the corresponding INSERT statement to include the new field.</li>
    <li>Edit <code>engine/db.py</code> CREATE TABLE statement to add the column to the schema.</li>
    <li>Edit <code>engine/normalize.py</code> to populate the new field from the live USASpending API response.</li>
    <li>Run <code>tools/bulk_loader.py</code> against the archives to repopulate. New column will be NULL on old rows unless you also write a backfill query.</li>
    <li>Reference the new field from any new flag function or structural rule that needs it.</li>
  </ol>
</section>

<section class="tab" id="tab-filter">
  <h2 class="sec">The structural filter</h2>
  <p>
    The structural filter sits between the flag pipeline and the dashboard. It
    has two layers:
  </p>
  <ol>
    <li><strong>Structural rules</strong> ({len(STRUCTURAL_RULES)} active): a flag fires, but the result is stripped because the pattern is structural rather than anomalous.</li>
    <li><strong>Publish filters</strong> ({len(PUBLISH_FILTERS)} active): drop entire awards from <code>latest.json</code> at export time. Used to drop pre-FY18 action dates.</li>
  </ol>
  <p>
    Each rule below is independently toggleable in code. Click any card to see
    its rationale and the flags it applies to.
  </p>

  <h3>Structural rules</h3>

  <div class="rules-explainer">
    <h4>How a structural rule works</h4>
    <p>
      Every award that triggers a flag (F01, F02, or F03) gets run through these rules in order. Each rule has a <strong>match function</strong> that returns true or false based on properties of the award (recipient name, NAICS code, PSC code, dollar value, awarding agency, etc.). When the match returns true, the rule strips the flags listed in its <code>applies to</code> field. If all of an award's flags get stripped, the award disappears from the dashboard.
    </p>
    <p>
      Some rules use a <strong>data array</strong> (a curated list of prime names, ANC subsidiaries, etc.) as part of their match logic. Others are <strong>pure-logic gates</strong> with no list — they fire on NAICS codes, dollar thresholds, or other award properties directly. The "Strips flags when" code block on each rule below shows the actual condition.
    </p>
  </div>

  <h3>Tips for builders</h3>
  <p>
    Flags and the structural filter are paired by design. A flag is the <strong>positive signal</strong>: it catches a pattern. The structural filter is the <strong>negative signal</strong>: it strips patterns that fire mechanically rather than meaningfully (major-prime subsidiaries, named JVs, ANC sole-source, M&amp;O contracts, etc.). Neither is useful without the other.
  </p>
  <p>
    A flag without a paired structural filter is not a usable signal. Empirically, raw flag candidates outnumber surviving flags by 3:1 to 5:1 after structural filtering. Adding a flag without doing the matching filter work means dumping unreviewed noise onto the dashboard.
  </p>
  <p>
    Plan flags in groups, not singletons. F01/F02/F03 form one group targeting the no-prior-history large-award pattern and share structural-filter rules across all three. A new flag for a different anomaly category (pass-through, repeat sole-source clustering, bid concentration) should be planned as its own group with its own filter work for that category's noise.
  </p>
  <p>
    Test empirically before committing. Write the candidate SQL, apply the existing structural filter, inspect what survives. If most survivors are clearly legitimate, the signal-to-noise ratio is too low and the flag is not worth adding.
  </p>
  <p>
    Not every detectable pattern is worth detecting. Leaving a coverage gap acknowledged is better than filling it with noise.
  </p>

  <h3>Currently active rules</h3>

{rules_html}

  <h3>Publish filters</h3>
{publish_html}

  <h3>The data arrays</h3>
  <p>
    The structural rules call helper functions that compare an award's recipient
    name, address, NAICS, or PSC against the arrays below. Click any array to
    expand its current contents.
  </p>
{arrays_html}

  <div class="callout">
    To modify the filter: edit the arrays or rules in
    <code>engine/structural_filter.py</code>, then run
    <code>uv run python tools/reflag_all.py</code> to re-score the DB. No schema
    changes, no rebuild. To regenerate this page after a filter change, run
    <code>uv run python tools/build_readme.py</code>.
  </div>
</section>

<section class="tab" id="tab-run">
  <h2 class="sec">Setup &amp; run</h2>

  <h3>Prerequisites</h3>
  <p>
    Before starting, the following must be installed on the local machine.
  </p>
  <ul>
    <li>
      <strong>Python 3.11 or newer.</strong> Check the installed version with <code>python3 --version</code>. macOS ships with Python 3.9 in <code>/usr/bin/python3</code> which is too old; install a newer version via <a href="https://www.python.org/downloads/">python.org</a>, Homebrew (<code>brew install python@3.12</code>), or <code>uv python install 3.12</code>.
    </li>
    <li>
      <strong><a href="https://github.com/astral-sh/uv">uv</a></strong>, the Python package manager from Astral. Used in place of pip + virtualenv. Install on macOS or Linux with <code>curl -LsSf https://astral.sh/uv/install.sh | sh</code>, or on Windows with <code>powershell -c "irm https://astral.sh/uv/install.ps1 | iex"</code>. ContractWatch uses <code>uv</code> for environment isolation and dependency resolution because it is roughly 10x faster than pip and handles Python version installation in one tool.
    </li>
    <li>
      <strong>Approximately 25 GB of free disk space.</strong> Roughly 15 GB for the downloaded USASpending bulk archives and 9 GB for the SQLite database that gets built from them.
    </li>
    <li>
      <strong>Optional: <code>wrangler</code>.</strong> Cloudflare's CLI for deploying static sites to Cloudflare Pages. Only required if pushing the dashboard to a Cloudflare-hosted site. Install with <code>npm install -g wrangler</code>. The dashboard can also be served locally or from any other static host without wrangler.
    </li>
  </ul>

  <h3>From clone to live dashboard</h3>
  <p>
    Six steps. The full sequence takes roughly 50 minutes the first time, almost all of which is the bulk database build. Subsequent runs (reflag, export, view) take seconds.
  </p>

  <h4>Step 1. Install dependencies</h4>
  <p>
    From the repository root, run:
  </p>
<pre><code>uv sync</code></pre>
  <p>
    This creates a local virtual environment in <code>.venv/</code> and installs the project's Python dependencies (just <code>requests</code>, since ContractWatch deliberately keeps the dependency tree minimal). It also reads <code>pyproject.toml</code> for any Python-version constraints and downloads a matching interpreter if needed.
  </p>

  <h4>Step 2. Configure overrides (optional)</h4>
  <p>
    ContractWatch ships with reasonable defaults and runs without any configuration. To customize behavior, copy the example environment file to a real one:
  </p>
<pre><code>cp .env.example .env</code></pre>
  <p>
    Then edit <code>.env</code> to set any of the optional variables documented in the Configuration table below (excluded agencies, scan window, Cloudflare project name, etc.). The <code>.env</code> file is gitignored so local settings stay out of the repository.
  </p>

  <h4>Step 3. Build the database</h4>
  <p>
    This step downloads USASpending's annual bulk archives, parses them, and loads the resulting awards into a SQLite database at <code>contractwatch.db</code> in the repository root.
  </p>
<pre><code>uv run python tools/bulk_loader.py tools/jobs.example.json</code></pre>
  <p>
    The job is driven by <code>tools/jobs.example.json</code>, which lists the FY2018 through FY2026 archive URLs to fetch. USASpending rotates the archive filenames roughly monthly; if the URLs in the file 404, open <a href="https://files.usaspending.gov/award_data_archive/">files.usaspending.gov/award_data_archive/</a> in a browser, find the current snapshot date, and edit <code>jobs.example.json</code> accordingly.
  </p>
  <p>
    Expect roughly 45 minutes total wall time: about 15 minutes downloading the ~15 GB of zip archives, then 30 minutes parsing the CSVs and bulk-inserting roughly 5 million rows into SQLite. The resulting database is around 9 GB on disk. Watch live progress in a browser by also running:
  </p>
<pre><code>python -m http.server 8000 -d web &amp;
open http://localhost:8000/loader.html</code></pre>

  <h4>Step 4. Apply the flags</h4>
  <p>
    With the database built, run the flag pipeline:
  </p>
<pre><code>uv run python tools/reflag_all.py</code></pre>
  <p>
    This evaluates F01/F02/F03 against every award in the database, applies the structural filter, and writes the surviving flags to the <code>flags</code> table. The full pass completes in about 1.5 seconds because the flag-eligible subset is pulled via three bulk SQL queries rather than per-row Python iteration. Output prints the candidate count, survivor count, and how many candidates were stripped by the structural filter.
  </p>
  <p>
    The previous <code>flags</code> table is backed up to a timestamped table (e.g., <code>flags_backup_20260528_071500</code>) before being replaced, so prior flag state can be inspected or restored if needed.
  </p>

  <h4>Step 5. Generate the dashboard JSON</h4>
  <p>
    Convert the populated <code>flags</code> table into the static JSON files the dashboard reads:
  </p>
<pre><code>uv run python export_json.py</code></pre>
  <p>
    This writes <code>web/data/latest.json</code> (the flagged awards), <code>web/data/stats.json</code> (running totals), and a dated archive at <code>web/data/history/YYYY-MM-DD.json</code>. The publish filter (drop pre-FY18 action dates) is applied at this step, so the published count is slightly lower than the in-database flag count.
  </p>

  <h4>Step 6. View the dashboard</h4>
  <p>
    Serve the <code>web/</code> folder locally:
  </p>
<pre><code>python -m http.server 8000 -d web</code></pre>
  <p>
    Then open <a href="http://localhost:8000/">http://localhost:8000/</a> in a browser. The dashboard reads the JSON files generated in step 5 and renders the flagged awards with sorting, filtering, and per-award detail expansion.
  </p>

  <h3>Daily updates</h3>
  <p>
    Once the database is built, the day-to-day workflow is the <code>daily_scan.sh</code> shell script. It hits USASpending's search API for any awards modified in the trailing N-day window (default 2 days), upserts them into the database, re-runs the flag pipeline, regenerates the dashboard JSON, and (if <code>wrangler</code> is configured) deploys to Cloudflare Pages.
  </p>
<pre><code>./daily_scan.sh</code></pre>
  <p>
    For automated daily runs on macOS, copy <code>launchd/com.contractwatch.plist.example</code> to <code>~/Library/LaunchAgents/com.contractwatch.plist</code>, edit the two hardcoded absolute paths (launchd does not expand <code>~</code>), then load the job:
  </p>
<pre><code>launchctl load ~/Library/LaunchAgents/com.contractwatch.plist</code></pre>
  <p>
    The example template fires once daily at 04:30 local time. Adjust the <code>Hour</code> and <code>Minute</code> values in the plist to change the schedule. To disable the job, run <code>launchctl unload</code> with the same path.
  </p>

  <h3>Configuration (all env vars optional)</h3>
  <table>
    <thead><tr><th>Variable</th><th>Default</th><th>Purpose</th></tr></thead>
    <tbody>
      <tr><td><code>CONTRACTWATCH_EXCLUDED_AGENCIES</code></td><td>(empty)</td><td>Pipe-delimited agency names to skip at ingestion and strip at reflag time</td></tr>
      <tr><td><code>CONTRACTWATCH_BACKFILL_DAYS</code></td><td>{BACKFILL_DAYS}</td><td>Daily catch-up window in days</td></tr>
      <tr><td><code>CONTRACTWATCH_CF_PROJECT</code></td><td><code>{CLOUDFLARE_PROJECT}</code></td><td>Cloudflare Pages project name for <code>wrangler pages deploy</code></td></tr>
    </tbody>
  </table>

  <h3>Adjusting the flag thresholds</h3>
  <p>
    Threshold knobs live in <code>engine/config.py</code>:
  </p>
  <table>
    <thead><tr><th>Constant</th><th>Default</th><th>Purpose</th></tr></thead>
    <tbody>
      <tr><td><code>MIN_OBLIGATION</code></td><td>{m(MIN_OBLIGATION)}</td><td>Ingestion floor</td></tr>
      <tr><td><code>CRITICAL_SOLE_SOURCE_MIN</code></td><td>{m(CRITICAL_SOLE_SOURCE_MIN)}</td><td>F01 dollar floor</td></tr>
      <tr><td><code>FIRST_LARGE_AWARD_MIN</code></td><td>{m(FIRST_LARGE_AWARD_MIN)}</td><td>F03 dollar floor</td></tr>
      <tr><td><code>ONE_OFFER_MIN_OBLIGATION</code></td><td>$25M</td><td>F02 dollar floor (lives in <code>engine/flags.py</code>)</td></tr>
    </tbody>
  </table>
  <p>
    Edit a value, run <code>tools/reflag_all.py</code>, then
    <code>export_json.py</code>. New thresholds take effect immediately. No DB
    rebuild needed.
  </p>

  <h3>Deployment</h3>
  <p>
    The dashboard is a static <code>web/</code> folder. ContractWatch deploys
    to Cloudflare Pages via <code>wrangler</code>, but any static host works.
    To run your own copy on Cloudflare:
  </p>
  <ol>
    <li>Create a Cloudflare account and a Pages project named whatever you like.</li>
    <li>Install <code>wrangler</code> and run <code>wrangler login</code>.</li>
    <li>Set <code>CONTRACTWATCH_CF_PROJECT</code> in your <code>.env</code> to your project name.</li>
    <li>Run <code>wrangler pages deploy web --project-name="$CONTRACTWATCH_CF_PROJECT"</code>, or just run <code>daily_scan.sh</code>.</li>
  </ol>
  <p>
    You can also skip Cloudflare entirely and serve <code>web/</code> from
    anywhere: <code>python -m http.server</code>, S3, GitHub Pages, nginx.
  </p>
</section>

<div class="footer">
  Generated {generated} by <code>tools/build_readme.py</code>. Filter data pulled live from <code>engine/structural_filter.py</code>.
</div>

</main>

<script>
const buttons = document.querySelectorAll('nav.tabs button');
const tabs = document.querySelectorAll('section.tab');
buttons.forEach(btn => {{
  btn.addEventListener('click', () => {{
    const target = btn.dataset.tab;
    buttons.forEach(b => b.classList.toggle('active', b === btn));
    tabs.forEach(t => t.classList.toggle('active', t.id === 'tab-' + target));
    window.scrollTo({{ top: 0, behavior: 'instant' }});
  }});
}});
</script>

</body>
</html>
"""
    out_path = os.path.join(ROOT, "operators_guide.html")
    with open(out_path, "w") as fh:
        fh.write(html_out)
    print(f"Wrote {out_path} ({os.path.getsize(out_path):,} bytes)")


if __name__ == "__main__":
    main()
