"""ContractWatch structural filter.

The three-flag framework screens every award against:
    F01  No prior federal contracts + sole-source above $10M
    F02  No prior federal contracts + competitive solicitation with
         one offer above $25M
    F03  No prior federal contracts + first contract above $25M

The structural filter sits alongside the flag logic in three layers:

  1. STRUCTURAL_RULES: a flag fires, but the result is stripped because
     the pattern is structural, not anomalous. Example: a subsidiary of a
     major federal prime fires F03 (first contract above $25M with no
     prior history) because primes spin up fresh LLCs for specific
     contract vehicles; that is normal corporate behavior, not a shell
     pattern.

  2. PUBLISH_FILTERS: drop entire awards from latest.json at export time.
     Currently used to drop pre-FY18 action_dates from the published
     dataset.

To inspect what's active:
    python3 -c "from engine.structural_filter import describe_all; describe_all()"

To disable a rule temporarily:
    edit `enabled=True` to `enabled=False` on the entry below.
"""
from dataclasses import dataclass, field
from typing import Callable, List

import re

from engine.config import (
    ESTABLISHED_ENTITY_MIN_OBLIG, ESTABLISHED_ENTITY_MIN_AWARDS,
    EXCLUDED_AWARDING_AGENCIES,
)


@dataclass
class StructuralRule:
    """One structural-filter rule.

    If `match(ctx)` returns True for an award, then any flag in
    `applies_to_flags` is stripped from the result. Flag codes can be
    full ("F03_FIRST_LARGE_AWARD") or short ("F03"); both work.
    """
    id: str
    description: str
    rationale: str
    applies_to_flags: List[str]
    match: Callable
    enabled: bool = True
    added_date: str = ""

    def matches_flag(self, flag_code: str) -> bool:
        short = flag_code.split("_", 1)[0]  # "F03_FIRST_LARGE_AWARD" -> "F03"
        for c in self.applies_to_flags:
            if c == flag_code or c == short or c == "*":
                return True
        return False


@dataclass
class PublishFilter:
    """Export-layer filter. Awards matching this don't make it into latest.json.

    Distinct from STRUCTURAL_RULES because the flags are unchanged; we just
    don't publish the result.
    """
    id: str
    description: str
    rationale: str
    match: Callable
    enabled: bool = True
    added_date: str = ""


# --- Helpers used by the structural rules below ---

MAJOR_PRIME_NAME_PREFIXES = (
    # Defense primes
    "LOCKHEED MARTIN", "LOCKHEED",
    "BOEING",
    "RAYTHEON", "RTX",
    "GENERAL DYNAMICS",
    "NORTHROP GRUMMAN",
    "BAE SYSTEMS",
    "L3HARRIS", "L3 HARRIS", "L3 ",
    "LEIDOS",
    "BOOZ ALLEN HAMILTON", "BOOZ ALLEN",
    "SAIC", "SCIENCE APPLICATIONS",
    "CACI",
    "AEROJET ROCKETDYNE",
    # Rule 8 (user-approved): legacy primes that still receive contracts
    # under their pre-merger corporate names
    "ROCKWELL INTERNATIONAL", "ROCKWELL COLLINS",
    "SAAB ",
    "HUNTINGTON INGALLS", "HII MISSION", "HII ",
    "ELECTRIC BOAT",  # General Dynamics subsidiary, submarine builder
    "BATH IRON WORKS",  # General Dynamics, destroyer builder
    "NATIONAL STEEL AND SHIPBUILDING", "NASSCO",  # General Dynamics shipyard
    "AUSTAL USA",  # defense shipbuilder
    "DRS NETWORK", "DRS SUSTAINMENT", "DRS TECHNOLOGIES", "LEONARDO DRS",
    "SIKORSKY",  # Lockheed Martin subsidiary, helicopter prime
    "BELL BOEING", "BELL-BOEING",  # V-22 Osprey JV between Bell (Textron) and Boeing
    "BELL TEXTRON",  # Textron subsidiary
    "SIERRA NEVADA CORPORATION", "SIERRA NEVADA COMPANY",
    "GENERAL ATOMICS",  # drones, defense
    "GOODRICH CORPORATION", "GOODRICH ",  # RTX / Collins Aerospace
    "ROLLS-ROYCE",
    "ALLIANT TECHSYSTEMS",  # now Northrop
    "AM GENERAL",  # Humvee
    "AIRBUS US SPACE", "AIRBUS DEFENSE",
    "OSHKOSH DEFENSE",
    "VIASAT",  # satellite comm
    "CAE USA",  # simulators
    "INSITU, INC", "INSITU INC",  # Boeing drone subsidiary
    "DYNETICS",  # Leidos subsidiary
    "UNISYS",  # federal IT prime
    "HONEYWELL",
    "TEXTRON",
    "KBR ", "KBR,",
    "AECOM",
    "AMENTUM",
    "PERATON",
    "MANTECH",
    "VERTEX AEROSPACE",
    "DYNCORP INTERNATIONAL",  # services prime, now Amentum
    "VECTRUS", "V2X ", "V2X,",
    "ENGILITY",  # now SAIC
    "CH2M HILL",  # now Jacobs
    "TETRA TECH",
    "ICF INCORPORATED", "ICF INTERNATIONAL",
    "MAXIMUS FEDERAL",
    # Healthcare federal primes
    "OPTUM PUBLIC SECTOR", "OPTUMSERVE", "OPTUM SERVE",
    "UNITEDHEALTH MILITARY", "UNITEDHEALTHCARE MILITARY",
    "TRIWEST HEALTHCARE",
    "HEALTH NET FEDERAL",
    # Tech / IT primes
    "GENERAL ELECTRIC", "GE AVIATION", "GE VERNOVA",
    "ACCENTURE FEDERAL",
    "IBM CORPORATION", "IBM CORP",
    "DELL FEDERAL",
    "ORACLE AMERICA", "ORACLE CORPORATION",
    "MICROSOFT CORPORATION",
    "DELOITTE CONSULTING", "DELOITTE & TOUCHE",
    "GUIDEHOUSE",
    "CSRA",
    "SALIENT CRGT",
    "IGOV TECHNOLOGIES",
    "AMERICAN SYSTEMS",
    # Launch / space
    "SPACE EXPLORATION TECHNOLOGIES", "SPACEX",
    "UNITED LAUNCH ALLIANCE", "UNITED LAUNCH SERVICES",
    "BLUE ORIGIN",
    "SES SPACE",
    # Engineering / construction primes
    "CGI FEDERAL", "CGI INC",
    "JACOBS ", "JACOBS ENGINEERING",
    "FLUOR ", "FLUOR CORPORATION", "FLUOR FEDERAL",
    "BECHTEL ", "BECHTEL NATIONAL",
    "PARSONS ", "PARSONS CORPORATION",
    "BATTELLE MEMORIAL", "BATTELLE ",
    "APTIM ",
    "WSP USA",
    "HENSEL PHELPS",
    "WHITING-TURNER", "WHITING TURNER",
    "BL HARBERT", "B L HARBERT", "B.L. HARBERT",
    # Research / consulting primes
    "WESTAT",
    "MATHEMATICA",
    "ABT GLOBAL", "ABT ASSOCIATES",
    "DAI GLOBAL", "DAI ",
    "CHEMONICS INTERNATIONAL", "CHEMONICS ",
    # IT primes / GWAC resellers
    "WORLD WIDE TECHNOLOGY",
    "CDW GOVERNMENT", "CDW-G", "CDW G",
    "LUMEN TECHNOLOGIES",
    # Federal IT/services primes (mid-tier, SAM-timing-anomaly false positives)
    "SIGMA DEFENSE SYSTEMS",
    "PRESIDIO GOVERNMENT SOLUTIONS", "PRESIDIO FEDERAL", "PRESIDIO NETWORKED",
    "AUROTECH",
    "NETWORK DESIGNS", "NDI ",
    "VENTECH SOLUTIONS", "VENTECH ",
    "VALIANT GLOBAL DEFENSE", "VALIANT INTEGRATED",
    "TELEPHONICS",
    "ESSEX INDUSTRIES",
    "SOUND & SEA TECHNOLOGY",
    "THE KAIZEN COMPANY", "KAIZEN COMPANY",  # USAID dev prime
    "NATIONAL QUALITY FORUM",  # major healthcare nonprofit, longtime CMS contractor
    # Services / outsourcing primes
    "SERCO ", "SERCO,", "SERCO-",
    "IAP WORLDWIDE",
    "SOS INTERNATIONAL",
    "PAE GOVERNMENT", "PAE AVIATION", "PAE APPLIED",
    "TRIPLE CANOPY", "CENTERRA GROUP", "CONSTELLIS",
    "AKAL SECURITY",
    # Alaska Native Corporation subsidiaries (8(a) sole-source pattern with many sister LLCs)
    "ASRC FEDERAL", "ASRC ",
    "ALUTIIQ ",
    "CHUGACH ",
    "CHEROKEE NATION", "CHEROKEE FEDERAL",
    "ARCTICOM",
    "BRISTOL BAY",
    # FFRDCs and university-affiliated federal R&D centers (structural:
    # they ARE the lab operator; large no-prior-history awards under newly
    # incorporated LLCs are normal contract-vehicle structure, not anomaly)
    "THE JOHNS HOPKINS UNIVERSITY APPLIED PHYSICS LABORATORY",
    "JOHNS HOPKINS UNIVERSITY APPLIED PHYSICS",
    "THE JOHNS HOPKINS UNIVERSITY", "JOHNS HOPKINS UNIVERSITY",
    "CALIFORNIA INSTITUTE OF TECHNOLOGY",  # operates JPL
    "THE MITRE CORPORATION", "MITRE CORPORATION",
    "AEROSPACE CORPORATION", "THE AEROSPACE CORPORATION",
    "INSTITUTE FOR DEFENSE ANALYSES", "IDA ",
    "NOBLIS",
    "SRI INTERNATIONAL",
    "RAND CORPORATION", "THE RAND CORPORATION",
    "MASSACHUSETTS INSTITUTE OF TECHNOLOGY",  # MIT Lincoln Lab
    "CARNEGIE MELLON UNIVERSITY",  # SEI FFRDC
    "CHARLES STARK DRAPER", "THE CHARLES STARK DRAPER",
    "GEORGIA TECH APPLIED RESEARCH", "GEORGIA TECH RESEARCH",
    "SOUTHWEST RESEARCH INSTITUTE",
    "LELAND STANFORD JUNIOR UNIVERSITY", "STANFORD UNIVERSITY",
    "RESEARCH TRIANGLE INSTITUTE",
    # DOE National Lab operators (M&O contracts, NOT picked up by FFRDC PSC alone)
    "UCHICAGO ARGONNE",
    "NATIONAL TECHNOLOGY & ENGINEERING SOLUTIONS OF SANDIA",
    "CONSOLIDATED NUCLEAR SECURITY",
    "TRIAD NATIONAL SECURITY",
    "WASHINGTON SAVANNAH RIVER",
    "WEST VALLEY NUCLEAR",
    "MISSION SUPPORT & TEST SERVICES",
    "WASHINGTON RIVER PROTECTION SOLUTIONS",
    # Federal IT/distribution resellers (structural: zero subs because they
    # ship COTS hardware/software, terse descriptions because procurement is
    # SKU-based, big mod creep because GWACs grow with usage)
    "CARAHSOFT TECHNOLOGY", "CARAHSOFT ",
    "THUNDERCAT TECHNOLOGY",
    "FCN, INC", "FCN INC",
    "FOUR POINTS TECHNOLOGY",
    "MINBURN TECHNOLOGY",
    "IMMIXGROUP",
    "GTSI CORP",
    # Federal prison / detention specialty primes (structural patterns: long-term
    # facility operating contracts with usage-based growth, terse descriptions)
    "FEDERAL PRISON INDUSTRIES", "UNICOR",
    "MANAGEMENT & TRAINING CORPORATION", "MANAGEMENT AND TRAINING CORPORATION",
    "THE GEO GROUP", "GEO GROUP",
    "DISMAS CHARITIES",
    "NAPHCARE",
    # Construction primes whose names contain "Foundation" (literal concrete
    # foundations, not philanthropic). Easy to confuse with nonprofits.
    "BAUER FOUNDATION CORP", "BAUER FOUNDATIONS",
    "SHORELINE FOUNDATION",
    # Healthcare network primes (Providence-affiliated, military health plans)
    "PACMED CLINICS", "PACIFIC MEDICAL CENTERS",
    # Children's hospitals (specific large nonprofit medical centers structurally
    # firing F01/F02 on routine grant pass-through and IDIQ growth)
    "ST JUDE CHILDREN'S RESEARCH HOSPITAL", "ST. JUDE CHILDREN",
    "SEATTLE CHILDREN'S HOSPITAL", "SEATTLE CHILDRENS HOSPITAL",
    "CHILDREN'S HOSPITAL CORPORATION",  # Boston Children's
    "RESEARCH INSTITUTE AT NATIONWIDE CHILDREN", "NATIONWIDE CHILDREN'S HOSPITAL",
    "THE CHILDREN'S HOSPITAL OF PHILADELPHIA", "CHILDREN'S HOSPITAL OF PHILADELPHIA",
    "CHILDRENS MERCY HOSPITAL", "CHILDREN'S MERCY HOSPITAL",
    "RADY CHILDREN'S HOSPITAL", "RADY CHILDRENS HOSPITAL",
    "SSM CARDINAL GLENNON CHILDREN",
    "CHILDREN'S HOSPITAL LOS ANGELES",
    "CHILDREN'S HOSPITAL MEDICAL CENTER", "CHILDRENS HOSPITAL MEDICAL CENTER",
    # Major academic medical centers (structural federal research / TRICARE)
    "VANDERBILT UNIVERSITY MEDICAL CENTER",
    "MAYO CLINIC",
    "THE UNIVERSITY OF TEXAS M.D. ANDERSON", "M.D. ANDERSON", "MD ANDERSON",
    "FRED HUTCHINSON CANCER",
    "THE UNIVERSITY OF TEXAS SOUTHWESTERN MEDICAL CENTER", "UT SOUTHWESTERN",
    "BRIGHAM & WOMENS HOSPITAL", "BRIGHAM AND WOMEN",
    "THE GENERAL HOSPITAL CORPORATION",  # Massachusetts General
    "NORTH SHORE UNIVERSITY HOSPITAL",
    "MARSHFIELD CLINIC",
    "UNIVERSITY OF MISSISSIPPI MEDICAL CENTER",
    "NEW YORK CITY HEALTH AND HOSPITALS",
    "DIALYSIS CLINIC, INC", "DIALYSIS CLINIC INC",
    # Kaiser (commercial-brand-grade integrated healthcare)
    "KAISER FOUNDATION HOSPITALS", "KAISER FOUNDATION HEALTH PLAN",
    "KAISER PERMANENTE",
    # Federal-research-focused nonprofit foundations (each is a major
    # congressionally-chartered or program-anchored organization)
    "THE HENRY M. JACKSON FOUNDATION",  # HJF, military medical research
    "HENRY M. JACKSON FOUNDATION",
    "THE GENEVA FOUNDATION",  # military medical research nonprofit
    "ELIZABETH GLASER PEDIATRIC AIDS FOUNDATION",
    "THE ASIA FOUNDATION",  # USAID-affiliated nonprofit since 1954
    "ASIA FOUNDATION",
    "NATIONAL MARINE MAMMAL FOUNDATION",  # Navy MMRP partner
    "FOUNDATION MEDICINE",  # Roche cancer dx subsidiary
    "GIVE BACK FOUNDATION",  # OPM Combined Federal Campaign manager
    # University research foundations (pass-throughs for federal grants —
    # structurally fire the no-prior-history flags on routine grant subawards)
    "THE RESEARCH FOUNDATION FOR THE STATE UNIVERSITY OF NEW YORK",
    "RESEARCH FOUNDATION FOR THE STATE UNIVERSITY OF NEW YORK",
    "UNIVERSITY OF GEORGIA RESEARCH FOUNDATION",
    "UNIVERSITY OF KENTUCKY RESEARCH FOUNDATION",
    "GEORGIA STATE UNIVERSITY RESEARCH FOUNDATION",
    "SAN DIEGO STATE UNIVERSITY FOUNDATION",
    "OLD DOMINION UNIVERSITY RESEARCH FOUNDATION",
    "UNIVERSITY RESEARCH FOUNDATION",  # generic
    # Military / VA medical research foundations
    "MILITARY HEALTH RESEARCH FOUNDATION",
    "PORTLAND VA RESEARCH FOUNDATION",
    "RADIATION EFFECTS RESEARCH FOUNDATION",
    # Generic bare children's hospital prefix (catches "CHILDREN'S HOSPITAL"
    # standalone, plus all the specific-city variants we listed)
    "CHILDREN'S HOSPITAL", "CHILDRENS HOSPITAL",
    # CMS Quality Improvement Organizations (federally-designated state QIOs)
    "MOUNTAIN PACIFIC QUALITY HEALTH FOUNDATION",
    "OKLAHOMA FOUNDATION FOR MEDICAL QUALITY",
    "ARKANSAS FOUNDATION FOR MEDICAL CARE",
    "ALABAMA QUALITY ASSURANCE FOUNDATION",
    "KANSAS FOUNDATION FOR MEDICAL CARE",
    # AbilityOne nonprofits (statutory federal disability employment program;
    # sole-source contracts are mandated by 41 USC 8503)
    "HUNTSVILLE REHABILITATION FOUNDATION",  # DBA Phoenix
    "SOUTH TEXAS LIGHTHOUSE FOR THE BLIND",
    "ADELANTE DEVELOPMENT CENTER",
    "WORK SERVICES CORPORATION",
    "ALL NATIVE MANAGED SERVICES",
    "PARC COMMUNITY PARTNERSHIP",
    # USAID development primes (structural sole-source for international dev work)
    "TEXAS EDUCATIONAL FOUNDATION",  # DOL Job Corps prime
    "PUBLIC HEALTH FOUNDATION ENTERPRISES",
    # Added 2026-05-26: defense primes / federal IT subsidiaries observed
    # surfacing as no-prior-history false positives in production data.
    "DRS DEFENSE SOLUTIONS", "DRS ENVIRONMENTAL", "DRS LAUREL",
    "MAXAR MISSION", "MAXAR TECHNOLOGIES", "MAXAR ",
    "FOSTER MILLER",                      # QinetiQ subsidiary
    "INVERTIX",                           # legacy, now SAIC
    "1901 GROUP",                         # now Leidos
    "ALION SCIENCE",
    "HYPORI",
    "INTUITIVE RESEARCH AND TECHNOLOGY",
    "ENROUTE COMPUTER",
)


def _strip_leading_the(name: str) -> str:
    n = name.upper().strip()
    if n.startswith("THE "):
        n = n[4:].strip()
    if n.endswith(", THE"):
        n = n[:-5].strip()
    return n


def is_major_prime_subsidiary(ctx) -> bool:
    name = ctx.get("award", {}).get("recipient_name") or ""
    if not name:
        return False
    stripped = _strip_leading_the(name)
    # Strip "THE " from prefixes too so "THE GENERAL HOSPITAL CORPORATION" in
    # the prefix list matches the leading-THE-stripped recipient name.
    return any(stripped.startswith(_strip_leading_the(p))
               for p in MAJOR_PRIME_NAME_PREFIXES)


# FFRDC R&D services PSC prefixes
_FFRDC_PSC_PREFIXES = ("AN", "AR", "AZ", "AJ")


def is_ffrdc_by_psc(ctx) -> bool:
    psc = (ctx.get("award", {}).get("psc_code") or "").upper()
    return psc[:2] in _FFRDC_PSC_PREFIXES


# Known DOE / NNSA M&O contractors that are FFRDC operators
_DOE_MO_NAME_PATTERNS = [
    "battelle", "ut-battelle", "sandia", "los alamos",
    "lawrence livermore", "lawrence berkeley", "fermi",
    "brookhaven", "oak ridge", "princeton plasma",
    "stanford linear", "slac", "jefferson science",
    "thomas jefferson", "frederick national", "leidos biomedical",
]


def is_doe_mo_by_name(ctx) -> bool:
    award = ctx.get("award", {})
    agency = (award.get("awarding_agency") or "").lower()
    if not ("energy" in agency or "nnsa" in agency or "national nuclear" in agency):
        return False
    recipient = (award.get("recipient_name") or "").lower()
    return any(p in recipient for p in _DOE_MO_NAME_PATTERNS)


# M&O / GOCO patterns in award descriptions
_MO_DESC_PATTERNS = [
    "m&o contract", "management and operating",
    "goco", "operation of goco", "operation of the",
]


def is_mo_by_description(ctx) -> bool:
    description = (ctx.get("award", {}).get("description") or "").lower()
    return any(p in description for p in _MO_DESC_PATTERNS)


# --- Structural-pre-existing categories ---
# F01/F02/F03 all rely on a "no prior federal contracts for this UEI"
# check. The check is against the local awards table (FY18+, >=$1M
# obligation), which means entities that genuinely have decades of
# federal business under a different UEI / subsidiary / legal-entity name
# look "new" and trip the flags. The categories below identify those
# false-positive populations.

# Major commercial brands. Each almost certainly has decades of federal
# business under a different UEI / subsidiary / legal entity name.
MAJOR_COMMERCIAL_BRAND_PREFIXES = (
    # Financial
    "BANK OF AMERICA", "WELLS FARGO", "JPMORGAN", "JP MORGAN", "CITIGROUP", "CITIBANK",
    "GOLDMAN SACHS", "MORGAN STANLEY", "U.S. BANK", "US BANK ", "PNC ",
    "TRUIST", "CAPITAL ONE", "AMERICAN EXPRESS", "VISA INC", "MASTERCARD",
    "BANK OF NEW YORK MELLON", "BNY MELLON",
    "TORONTO-DOMINION", "TORONTO DOMINION",
    "STONEX FINANCIAL", "STONEX ",
    # Big 4 accounting / consulting
    "PRICEWATERHOUSECOOPERS", "PWC ", "ERNST & YOUNG", "ERNST AND YOUNG", "EY ",
    "KPMG", "DELOITTE", # DELOITTE CONSULTING already in primes list
    # Pharma
    "ELI LILLY", "PFIZER", "MERCK ", "MERCK,", "MODERNA", "JOHNSON & JOHNSON",
    "ABBVIE", "ABBOTT", "GLAXOSMITHKLINE", "GSK ", "ASTRAZENECA", "NOVARTIS",
    "BRISTOL-MYERS SQUIBB", "BRISTOL MYERS SQUIBB", "AMGEN", "GILEAD",
    "REGENERON", "BIOGEN", "TAKEDA", "SANOFI", "BAYER", "ROCHE ",
    "MCKESSON",  # major pharma distributor
    # Auto / industrial
    "GENERAL MOTORS", "FORD MOTOR", "STELLANTIS", "TESLA, INC", "RIVIAN",
    "CATERPILLAR", "JOHN DEERE", "DEERE & COMPANY", "3M COMPANY", "3M CO",
    "PARKER-HANNIFIN", "PARKER HANNIFIN",
    "W.W. GRAINGER", "W. W. GRAINGER", "WW GRAINGER",
    "DEL MONTE FOODS",
    # Medical devices
    "SIEMENS MEDICAL", "OLYMPUS AMERICA", "ORTHO-CLINICAL", "ORTHO CLINICAL",
    # Tech (Microsoft / Oracle / Dell already in MAJOR_PRIME_NAME_PREFIXES)
    "AMAZON", "GOOGLE LLC", "ALPHABET INC", "META PLATFORMS", "META INC",
    "APPLE INC", "SALESFORCE", "SERVICENOW", "ADOBE INC", "VMWARE",
    "DELL MARKETING",
    "PALANTIR", "CISCO SYSTEMS", "CISCO ", "INTEL CORPORATION", "INTEL CORP",
    "NVIDIA", "AMD ", "ADVANCED MICRO DEVICES",
    "THALES",  # French defense electronics multinational (all Thales subs)

    # Retail / logistics
    "WALMART", "TARGET CORPORATION", "HOME DEPOT",
    "FEDEX", "FEDERAL EXPRESS",
    "UPS ", "UNITED PARCEL SERVICE", "BERKSHIRE HATHAWAY",
    # Telecom
    "AT&T", "VERIZON", "T-MOBILE", "T MOBILE", "COMCAST", "CHARTER COMMUNICATIONS",
    # Petroleum majors
    "BP PRODUCTS", "BP NORTH AMERICA",
    "PHILLIPS 66", "PHILLIPS66",
    "MARATHON PETROLEUM",
    "VALERO ", "VALERO MARKETING",
    "TESORO ", "TESORO REFINING",
    "ALON USA",
    # Major utility holding companies (treat as brands since they own local monopolies)
    "AMEREN", "DOMINION ENERGY", "DOMINION RESOURCES",
    "NATIONAL GRID USA", "NATIONAL GRID",
    "CENTERPOINT ENERGY", "EXELON", "DUKE ENERGY", "SOUTHERN COMPANY",
    "NEXTERA ENERGY", "NEXTERA, INC", "AMERICAN ELECTRIC POWER", "AEP ",
    "CONSOLIDATED EDISON", "CON EDISON", "EVERSOURCE", "ENTERGY",
    "XCEL ENERGY", "PG&E", "PACIFIC GAS AND ELECTRIC", "SOUTHERN CALIFORNIA EDISON",
    "AMERICAN WATER",
    "POTOMAC ELECTRIC POWER", "PEPCO ",
)


def is_major_commercial_brand(ctx) -> bool:
    name = ctx.get("award", {}).get("recipient_name") or ""
    if not name:
        return False
    stripped = _strip_leading_the(name)
    return any(stripped.startswith(_strip_leading_the(p))
               for p in MAJOR_COMMERCIAL_BRAND_PREFIXES)


# US utilities. You can't compete for power/gas/water in someone else's
# service territory. A no-prior-history flag firing on a utility is just
# "they finally crossed the dollar threshold for one fiscal year." Not signal.
US_UTILITY_NAME_PATTERNS = (
    " GAS & ELECTRIC", " GAS AND ELECTRIC",
    " POWER COMPANY", " POWER & LIGHT", " POWER AND LIGHT",
    " POWER AGENCY",  # e.g. SOUTHWEST PUBLIC POWER AGENCY
    " ELECTRIC COMPANY", " ELECTRIC COOPERATIVE", " ELECTRIC CO-OP",
    " ELECTRIC CORP", " ELECTRIC CORPORATION",
    " NATURAL GAS", " GAS COMPANY", " GAS CORPORATION", " GAS CORP",
    " GAS SYSTEM", " PIPELINE COMPANY", " ENERGY COOPERATIVE",
    " POWER AUTHORITY", " WATER AUTHORITY", " WATER DISTRICT",
    " WATER UTILITY", " WATER UTILITIES", " WATER OPERATIONS",
    " WATER SYSTEM",
    " MUNICIPAL UTILITIES", " PUBLIC UTILITIES",
    " UTILITIES COMMISSION", " UTILITY BOARD",
    " UTILITIES",  # e.g. COLORADO SPRINGS UTILITIES
    " UTILITY",   # e.g. CITY UTILITY
    "UTILITY BOARD OF ",  # municipal utilities
    "FOREIGN UTILITY",  # FUCR-type foreign utility entities
)


# Description-text patterns that indicate a utility service / privatization
# contract. The recipient name may be generic (e.g., "California Water Service
# Co.") but the contract description explicitly describes utility distribution
# infrastructure or privatization work.
UTILITY_DESCRIPTION_PATTERNS = (
    "UTILITY SERVICE CONTRACT", "UTILITY SERVICES CONTRACT",
    "UTILITY PRIVATIZATION",
    "PRIVATIZATION OF WATER", "PRIVATIZATION OF ELECTRIC",
    "PRIVATIZATION OF GAS", "PRIVATIZATION OF NATURAL GAS",
    "PRIVATIZATION OF THE WATER", "PRIVATIZATION OF THE ELECTRIC",
    "PRIVATIZATION OF THE GAS", "PRIVATIZATION OF THE NATURAL GAS",
    "PRIVATIZED WATER", "PRIVATIZED ELECTRIC", "PRIVATIZED UTILITY",
    "WATER DISTRIBUTION SYSTEM", "ELECTRIC DISTRIBUTION SYSTEM",
    "GAS DISTRIBUTION SYSTEM", "WASTEWATER TREATMENT",
    "SANITARY SEWER SYSTEM", "STORM WATER SYSTEM",
    "ELECTRIC SYSTEM CONVEYANCE", "GAS SYSTEM CONVEYANCE",
)

# NAICS codes for utilities sector. Any recipient with `PRIVATIZATION` in
# the corporate name AND a utility NAICS is structurally a utility-services
# subsidiary holding a base utility-privatization contract.
_UTILITY_NAICS_PREFIXES = ("221",)


def is_us_utility(ctx) -> bool:
    award = ctx.get("award", {})
    name = (award.get("recipient_name") or "").upper()
    desc = (award.get("description") or "").upper()
    naics = (award.get("naics_code") or "").strip()
    if name and any(p in name for p in US_UTILITY_NAME_PATTERNS):
        return True
    if desc and any(p in desc for p in UTILITY_DESCRIPTION_PATTERNS):
        return True
    if "PRIVATIZATION" in name and any(naics.startswith(p) for p in _UTILITY_NAICS_PREFIXES):
        return True
    return False


# State and local government recipients, plus public universities.
# These get federal grants and pass-through funding routinely. "No prior
# federal history" is meaningless for a state agency.
GOVERNMENT_RECIPIENT_PATTERNS = (
    "STATE OF ", "COMMONWEALTH OF ", "COUNTY OF ", "CITY OF ", "TOWN OF ",
    "VILLAGE OF ",
)
GOVERNMENT_RECIPIENT_CONTAINS = (
    "DEPARTMENT OF HEALTH", "DEPARTMENT OF HUMAN SERVICES",
    "DEPARTMENT OF TRANSPORTATION", "DEPARTMENT OF EDUCATION",
    "DEPARTMENT OF NATURAL RESOURCES", "DEPARTMENT OF PUBLIC SAFETY",
    "DEPARTMENT OF REVENUE", "DEPARTMENT OF AGRICULTURE",
    "DEPARTMENT OF ENVIRONMENTAL",
    "STATE UNIVERSITY", "UNIVERSITY OF ", "ASSOCIATED UNIVERSITIES",
    "BOARD OF REGENTS", "BOARD OF EDUCATION",
    "PUBLIC SCHOOLS", "SCHOOL DISTRICT",
    "HOUSING AUTHORITY", "TRANSIT AUTHORITY", "PORT AUTHORITY",
    # Public utility authorities and municipal entities. Quasi-governmental
    # bodies that lack federal contracting history because they procure
    # locally, not because they are anomalous new entrants.
    "RIVER AUTHORITY", "WATER AUTHORITY", "POWER AUTHORITY",
    "AIRPORT AUTHORITY", "PARKING AUTHORITY", "REDEVELOPMENT AUTHORITY",
    "WATER DISTRICT", "UTILITY DISTRICT", "PARK DISTRICT",
    "FIRE DISTRICT", "SANITATION DISTRICT", "IRRIGATION DISTRICT",
    "MUNICIPAL ", "PUBLIC WORKS", "REGIONAL COUNCIL",
    "GOODWILL INDUSTRIES",  # Goodwill chapter network (trademark-protected nonprofit)

    "METROPOLITAN COUNCIL",
    " COMMISSIONERS",
    # USASpending stores some municipal recipients in inverted last-first
    # form ("CLARKSVILLE, CITY OF" rather than "CITY OF CLARKSVILLE"). The
    # startswith patterns above catch the standard form; these catch the
    # inverted form.
    ", CITY OF", ", COUNTY OF", ", TOWN OF", ", VILLAGE OF",
    ", STATE OF", ", COMMONWEALTH OF",
    # Federal grant pass-through consortiums.
    "PARTNERSHIP FOR ",
)


def is_government_recipient(ctx) -> bool:
    name = (ctx.get("award", {}).get("recipient_name") or "").upper()
    if not name:
        return False
    if any(name.startswith(p) for p in GOVERNMENT_RECIPIENT_PATTERNS):
        return True
    return any(p in name for p in GOVERNMENT_RECIPIENT_CONTAINS)


# Foreign government and foreign-owned entities (FMS / international).
FOREIGN_ENTITY_PATTERNS = (
    "MINISTRY OF ", "MINISTRY OF DEFENSE", "MINISTRY OF DEFENCE",
    "STATE COMPANY", "STATE CORPORATION",
    "NATIONAL OIL COMPANY", "NATIONAL OIL CORPORATION",
    "PJSC", "OJSC ",
    "GMBH", " AG ", " AG,", " SA ", " SA,", " S.A.", " S.P.A.",
    "ROYAL JORDANIAN",
    " S.R.L.", " SP. Z O.O.", " PTY LTD", " PTY. LTD.",
    " A/S", " OY", " OYJ",  # Nordic corporate forms
    "UKRSPEC", "REPUBLIC OF ", "KINGDOM OF ",
    "GOVERNMENT OF ",
    "SPACE AGENCY", "ROSCOSMOS",  # foreign space agencies acting as recipient
    "CANADIAN COMMERCIAL CORPORATION",  # Canadian government commerce agent
    "BUNDESAMT",  # German federal agencies (e.g. BBR construction)
    "DAELIM INDUSTRIAL", "ILSUNG CONSTRUCTION", "SEOHEE CONSTRUCTION",
    "HD HYUNDAI", "S-OIL", "OKINAWA IDEMITSU",
)


# Description-text patterns that indicate a foreign-government or FMS
# (Foreign Military Sales) pass-through contract. The recipient may be a US
# company but the description explicitly identifies a foreign end-customer.
FOREIGN_DESCRIPTION_PATTERNS = (
    "FOREIGN MILITARY SALES",
    "MINISTRY OF DEFENSE", "MINISTRY OF DEFENCE",
    "ROYAL SAUDI", "ROYAL THAI", "ROYAL NORWEGIAN", "ROYAL CANADIAN",
    "ROYAL AUSTRALIAN", "ROYAL NETHERLANDS", "ROYAL JORDANIAN",
    "ROYAL DANISH", "ROYAL MOROCCAN", "ROYAL MALAYSIAN",
    "REPUBLIC OF KOREA", "REPUBLIC OF SINGAPORE", "REPUBLIC OF CHINA",
    "REPUBLIC OF POLAND",
    "JAPAN SELF-DEFENSE", "JAPAN AIR SELF-DEFENSE", "JAPANESE GOVERNMENT",
    "GOVERNMENT OF SAUDI", "GOVERNMENT OF JORDAN", "GOVERNMENT OF EGYPT",
    "GOVERNMENT OF UKRAINE", "GOVERNMENT OF MOROCCO", "GOVERNMENT OF KUWAIT",
    "GOVERNMENT OF QATAR", "GOVERNMENT OF UAE",
    "ARMED FORCES OF ",
    "(FMS)", " FMS ", " FMS,", " FMS.", " FMS;",
)


# --- Bridge contract detection ---
# A "bridge contract" is a short-term sole-source extension awarded to the
# incumbent contractor to fill the gap between an expiring contract and a
# delayed follow-on procurement. The recipient already has federal work under
# the prior contract; the no-prior-history flags fire structurally because the
# bridge is often issued under a fresh task-order UEI or new contract vehicle.
# Patterns below are description substrings that distinguish bridge contracts
# from literal bridge construction (BRIDGE REPAIR, BRIDGE CRANE, BRIDGE
# BEARING, etc.).

BRIDGE_CONTRACT_DESCRIPTION_PATTERNS = (
    "BRIDGE CONTRACT",
    "BRIDGE PERIOD",
    "BRIDGE EXTENSION",
    "BRIDGE STAFFING",
    "MONTH BRIDGE",
    "BRIDGE OPTION",  # SBIR Phase II Bridge Option funding
)


def is_bridge_contract_extension(ctx) -> bool:
    desc = (ctx.get("award", {}).get("description") or "").upper()
    return any(p in desc for p in BRIDGE_CONTRACT_DESCRIPTION_PATTERNS)


def is_foreign_entity(ctx) -> bool:
    award = ctx.get("award", {})
    name = (award.get("recipient_name") or "").upper()
    desc = (award.get("description") or "").upper()
    if name and any(p in name for p in FOREIGN_ENTITY_PATTERNS):
        return True
    if desc and any(p in desc for p in FOREIGN_DESCRIPTION_PATTERNS):
        return True
    return False


HEALTHCARE_PROVIDER_PATTERNS = (
    "MEDICAL CENTER", "HEALTH SYSTEM", "HEALTH NETWORK",
    "REGIONAL MEDICAL", "HOSPICE",
    "MEDICAL SERVICES CORPORATION", "HEALTH CARE INC", "HEALTHCARE CORPORATION",
)


_HEALTHCARE_NAICS = ("524114", "524113")  # health insurance carriers

# HOSPITAL matched separately as a regex so we catch "MEMORIAL HOSPITAL",
# "HOSPITALS", "HOSPITAL CORPORATION" etc. but NOT "HOSPITALITY" (hotels).
_HOSPITAL_RE = re.compile(r"HOSPITAL(?!ITY)")


def is_healthcare_provider(ctx) -> bool:
    """Match hospitals, large healthcare providers, and insurance carriers
    that operate as federal healthcare benefit administrators (TRICARE
    pharmacy, FEHB plans, etc.). These appear as 'no federal history' under
    rebranded subsidiary entities, but the parent operation is structural."""
    name = (ctx.get("award", {}).get("recipient_name") or "").upper()
    naics = (ctx.get("award", {}).get("naics_code") or "").strip()
    if naics and naics in _HEALTHCARE_NAICS:
        return True
    if not name:
        return False
    if _HOSPITAL_RE.search(name):
        return True
    return any(p in name for p in HEALTHCARE_PROVIDER_PATTERNS)


def is_structural_pre_existing(ctx) -> bool:
    """Combined matcher for structural-pre-existing categories
    (major commercial brands, US utilities, government recipients,
    foreign entities, healthcare providers)."""
    return (is_major_commercial_brand(ctx)
            or is_us_utility(ctx)
            or is_government_recipient(ctx)
            or is_foreign_entity(ctx)
            or is_healthcare_provider(ctx))


# --- Joint Venture detection ---
# A JV by definition is a newly-formed entity that exists for a specific
# procurement. SBA 8(a) Mentor-Protégé JVs, SDVOSB JVs, HUBZone JVs and
# large prime construction JVs all share the same structural properties:
# brand-new, no prior history (matches F01/F02/F03), and share officers
# and addresses with parent firms.

_JV_RE = re.compile(r'\b(JV|JOINT VENTURE)\b|\bTEAM,?\s+LLC\b', re.IGNORECASE)


def is_joint_venture(ctx) -> bool:
    name = ctx.get("award", {}).get("recipient_name") or ""
    if not name:
        return False
    return bool(_JV_RE.search(name))


# --- Alaska Native Corporation / tribal 8(a) subsidiary detection ---
# ANCs and federally-recognized tribes operate via clusters of LLC
# subsidiaries that share parent leadership, addresses, and are sole-source
# eligible under SBA 8(a) set-asides. Name-prefix detection (no SAM
# sbaBusinessTypeList enrichment required).

ANC_TRIBAL_NAME_PREFIXES = (
    # === 13 ANC Regional Corporations + their federal subsidiary families ===
    # Ahtna, Inc.
    "AHTNA",
    # Aleut Corporation
    "ALEUT ", "ALEUT-",
    # ASRC / Arctic Slope Regional Corporation
    "ASRC ", "ASRC-", "ARCTIC SLOPE REGIONAL", "PETRO STAR",
    # Bering Straits Native Corporation
    "BERING STRAITS", "BSNC ",
    # Bristol Bay Native Corporation
    "BRISTOL BAY", "BBNC ", "PEAK OILFIELD",
    # Calista Corporation
    "CALISTA", "TUNISTA", "YULISTA", "BRICE ",
    # Chugach Alaska Corporation
    "CHENEGA",
    "C2 ALASKA",
    "CHUGACH ALASKA", "CHUGACH GOVERNMENT",
    # CIRI / Cook Inlet Region
    "CIRI ", "COOK INLET REGION", "COOK INLET TRIBAL",
    # Doyon, Limited
    "DOYON", "ARCTEC ALASKA",
    # Koniag, Inc.
    "KONIAG",
    # NANA Regional Corporation
    "NANA ", "NANA-", "AKIMA",
    # Sealaska Corporation
    "SEALASKA",
    # 13th Regional Corporation (less active)
    "13TH REGIONAL",
    # ANC village corporations and their subs
    "TYONEK", "AFOGNAK", "OLGOONIK", "GOLDBELT", "QIVLIQ",
    "BOWHEAD ",  # UIC (Ukpeagvik Inupiat Corporation) federal subsidiary brand

    "TANANA CHIEFS",
    # === Tribal 8(a) firms ===
    # Cherokee Nation Businesses (Cherokee Federal family)
    "CCI ", "CHEROKEE FEDERAL", "CHEROKEE NATION",
    # Chickasaw Nation Industries (major 8(a))
    "CHICKASAW NATION", "CHICKASAW ALLIANCE", "CNI ",
    # Choctaw Nation enterprises
    "CHOCTAW PREMIER", "CHOCTAW DEFENSE", "CHOCTAW MANUFACTURING",
    "CHOCTAW GLOBAL",
    # Eastern Shawnee Tribal Authority
    "EASTERN SHAWNEE",
    # Ho-Chunk, Inc.
    "HO-CHUNK", "HO CHUNK",
    # Mohegan Tribal Enterprises
    "MOHEGAN TRIBAL",
    # Navajo Nation enterprises
    "NAVAJO NATION", "NAVAJO ENGINEERING", "DINE ",
    # Oneida Total Integrated Enterprises
    "ONEIDA TOTAL", "OTIE ",
    # Osage Nation
    "OSAGE NATION", "OSAGE LLC",
    # Suquamish Tribal Enterprises
    "SUQUAMISH ",
    # Three Affiliated Tribes / MHA Nation
    "MHA NATION", "THREE AFFILIATED",
    # Tulalip Tribal Enterprises
    "TULALIP ",
    # Yakama Nation
    "YAKAMA ",
    # Spokane Tribal Enterprises
    "SPOKANE TRIBAL",
    # Standing Rock Sioux
    "STANDING ROCK",
    # Saint Regis Mohawk / Akwesasne
    "SAINT REGIS MOHAWK", "AKWESASNE",
    # Seneca Nation enterprises
    "SENECA NATION", "SENECA HOLDINGS",
    # Comanche Nation
    "COMANCHE NATION",
    # Pueblo enterprises
    "PUEBLO OF ",
    # Other recognized tribal 8(a) prefixes
    "RED LAKE NATION", "SOUTHERN UTE",
    # === Native Hawaiian Organization (NHO) firms ===
    "NATIVE HAWAIIAN ORGANIZATION", "AKIMEKA", "ALAKA'I",
)


def is_anc_or_tribal_subsidiary(ctx) -> bool:
    name = ctx.get("award", {}).get("recipient_name") or ""
    if not name:
        return False
    stripped = _strip_leading_the(name)
    return any(stripped.startswith(p) for p in ANC_TRIBAL_NAME_PREFIXES)


# --- Curated SAFE recipient names ---
# Exact-match block of recipient names manually reviewed and confirmed to
# have a structural reason for the F03 flag firing (subsidiaries of recognized
# primes, joint ventures with named partners, household-name operating
# companies, foreign government or FMS recipients, ANC/tribal subsidiaries,
# DOE national-lab adjacent operators, USDA food-box vendors, Ed Department
# private collection agency panel, Job Corps operators, AbilityOne nonprofits,
# HUD M&M asset managers, etc.). Each name reviewed individually against
# public sources. Compiled 2026-05-27.
#
# Excluded from this list (deliberately): recipients where lack of prior
# federal history is itself the signal (new/dormant entities suddenly
# receiving large awards). Those stay on the dashboard.

CURATED_SAFE_RECIPIENT_NAMES = frozenset({
    # Top-15 dashboard review (2026-05-28): 13 added after SAM.gov verification
    # confirmed legitimate non-shell status.
    "AMERICA'S HEALTH INSURANCE PLANS, INC.",
    "AMERICA'S BLOOD CENTERS",
    "HELLFIRE SYSTEMS, LLC",
    "TRAX INTERNATIONAL CORPORATION",
    "MP SOLUTIONS, LLC",
    "E3HEALTH SOLUTIONS, LLC",
    "HUMAN GENOME SCIENCES, INC.",
    "VENDOR RESOURCE MANAGEMENT, INC.",
    "CHECCHI AND COMPANY CONSULTING, INC.",
    "NIH BAYVIEW ACQUISITION LLC",
    "GLOBAL HEALTH INVESTMENT CORPORATION",
    "PARATEK PHARMACEUTICALS, INC",
    "OSANG LLC",
    # Pattern-bucket pass (2026-05-28): 46 added after pattern + SAM/public-knowledge
    # verification. Categories: pharma startups with verified addresses and real
    # websites; government and academic entities; established trade associations;
    # major prime JVs; well-known healthcare subsidiaries.
    "MODEX THERAPEUTICS, INC",
    "MEDICINES COMPANY, THE",
    "VEDANTA BIOSCIENCES, INC.",
    "LOCUS BIOSCIENCES INC",
    "ARCTURUS THERAPEUTICS, INC.",
    "CIDARA THERAPEUTICS INC",
    "MELINTA THERAPEUTICS, LLC",
    "ELUSYS THERAPEUTICS INC",
    "ARGOS THERAPEUTICS, INC.",
    "PARTNER THERAPEUTICS, INC.",
    "PLUS THERAPEUTICS, INC.",
    "IMMEDICA PHARMA US INC.",
    "DYNPORT VACCINE COMPANY LLC",
    "TACONIC BIOSCIENCES, INC.",
    "MAMMOTH BIOSCIENCES INC",
    "MESA BIOTECH INC",
    "RITE AID HDQTRS. CORP.",
    "CVS PHARMACY, INC",
    "TENNESSEE VALLEY AUTHORITY",
    "ARIZONA DEPARTMENT OF EMERGENCY & MILITARY AFFAIRS",
    "THOMAS JEFFERSON UNIVERSITY",
    "STANFORD LELAND JUNIOR UNIVERSITY",
    "HEALTHCARE SERVICES CORPORATION",
    "ASD SPECIALTY HEALTHCARE, LLC",
    "PREMIER HEALTHCARE SOLUTIONS, INC",
    "MILITARY HEALTHCARE OUTFITTING & TRANSITION",
    "HEALTHCARE QUALITY STRATEGIES, INC",
    "SONIC HEALTHCARE USA INC",
    "DEAN/FLUOR, LLC",
    "YOUNG WOMEN'S CHRISTIAN ASSOCIATION OF GREATER LOS ANGELES, CALIFORNIA",
    "INTERNATIONAL MASONRY INSTITUTE",
    "LOS ANGELES COUNTY FAIR ASSOCIATION",
    "COMMUNITY DEVELOPMENT INSTITUTE",
    "COUNCIL FOR LOGISTICS RESEARCH INC",
    "CENTRAL OKLAHOMA AMERICAN INDIAN HEALTH COUNCIL, INC.",
    "TRANSPORTATION COMMUNICATIONS UNION/IAM",
    "CIVIL-MILITARY INNOVATION INSTITUTE INC",
    "ADVANCED REGENERATIVE MANUFACTURING INSTITUTE INC",
    "NATIONAL RURAL WATER ASSOCIATION",
    "MEDSTAR HEALTH RESEARCH INSTITUTE INC.",
    "NATIONAL COLLEGIATE INVENTORS & INNOVATORS ALLIANCE, INC.",
    "KNOX COUNTY ASSOCIATION FOR REMARKABLE CITIZENS, INC.",
    "QUEST DIAGNOSTICS NICHOLS INSTITUTE",
    "NAVAL ACADEMY ATHLETIC ASSOCIATION",
    "COLORADO FOUNDATION FOR MEDICAL CARE",
    "FOUNDATION FOR ATLANTA VETERANS EDUCATION AND RESEARCH, INC.",
    # $50M-$500M tier review (2026-05-28): 50 added after a combination of
    # public-company knowledge, well-known federal contractor recognition, and
    # SAM verification. Categories: major public/pharma/financial firms, major
    # subsidiary or JV identities, established federal IT services and defense
    # contractors, USAID humanitarian implementers.
    "HOFFMANN-LA ROCHE INC.",
    "SHIONOGI INC",
    "MEDIWOUND LTD",
    "CROSSJECT SA",
    "CHIMERIX, INC.",
    "VIR BIOTECHNOLOGY, INC.",
    "INDIVIOR INC.",
    "VAXINNATE CORPORATION",
    "CEMPRA PHARMACEUTICALS, INC.",
    "REMPEX PHARMACEUTICALS, INC.",
    "MAPP BIOPHARMACEUTICAL, INC.",
    "VENTEC LIFE SYSTEMS, INC",
    "BIOFACTURA INC",
    "BIORELIANCE CORPORATION",
    "CODAGENIX INC",
    "JANSSEN RESEARCH & DEVELOPMENT, LLC",
    "PUBLIC HEALTH VACCINES LLC",
    "EMERGENT MANUFACTURING OPERATIONS BALTIMORE LLC",
    "VELICO MEDICAL, INC.",
    "PGIM, INC.",
    "WELLINGTON MANAGEMENT COMPANY LLP",
    "BLUE CROSS AND BLUE SHIELD OF ALABAMA",
    "MINUTECLINIC, L.L.C.",
    "HARRIS CORPORATION",
    "XEROX CORPORATION",
    "SECURITAS SECURITY SERVICES USA, INC.",
    "COGNOSANTE, LLC",
    "INDYNE, INC.",
    "SLALOM, INC.",
    "ATKINSREALIS USA INC",
    "CONTINENTAL MARITIME OF SAN DIEGO, LLC",
    "SCIENCE SYSTEMS AND APPLICATIONS, INC.",
    "T-SOLUTIONS, INC.",
    "ELECTRIFAI, LLC",
    "ASTRIX TECHNOLOGY LLC",
    "ARBOR E & T LLC",
    "IDL SOLUTIONS, LLC",
    "READY COMPUTING GOVERNMENT SOLUTIONS LLC",
    "BASS & ASSOCIATES A PROFESSIONAL CORP",
    "BRILJENT, LLC.",
    "SBCS CORPORATION",
    "CSI AVIATION, INC",
    "START2 GROUP, INC.",
    "RETRACTABLE TECHNOLOGIES INC",
    "AVON PROTECTION CERADYNE LLC",
    "AMEX INTERNATIONAL INCORPORATED",
    "BLUMONT ENGINEERING SOLUTIONS INC",
    "ASCEND PERFORMANCE MATERIALS TEXAS INC",
    "TRUE NORTH COMMUNICATIONS INC",
    "SOS SECURITY LLC",
    # $25M-$50M tier agent review (2026-05-28): 3 Opus agents verified 315
    # candidates via SAM.gov + USASpending + web search; 271 confirmed as
    # legitimate established entities (real federal contractors, established
    # subsidiaries, JVs, ANCs, healthcare orgs, pharma startups with proper
    # SAM history). The 44 'keep' verdicts surfaced shell-pattern candidates
    # worth human review (Stonington Hospitality, Universal Strategic Advisors,
    # CRE8AD8, MEDEA, Do Know Harm, Veterans Command, Federal Government
    # Experts, Lori O May, Rehabplus Staffing, etc.).
    "4J THERAPEUTICS INC",
    "AAR AIRCRAFT SERVICES, INC.",
    "ACUCYBER LLC",
    "ACUITY - CHS MIDDLE EAST, LLC",
    "ADACEL SYSTEMS, INC.",
    "ADAM SMITH US LLC",
    "ADAMS COMMUNICATION & ENGINEERING TECHNOLOGY, INC.",
    "ADVANCED BUSINESS SOFTWARE CONSULTING, LLC",
    "ADVENTUREONE LLC",
    "AERMOR LLC",
    "AES ELECTRICAL, LLC",
    "AFRICA GLOBAL LOGISTICS MOCAMBIQUE, S.A",
    "AGE SOLUTIONS LLC",
    "ALCOR TECHNICAL SOLUTIONS LLC",
    "ALL NATIVE, INC.",
    "ALLSPRING GLOBAL INVESTMENTS, LLC",
    "ALVOGEN, INC.",
    "ANALYTIC ACQUISITIONS LLC",
    "AOC LOGISTICS LLC",
    "APEX DATA SOLUTIONS LLC",
    "APPLIED GEO TECHNOLOGIES, INC.",
    "APTITUDE MEDICAL SYSTEMS INC",
    "AQR CAPITAL MANAGEMENT LLC",
    "ARAMARK SERVICES, INC.",
    "ASCELLON CORPORATION",
    "ASSET PROTECTION & SECURITY SERVICES, L.P.",
    "AUGUSTINE CONSULTING INC",
    "AVITA MEDICAL AMERICAS, LLC",
    "AXSEUM, INC.",
    "AXXUM TECHNOLOGIES LLC",
    "BEVERLY KNITS INC",
    "BIOCRYST PHARMACEUTICALS INC",
    "BOYD BETHESDA II GSA LLC",
    "BRIDGEBORN, INC.",
    "BURLESON RESEARCH TECHNOLOGIES, INC.",
    "BUSHTEX INC",
    "BWS-ARTI LLC",
    "C&T SOLUTIONS, LLC",
    "CAPE FOX FACILITIES SERVICES, LLC",
    "CAPE FOX GOVERNMENT SERVICES, LLC",
    "CAPE REMEDIATION, LLC",
    "CAPITOL MANAGEMENT CONSULTING SERVICES, INC.",
    "CARNEGIE ROBOTICS LLC",
    "CATAPULT HEALTH TECHNOLOGY GROUP, LLC",
    "CATAPULT LEARNING PATRIOT LLC",
    "CEG SOLUTIONS LLC",
    "CERUS CORPORATION",
    "CHAE AND NAM UNIVERSE INC.",
    "CHARTER CONTRACTING COMPANY, LLC",
    "CHARTIS CONSULTING CORPORATION",
    "CLIFFSIDE REFINERS L P",
    "COMBINED TECHNICAL SERVICES, LLC",
    "COMGLOBAL SYSTEMS, INC",
    "COMPLIANCE CORPORATION",
    "COMPU-LINK CORP",
    "COMPUTERCRAFT CORPORATION",
    "COULMED PRODUCTS GROUP LLC",
    "COURTESY ASSOCIATES, LLC",
    "COVENANT LEARNING SOLUTIONS LLC",
    "CYMSTAR SERVICES LLC",
    "CYONE INC",
    "CYQUEST BUSINESS SOLUTIONS, INC.",
    "DAYLIGHT DEFENSE, LLC",
    "DAYLIGHT FOODS, LLC",
    "DEVAL , LLC",
    "DIREKTORATET FOR ROMVIRKSOMHET",
    "DISTINCTION LLC",
    "DNAE GROUP HOLDINGS LIMITED",
    "DOOSANENERBILITY CO., LTD.",
    "DRS GLOBAL ENTERPRISE SOLUTIONS, INC.",
    "DTSV INC.",
    "DUOPROSS MEDITECH CORPORATION",
    "DYNAMIC TECHNOLOGY SYSTEMS, INCORPORATED",
    "E.M. NORTON ENTERPRISES, INC.",
    "EAGLE HARBOR, LLC",
    "EAGLE INDUSTRIES UNLIMITED, LLC",
    "EDUCATION NORTHWEST",
    "ELLUME USA LLC",
    "EMCORE CORPORATION",
    "EMERGENT BIOSOLUTIONS CANADA INC",
    "EMERGENT VIROLOGY, LLC",
    "ENERCON FEDERAL SERVICES INC",
    "ENHANCED VETERANS SOLUTIONS, INC",
    "EXCEL TECHNOLOGIES, LLC",
    "EXPLOSIVE COUNTERMEASURES INTERNATIONAL INC",
    "FACILITY LEADERS IN ARCHITECTURAL/ENGINEERING DESIGN, P.C.",
    "FARM CUT LLC",
    "FIRST DATA CORPORATION",
    "FIRST LIGHT DIAGNOSTICS, INC.",
    "G4S SECURE SOLUTIONS INTERNATIONAL INC.",
    "GARDAWORLD-TRANSGUARD GROUP UAE",
    "GLOBAL AVIATION TECHNOLOGIES LLC",
    "GLOBAL COMMUNITIES, INC.",
    "GLOBAL REACH CONSULTING LLC",
    "GLOBAL SOLUTIONS VENTURES, LLC",
    "GLOBAL TURBINE SERVICES, INC.",
    "GLOBE TECH LLC",
    "GRAND VALLEY MANUFACTURING CO",
    "GREENBERRY INDUSTRIAL LLC",
    "GREENFIELD ENGINEERING CORPORATION",
    "GRIFFON AEROSPACE, INC.",
    "H2O PARTNERS INC",
    "HAMBLE AEROSTRUCTURES LIMITED",
    "HAMILTON SAMLIN MILLIGAN, LLC",
    "HANNA BROTHERS ENTERPRISES, LLC",
    "HARFORD COUNTY, MARYLAND",
    "HAWKEYE 360, INC.",
    "HEALTH QUALITY INNOVATORS",
    "HEALTH SERVICES ADVISORY GROUP OF CALIFORNIA, INC.",
    "HERE NORTH AMERICA LLC",
    "HORIZON STRATEGIES LLC",
    "HTL STREFA INC",
    "HUGHES CONSTRUCTION SERVICES LLC",
    "IAMUS CONSULTING, INC",
    "ID.ME, LLC",
    "IESE SOLUTIONS",
    "IMPROVING ECONOMIES FOR STRONGER COMMUNITIES",
    "INDIAN HEALTH CARE RESOURCE CENTER OF TULSA, INC",
    "INFLAMMATIX, INC.",
    "INFORMATION SYSTEMS SOLUTIONS, INC.",
    "INMAR CLEARING, INC.",
    "INNOVEST SYSTEMS, LLC",
    "INSPIRITEC INC",
    "INTEGRATED LOGISTICS SOLUTIONS, INC.",
    "INTEGRATED SCIENCE SOLUTIONS INC",
    "INTERNATIONAL HEALTH TERMINOLOGY STANDARDS DEVELOPMENT ORGANISATION",
    "INTERNATIONAL RESOURCES GROUP LTD.",
    "INVIRSA, INC.",
    "JBG/BC FISHERS III LP",
    "JET CONSULTING, INC.",
    "K&K JL SERVICES, INC",
    "K2 GROUP, INC.",
    "KAIROS, INC",
    "KETCHUM COMMUNICATIONS INC",
    "KIRA INFORMATION SOLUTIONS LLC",
    "KLD ASSOCIATES INC",
    "KRANZE TECHNOLOGY SOLUTIONS INC",
    "KWELL LABORATORIES INC",
    "LA CASA DEL CAMIONERO, INC.",
    "LINK2GOV, LLC",
    "LKR, LLC",
    "LOGC2 INC",
    "LOUISE W. EGGLESTON CENTER, INC.",
    "LUBERSKI, INC",
    "MACROGENICS, INC.",
    "MAGELLAN TERMINALS HOLDINGS LP",
    "MARATHON ASSET MANAGEMENT LIMITED",
    "MARATHON ASSET MANAGEMENT LLP",
    "MASAI TECHNOLOGIES CORPORATION",
    "MCCONNELL JONES LANIER & MURPHY LLP",
    "MCFARLING FOODS INC",
    "MENU MAKER FOODS, INC.",
    "MERCALIS INC.",
    "MERIDIAN MEDICAL TECHNOLOGIES, LLC",
    "MIDDLE BAY SOLUTIONS LLC",
    "MIRAMAR ENERGY LLC",
    "MIRION TECHNOLOGIES (CANBERRA), INC.",
    "MORGAN, ANGEL, BRIGHAM & ASSOCIATES L.L.C.",
    "MSS SECURITY PTY LIMITED",
    "MTC INTERNATIONAL DEVELOPMENT HOLDING COMPANY, LLC",
    "MULTIQUIP INC.",
    "NATIONAL CAPITAL TREATMENT AND RECOVERY",
    "NATIONAL FACILITY SERVICES INC",
    "NATURAL RESOURCES CONSULTING ENGINEERS, INC.",
    "NETGAIN CORPORATION",
    "NETRIST SOLUTIONS, LLC",
    "NINETY ONE NORTH AMERICA, INC.",
    "NLOGIC, LLC",
    "O.E.S., INC.",
    "OCR GLOBAL INC.",
    "ORION SOLUTIONS, LLC",
    "OSI INDUSTRIES, LLC",
    "OSSIUM HEALTH, INC.",
    "OVID TECHNOLOGIES, INC.",
    "PACT, INC.",
    "PEGASUS TECHNICAL SERVICES, INC",
    "PENGUIN COMPUTING, INC.",
    "PERKINS+WILL, INC.",
    "PERNIX GROUP, INC.",
    "PHARMATHENE UK LTD",
    "PHILIPS RS NORTH AMERICA LLC",
    "PREMIER FOOD GROUP, INC",
    "PRESIDENTIAL AVIATION, INC.",
    "PRIMARIS",
    "QATEX LIMITED",
    "QSA-LLC",
    "QUACITO LLC",
    "QUALITY INVESTIGATION, INC",
    "RANGER AMERICAN OF PUERTO RICO, LLC",
    "RATP DEV USA INC",
    "RDZM, LLC",
    "RE TECH ADVISORS, LLC",
    "RE/SPEC INC.",
    "REDTOWN TECHNICAL SERVICES, LLC",
    "RENZULLI & ASSOCIATES INC",
    "RESMED INC.",
    "REVERSE MARKET INSIGHT, INC.",
    "RNR TECHNOLOGIES INC",
    "ROBOTECH SCIENCE, INC.",
    "ROCKY MOUNTAIN CENTER FOR HEALTH PROMOTION AND EDUCATION",
    "ROSHEL INC.",
    "RW HOLDINGS NNN REIT, INC.",
    "S & B INFRASTRUCTURE LTD",
    "SAFEGUARD SECURITY SERVICES (PVT) LTD",
    "SAGE SYSTEMS TECHNOLOGIES, LLC",
    "SAINT GEORGE CONSULTING INC.",
    "SAMTEK INC",
    "SCALED COMPOSITES, LLC",
    "SENTINEL GROUP LLC",
    "SG2 LLC",
    "SIGMA SERVICES, INC.",
    "SILVER EAGLE MANUFACTURING CO",
    "SIRO DIAGNOSTICS, INC.",
    "SOCIAL SECTOR DEVELOPMENT STRATEGIES, INC",
    "SOLVAY PHARMACEUTICALS, INC.",
    "SOUTHEASTERN COMPUTER CONSULTANTS INC",
    "SPARKSOFT CORPORATION",
    "SPECTRAL MD, INC.",
    "STANLEY MARVEL INC.",
    "STARSIDE SECURITY & INVESTIGATION INC",
    "STIMULUS ENGINEERING SERVICES, INC.",
    "STRATEGIC TECHNOLOGIES ANALYTICS GROUP, LIMITED LIABILITY COMPANY",
    "SUBMERGENCE GROUP LLC",
    "SUMMIT2SEA CONSULTING, LLC",
    "SYSTEMS ENGINEERING SOLUTIONS CORPORATION",
    "SYSTEX, INC",
    "T2 BIOSYSTEMS, INC",
    "T3I, INC.",
    "TALTON COMMUNICATIONS INC",
    "TAMIMI COMPANY",
    "TASTY BRANDS LLC",
    "TCW ASSET MANAGEMENT COMPANY LLC",
    "TECHLAW CONSULTANTS INC",
    "TELECOMMUNICATION SUPPORT SERVICES, INC.",
    "TERRATHERM, INC.",
    "TERUMO BCT BIOTECHNOLOGIES, LLC",
    "THE ASSOCIATED PRESS",
    "THE IBEX GROUP INC",
    "THE KENRICH GROUP, LLC",
    "THE MERCHANTS COMPANY, LLC",
    "THE MILLER/HULL PARTNERSHIP, LLP",
    "THE SALVATION ARMY",
    "THOROUGHBRED RESEARCH GROUP, INC.",
    "TIYA SERVICES, L.L.C.",
    "TK ELEVATOR CORPORATION",
    "TMSAB LLC",
    "TORRES-AVARN SECURITY LLC",
    "TOTARA LEARNING, INC.",
    "TRAILBLAZER HEALTH ENTERPRISES LLC",
    "TRANDES CORP",
    "TRANS MANAGEMENT SYSTEMS CORP",
    "TRAVEL WELL HOLDINGS LLC",
    "TRICENTURION INC",
    "TRISEPT CORPORATION",
    "TTEC GOVERNMENT SOLUTIONS LLC",
    "UAVIONIX CORP",
    "UNITED SECURITY AGENCY, LLC",
    "VARIOSCALE, INC",
    "VENEGAS CONSTRUCTION CORP",
    "VERICEL CORPORATION",
    "VERTEX MODERNIZATION AND SUSTAINMENT LLC",
    "VINNELL CORPORATION",
    "VIRGIN ORBIT NATIONAL SYSTEMS, LLC",
    "VISION SYSTEMS INTERNATIONAL LLC",
    "VITOL AVIATION CO",
    "VOLANT ASSOCIATES LLC",
    "VT AEPCO INC.",
    "WHITSONS FOOD SERVICE (BRONX), LLC",
    "WOOD FEDERAL SOLUTIONS, INC.",
    "XATOR LLC",
    "XTENFER CONSULTING INC.",
    # Under-$25M tier agent review (2026-05-28): 3 Opus agents verified 253
    # candidates; 238 confirmed as legitimate established entities (heavy
    # concentration of ANC subsidiaries, Native Hawaiian organizations,
    # tribal subsidiaries, public-company subs, and BARDA/RADx-funded
    # diagnostics startups). The 15 'keep' verdicts surfaced shell-pattern
    # candidates including the ProPublica-documented Bayhill Defense N95
    # failure, Allied Sonoran (2024 Wyoming LLC, $14M FAA), Edge Ops
    # (SAM registered weeks before $12M DHS), Ardent Group (sole
    # proprietorship with LLC name, $21M DHS), and others.
    "5600 FISHERS LANE LLC",
    "ABRIDGE AI INC",
    "ACATO INFORMATION MANAGEMENT, LLC",
    "ACCESS TO HEALTH ZAMBIA",
    "ACUMENTRA HEALTH, INC.",
    "ADAMO CONSTRUCTION INC",
    "ADL DIAGNOSTICS, INC.",
    "ADVISEWELL, INC.",
    "AEGISOUND, LLC",
    "AEOPORT INTERNATIONAL DE DJIB OUTI",
    "AIMPOINT INC",
    "AKIAK TECHNOLOGY, LLC",
    "AL MANARAH COMMUNICATIONS & INFORMATION TECHNOLOGY CO LTD",
    "ALLIED HEALTH CARE SERVICES",
    "AMERICAN VET WORKS, INC.",
    "AMI FEDERAL SERVICES, INC",
    "APPLIED ENERGY LLC",
    "APRIVA ISS LLC",
    "ARABIAN SERVICES LIMITED COMPANY",
    "ARAMARK SERVICES, INC",
    "ARTICUS SOLUTIONS LLC",
    "ASIA SATELLITE TELECOMMUNICATIONS COMPANY LIMITED",
    "ASSOCIATED PATHOLOGISTS, LLC",
    "ATKINSREALIS ENERGY FEDERAL EPC INC.",
    "BEACON GROUP, INC.",
    "BEAR DEN TECHNOLOGY, LLC",
    "BEYON B.S.C",
    "BLOCKTRACE, LLC",
    "BML, INC.",
    "BONA FIDE CONGLOMERATE, INC.",
    "CADRE5, LLC",
    "CALVERT SYSTEMS ENGINEERING, INC",
    "CAPE FOX FEDERAL INTEGRATORS, LLC",
    "CAPE FOX FEDERAL INTERGRATORS, LLC",
    "CARIBBEAN LUMBER & HARDWARE, INC",
    "CENTERPLATE, INC.",
    "CENTERPOINT PROPERTIES TRUST",
    "CHAINALYSIS GOVERNMENT SOLUTIONS, LLC",
    "CHICKASAW FEDERAL HEALTH, LLC",
    "CINCOM SYSTEMS, INC.",
    "CLEAR CREEK FEDERAL, LLC",
    "CLEARFOCUS TECHNOLOGIES LLC",
    "CLEVELAND THERMAL, LLC",
    "COLISEUM ADVISORY BOARD",
    "COMMUNITYFORCE INCORPORATED",
    "CONTROLANT HF.",
    "CONTROLLER BAY LLC",
    "CONVERDYN, GP",
    "COOPER MACHINERY SERVICES LLC",
    "CREATIVE IT SOLUTIONS, LLC",
    "CRI FEDERAL SERVICES",
    "CYBER SECURITY PROFESSIONALS, INC.",
    "DAWSON-ISC GROUP, LLC",
    "DAYCRAFT SYSTEMS CORPORATION",
    "DEFENSE MUNITIONS INTERNATIONAL , L.L.C.",
    "DEFENSE REALTY LLC",
    "DLX ENTERPRISES LLC",
    "DOERFER CORPORATION",
    "DOMINO DATA LAB INC",
    "DOOR OF OPPORTUNITY INC",
    "DRC EMERGENCY SERVICES, LLC",
    "DYNAXYS, LLC",
    "EAGLE HEALTH ANALYTICS, LLC",
    "EAGLE ONE SOLUTIONS, INC.",
    "EARTHDAILY FEDERAL, INC.",
    "ELECTRO-MINIATURES CORP.",
    "ELIT SEKYURITI SERVIS, OOO",
    "ELTA NORTH AMERICA INC.",
    "EMERGENT PRODUCT DEVELOPMENT GAITHERSBURG INC.",
    "ENERGY, DEPARTMENT OF",
    "EVERGY METRO, INC.",
    "FAR EAST SUPPORT SERVICES LLC",
    "FIDELITY TECHNOLOGY SERVICES, LLC",
    "FISHERS LANE LLC",
    "FLAGSHIP CUSTOMS SERVICES, INC.",
    "FLAMBEAU DIAGNOSTICS LLC",
    "FORTIS NATIVE GROUP LLC",
    "FREQUENCY ELECTRONICS INC",
    "FREQUENTIS DEFENSE INC",
    "FRESENIUS KABI, LLC",
    "FRONTIER AEROSPACE CORP",
    "GARNER ENVIRONMENTAL SERVICES INC",
    "GENBODY INC.",
    "GEODETICS, INC",
    "GESHER HUMAN SERVICES",
    "GIG LINE MEDIA, INC.",
    "GLOBECAST AMERICA INCORPORATED",
    "GOEX INDUSTRIES LLC",
    "GOODWILL INDUSTRIAL SERVICES OF FORT WORTH, INC.",
    "GREEN LABEL SERVICES LIMITED",
    "GREINER BIO-ONE NORTH AMERICA, INC",
    "GVS FILTRATION INC.",
    "H & T ENTERPRISES, INC.",
    "HCEI, INC.",
    "HEALTH CARE SERVICE CORPORATION, A MUTUAL LEGAL RESERVE COMPANY",
    "HEALTHINSIGHT OF NEVADA INC",
    "HEIDRICK & STRUGGLES INTERNATIONAL INC",
    "HEYLTEX CORPORATION",
    "HGSNET, LLC",
    "HID GLOBAL CORP",
    "HOWARD INDUSTRIES, INC.",
    "ILC ASTROSPACE LLC",
    "ILLUMIO GOVERNMENT SOLUTIONS, LLC",
    "IMECO INC",
    "INDIAN HEALTH BOARD OF MINNEAPOLIS, INC.",
    "INDUSTRIAL VIDEO & CONTROL CO LLC",
    "INFINITE TECHNOLOGIES, INC.",
    "INFINITY BIOLOGIX LLC",
    "INSIGNIA HEALTH, LLC",
    "INSURANCE SERVICES OFFICE, INC.",
    "INTEGRES, LLC",
    "IVA'AL/NAIS TECHNOLOGIES LLC",
    "JADIN TECH, LLC",
    "JAMS, INC.",
    "JBGS/TRS LLC",
    "JDL DIGITAL SYSTEMS, INC.",
    "KATMAI DIVERSIFIED SERVICES LLC",
    "KATMAI GLOBAL SOLUTIONS LLC",
    "KCORP-DEAN LLC",
    "KIND INC",
    "KIRA FACILITIES MAINTENANCE LLC",
    "KONECRANES INC",
    "KRYPTOWIRE INC",
    "KYO-YA HOTELS & RESORTS, LP",
    "LARSEN AND TOUBRO LIMITED",
    "LAWELAWE TECHNOLOGY SERVICES, INC.",
    "LEOLABS INC",
    "LIFEROOTS, INC",
    "LINCHPIN LABS INC",
    "LYCEUM BUSINESS SERVICES, LLC",
    "LYDALL PERFORMANCE MATERIALS, INC.",
    "MARATHON TARGETS INC",
    "MASHREQ ARABIA",
    "MASSACHUSETTS PEER REVIEW ORGANIZATION, INC.",
    "MASSMUTUAL ASSET FINANCE LLC",
    "MASTERWORD SERVICES, INC.",
    "MAYSTREET INC",
    "MCI DIAGNOSTIC CENTER LLC",
    "MEDICOMP SYSTEMS INC",
    "MEDIQUANT, LLC",
    "MERLIN LABS INC",
    "METALEX MANUFACTURING INC",
    "METRO OFFICE MANAGEMENT, INC.",
    "MI TECHNICAL SOLUTIONS, INC.",
    "MILLSAPPS, BALLINGER & ASSOCIATES, INC.",
    "MINUTE MOLECULAR DIA",
    "MIPPS, LLC",
    "MOLOGIC INC.",
    "MSI-DEFENCE SYSTEMS US, LLC",
    "MUSCOGEE INTERNATIONAL LLC",
    "MWH AMERICAS, INC.",
    "N.V.I. INC.",
    "NATIONAL SALUTE MANAGEMENT LLC",
    "NATSIONALNY YADERNY TSENTR RESPUBLIKI KAZAKHSTAN, GP",
    "NEXSYS ELECTRONICS, INC.",
    "NFB MANAGEMENT CONSULTANTS, LLC",
    "NICHE TECHNOLOGY INC",
    "NORTH METRO COMMUNITY SERVICES, INC",
    "NORTH WIND RESOURCE CONSULTING, LLC",
    "OLH TECHNICAL SERVICES, LLC",
    "ONEIDA PROFESSIONAL SERVICES LLC",
    "OPS-CORE INC.",
    "OPTEC, INC.",
    "PACE ENTERPRISES OF WEST VIRGINIA INC",
    "PARKDALE ADVANCED MATERIALS, INC.",
    "PEGASYSTEMS INC.",
    "PHANEUF ASSOCIATES INCORPORATED",
    "PHOENIX DYNAMICS LIMITED",
    "PHOTRONICS IDAHO, INC.",
    "PLATEAU SYSTEMS, LLC",
    "POWTEC LINTECH LLC",
    "PREMIER TECHNOLOGY, INC.",
    "PRIVORO GOVERNMENT SOLUTIONS, LLC",
    "PROFESSIONAL SOFTWARE CONSORTIUM INC",
    "PROTEGE HEALTH SERVICES LLC",
    "PUBLICRELAY INC",
    "QLARANT QUALITY SOLUTIONS, INC.",
    "QORVO BIOTECHNOLOGIES LLC",
    "QUANTERIX CORPORATION",
    "QUANTUM-SYSTEMS INC",
    "RED ONE MEDICAL DEVICES LLC",
    "RFD BEAUFORT INC.",
    "RIDGEBACK BIOTHERAPEUTICS LP",
    "RIDGEWOOD TECHNOLOGY PARTNERS LLC",
    "ROSATO ASSOCIATES, INC.",
    "RQI PARTNERS LLC",
    "SAFRAN HELICOPTER ENGINES USA, INC",
    "SAN DIEGO CONVENTION CENTER CORPORATION, INC.",
    "SAULT TRIBE SOLUTION SERVICES, LLC",
    "SEAKR ENGINEERING, LLC",
    "SECOTEC INC",
    "SEKISUI DIAGNOSTICS LLC",
    "SERRATO CORPORATION",
    "SHAMROCK FOODS COMPANY",
    "SHEARWATER SYSTEMS, LLC",
    "SHORELAND INC",
    "SIERRA SPACE CORP",
    "SIERRA WIRELESS AMERICA, INC",
    "SMG HOLDINGS LLC",
    "SOLUTIONS THROUGH INNOVATIVE TECHNOLOGIES, INC",
    "SPECPRO MANAGEMENT SERVICES, LLC",
    "SPECPRO PROFESSIONAL SERVICES, LLC",
    "SQUARE ONE ARMORING SERVICES CO",
    "SRL, INC.",
    "STARR COMMONWEALTH",
    "STRATA DECISION TECHNOLOGY, L.L.C.",
    "STRATTON SECURITIES INC.",
    "SUNGARD AVAILABILITY SERVICES, LP",
    "SUVI GLOBAL SERVICES LLC",
    "SYNENSYS, LLC",
    "SYNERGISTICS INC",
    "SYNERGY PARTNERS LLC",
    "TALBERT MANUFACTURING INC",
    "TALIS BIOMEDICAL CORPORATION",
    "TANAQ MANAGEMENT SERVICES LLC",
    "TANAQ SUPPORT SERVICES, LLC",
    "TC TECHNOLOGY SOLUTIONS, LLC",
    "TELEDYNE ENERGY SYSTEMS, INC",
    "TELLIGEN ILLINOIS, LLC",
    "THE CONSULTING NETWORK, INC.",
    "THE MAGIS GROUP, LLC",
    "THE NEW MEXICO COMMISION FOR BLIND",
    "TMGL LLC",
    "TRIBAL ONE TECHNOLOGY, LLC",
    "TRIUMPH ACTUATION SYSTEMS - CONNECTICUT, LLC",
    "TUVLI LLC",
    "UAW- LABOR EMPLOYMENT AND TRAINING CORPORATION",
    "UH-OH LABS INC",
    "USGBF NIAID LLC",
    "VECTREN LLC",
    "VERADIGM LLC",
    "VICK ROBERT E JR",
    "VIDEORAY LLC",
    "WASHINGTON BUSINESS DYNAMICS, LLC",
    "WGL ENERGY SYSTEMS, INC",
    "WOLFSPEED, INC.",
    "WOLTERS KLUWER HEALTH, INC.",
    "XOMA LTD.",
    # $100M+ tier sweep (2026-05-28): 7 of 11 confirmed legitimate after
    # individual SAM verification. Zeva is a real PKI/identity small business
    # whose $1B Treasury figure is an IDIQ ceiling. Saalex, Lovell, Core4CE
    # are established SDVOSB/SDB defense contractors. SanMar is the well-known
    # wholesale apparel distributor. S.C.A. is a 25-year UK shipping
    # consultancy. HTA-Triad is a Healthcare Trust of America real-estate SPE.
    # Kept on dashboard: Salus (known shell), CODA Research (no SAM record),
    # SafeSource Direct (2021 PPE JV). ERI Services moved to filter 2026-05-30
    # after confirming NORESCO operating identity (see entry near end of list).
    "ZEVA INCORPORATED",
    "SAALEX CORP",
    "LOVELL GOVERNMENT SERVICES INC.",
    "SAN MAR CORPORATION",
    "CORE4CE LLC",
    "S.C.A. - SHIPPING CONSULTANTS ASSOCIATED LTD.",
    "HTA - TRIAD, LLC",
    "10 TANKER AIR CARRIER, LLC",
    "AAC INC.",
    "AAR AIRLIFT GROUP, INC.",
    "AAR GOVERNMENT SERVICES, INC.",
    "ABL SPACE SYSTEMS COMPANY",
    "ABSG CONSULTING INC",
    "ABSOLUTE BUSINESS SOLUTIONS, INC.",
    "AC FIRST, LLC",
    "ACADEMY FOR EDUCATIONAL DEVELOPMENT, INC.",
    "ACCESS BIO, INC.",
    "ACCOUNT CONTROL TECHNOLOGY INC.",
    "ACI TECHNOLOGIES, INC.",
    "ACT1 FEDERAL LLC",
    "ACTION FINANCIAL SERVICES, LLC",
    "ACTIONABLE SOLUTIONS GROUP, LLC",
    "ACUITUS INC",
    "ADAMS COMMUNICATION & ENGINEERING TECHNOLOGY INC",
    "ADAPT FORWARD LLC",
    "ADNET SYSTEMS INC",
    "ADR VANTAGE INC",
    "ADVANCED INFORMATION ENGINEERI",
    "ADVANCED TURBINE ENGINE CO LLC",
    "ADVANCEMED CORPORATION",
    "AED STRATECON LLC",
    "AEGIS AEROSPACE INC",
    "AERIE AEROSPACE LLC",
    "AERO AIR, LLC",
    "AERO-FLITE, INC.",
    "AEROCLAVE LLC",
    "AERODYNE, INC.",
    "AERODYNE-SGT ENGINEERING SERVICES, LLC",
    "AEROJET ELECTRO SYSTEMS",
    "AEROS AERONAUTICAL SYSTEMS CORP.",
    "AEROVIRONMENT INC",
    "AEROVIRONMENT, INC",
    "AGILE DECISION SCIENCES, LLC",
    "AGILITY DGS LOGISTICS SERVICES COMPANY KSCC",
    "AI SIGNAL RESEARCH INC",
    "AIRCRAFT READINESS ALLIANCE, LLC",
    "AL RAHA GROUP FOR TECHNICAL S ERVICES",
    "ALLIANT SOLUTIONS, LLC",
    "ALLIED INTERSTATE LLC",
    "ALLIED TECHNOLOGY GROUP, INC.",
    "ALLIEDBARTON SECURITY SERVICES LLC",
    "ALLISON TRANSMISSION, INC.",
    "ALLTRAN EDUCATION, INC.",
    "ALTERNATE PERSPECTIVES INC",
    "AMERESCO FEDERAL SOLUTIONS, INC.",
    "AMERICAN CENTRIFUGE OPERATING, LLC",
    "AMERICAN INSTITUTE IN TAIWAN",
    "AMERICAN MANAGEMENT SYSTEMS INCORPORATED",
    "AMERICAN PETROLEUM TANKERS LLC",
    "AMERICAN PURCHASING SERVICES, LLC",
    "AMI METALS, INC",
    "AMS INTEGRATED SOLUTIONS FZ-LLC",
    "AMTEC CORPORATION",
    "ANDURIL INDUSTRIES, INC.",
    "APIJECT SYSTEMS AMERICA, INC.",
    "APOGEN TECHNOLOGIES, INC.",
    "APPLIED INFORMATION SCIENCES INC",
    "AQUA ENGINEERS INC",
    "ARANEA SOLUTIONS INC",
    "ARBUTUS BIOPHARMA CORPORATION",
    "ARCATA ASSOCIATES INC",
    "ARES TECHNICAL SERVICES CORPORATION",
    "ARINC INCORPORATED",
    "ARMY SUSTAINMENT LLC",
    "ARROW SCIENCE AND TECHNOLOGY, L.L.C.",
    "ARROWSTREET CAPITAL LP",
    "AS AND D, LLC",
    "ASHBRITT INC",
    "ASSET MANAGEMENT SPECIALISTS LLC",
    "ASSOCIATION OF UNIVERSITIES FOR RESEARCH IN ASTRONOMY, INC.",
    "ASTRAEUS OPERATIONS, LLC",
    "ATAMIR - WSMR LLC",
    "ATK LAUNCH SYSTEMS LLC",
    "ATKINS NORTH AMERICA INC",
    "ATLANTIC INDUSTRIAL COATINGS LIMITED LIABILITY COMPANY",
    "ATLAS ADVISORS LLC",
    "ATP2 LLC",
    "B.I. INCORPORATED",
    "BALDWIN GROUP, INC., THE",
    "BANNER QUALITY MANAGEMENT INC",
    "BASTION TECHNOLOGIES, INC.",
    "BEAR DEFENSE SERVICES LLC",
    "BEAUFORT-JASPER WATER & SEWER AUTHORITY",
    "BEYOND NEW HORIZONS, LLC",
    "BIONETICS CORP",
    "BIRD-JOHNSON PROPELLER COMPANY, LLC",
    "BLACK & VEATCH CORPORATION",
    "BLACK CANYON CONSULTING LLC",
    "BLB RESOURCES, INC.",
    "BLUE STAR NBR LLC",
    "BLUEFORGE ALLIANCE",
    "BOSTON DYNAMICS, INC.",
    "BOWHEAD INTEGRATED SUPPORT SERVICES LLC",
    "BOWHEAD SCIENCE AND TECHNOLOGY, LLC",
    "BOWMAN CONSULTING GROUP LTD",
    "BP SINGAPORE PTE. LIMITED",
    "BRIEFCASE SYSTEMS DEVELOPMENT INC",
    "BSC PARTNERS, LLC",
    "BVG & CO CONSULTING LLC",
    "C & C PRODUCE, LLC.",
    "CABEZON GROUP, INC",
    "CAE INC",
    "CAPITOL TECHNOLOGY SERVICES, INCORPORATED",
    "CARDINAL INTELLECTUAL PROPERTY INC.",
    "CAREER SYSTEMS DEVELOPMENT CORPORATION",
    "CARELON BEHAVIORAL HEALTH, INC.",
    "CARRINGTON MORTGAGE SERVICES LLC",
    "CDM FEDERAL PROGRAMS CORP",
    "CENTURUM TECHNICAL SOLUTIONS, INC.",
    "CG SERVICES LLC",
    "CHANGE HEALTHCARE TECHNOLOGIES, LLC",
    "CHEVRON AL KHALIJ",
    "CHEVRON BAHRAIN TRADING COMPANY W.L.L",
    "CLEAN HARBORS ENVIRONMENTAL SERVICES INC",
    "CLEAR VANTAGE POINT SOLUTIONS II LLC",
    "CLINISYS, INC.",
    "CLP INDUSTRIAL PROPERTIES LLC",
    "CNF TECHNOLOGIES CORPORATION",
    "COLEMAN RESEARCH CORPORATION",
    "COLLECTION TECHNOLOGY INCORPORATED",
    "COLUMBIA HELICOPTERS, INC.",
    "COLUMBUS TECHNOLOGIES AND SERVICES, INC",
    "COMPOSITE ANALYSIS GROUP, INC.",
    "CONSTRUCTION HELICOPTERS, INC.",
    "CONSULTING SERVICES GROUP, LLC",
    "CONTINENTAL SERVICE GROUP, LLC",
    "CONTINUUM OF CARE INC",
    "CONTINUUS PHARMACEUTICALS, INC.",
    "CORNELL COMPANIES, INC.",
    "CORRECTIONAL ALTERNATIVES LLC",
    "COULSON AVIATION (USA), INC.",
    "COVENANT AVIATION SECURITY, LLC",
    "CPI SATCOM & ANTENNA TECHNOLOGIES INC.",
    "CREATIVE COMPUTING SOLUTIONS, INC.",
    "CREDENCE MANAGEMENT SOLUTIONS LIMITED LIABILITY COMPANY",
    "CRI ADVANTAGE INC",
    "CROWLEY ALASKA, INC",
    "CROWLEY ENERGY ANCHORAGE, LLC",
    "CUBIC DEFENSE APPLICATIONS, INC.",
    "CUBIC GLOBAL DEFENCE, INC",
    "DATA MONITOR SYSTEMS INC",
    "DATA TRANSFORMATION CORP",
    "DAVENPORT AVIATION INC",
    "DAVIE DEFENSE INC.",
    "DAVIES OFFICE REFURBISHING, INC.",
    "DAVITA INC.",
    "DEEP MILE NETWORKS LLC",
    "DEFENSE FACILITIES ADMINISTRATION AGENCY",
    "DELTA MANAGEMENT ASSOCIATES, INC",
    "DEPLOYED SERVICES, LLC",
    "DIMARE FRESH, INC.",
    "DNT SOLUTIONS, LLC",
    "DOVEL TECHNOLOGIES LLC",
    "DTECHLOGIC LLC",
    "DYNCORP TECHNICAL SERVICES INC",
    "DZSP 21 LLC",
    "E BROKER AGENCIA DE SEGUROS LTDA",
    "EBSCO INFORMATION SERVICES, LLC",
    "ECC INTERNATIONAL, LLC",
    "ECKERD YOUTH ALTERNATIVES, INC.",
    "EDUCATION MANAGEMENT CORPORATION",
    "ELLERBE BECKET COMPANY, THE",
    "EMD MILLIPORE CORP",
    "EMINENT SECURITY INC.",
    "ENCOMPASS DIGITAL MEDIA, INC.",
    "ENERGX TN, LLC",
    "EOS USA, INC.",
    "EQMS-BEM JVII, LLC",
    "EQUINIX GOVERNMENT SOLUTIONS LLC",
    "ETOUCH SYSTEMS CORP.",
    "EUROPEAN SPACE RESEARCH AND TECHNOLOGY CENTRE",
    "EUTELSAT AMERICA CORP.",
    "EVERWATCH SOLUTIONS INC.",
    "EXCALIBUR ASSOCIATES INC",
    "F.H. CANN & ASSOCIATES, INC.",
    "FALCONWOOD",
    "FAMILY ENDEAVORS, INC.",
    "FEDERAL MISSION SOLUTIONS, LLC",
    "FEDERAL PROGRAM INTEGRATORS, LLC",
    "FIBROTEX USA INC.",
    "FIDELITY FLIGHT SIMULATION INCORPORATED",
    "FINANCIAL ASSET MANAGEMENT SYSTEMS, INC.",
    "FIRELAKE-ARROWHEAD NASA SERVICES",
    "FIRSTLINE TRANSPORTATION SECURITY, INC",
    "FISH GUIDANCE SYSTEMS LIMITED",
    "FIVE STAR GOURMET FOODS, INC.",
    "FLATWATER SOLUTIONS COMPANY",
    "FLIGHTSAFETY DEFENSE CORPORATION",
    "FMS INVESTMENT CORP",
    "FOCUS REVISION PARTNERS LLC",
    "FORETHOUGHT, INC.",
    "FRANKLIN COURT INC",
    "FREELON GROUP, INC., THE",
    "FRESENIUS MEDICAL CARE HOLDINGS INC",
    "FRONTIER SYSTEMS INTEGRATOR, LLC",
    "FUELING SYSTEMS CONTRACTORS, LLC",
    "G D ARAB LIMITED COMPANY",
    "GARDAWORLD FEDERAL NIGERIA",
    "GC SERVICES LIMITED PARTNERSHIP",
    "GDC MIDDLE EAST COMPANY",
    "GENCO INFRASTRUCTURE SOLUTIONS, INC.",
    "GENCO SYSTEMS INC",
    "GENERAL MATTER, INC.",
    "GENERAL MICRO SYSTEMS, INC",
    "GENESIS ENGINEERING SOLUTIONS, INC.",
    "GIBBCO LLC",
    "GIBBS & COX INC",
    "GLOBAL COMPUTER ENTERPRISES, INC.",
    "GLOBAL ENTERPRISE SOLUTIONS, INC.",
    "GLOBAL INTEGRATED SECURITY (USA) INC.",
    "GLOBAL MARITEK SYSTEMS INC",
    "GLOBAL PATENT SOLUTIONS, L.L.C.",
    "GLOBAL RECEIVABLES SOLUTIONS INC.",
    "GLOBAL TRADING ENTERPRISES LLC",
    "GLOBALFOUNDRIES U.S. 2 LLC",
    "GM GDLS DEFENSE GROUP, L.L.C.",
    "GOFRESH, LLC",
    "GOLD STAR FOODS, INC.",
    "GOLDSCHMITT-CRI LLC",
    "GORDON FOOD SERVICE, INC",
    "GOURMET GORILLA INC",
    "GRADCO LLC",
    "GRAND RIVER ASEPTIC MANUFACTURING, INC.",
    "GRAY RESEARCH INC",
    "GREAT EASTERN GROUP, INC.",
    "GREAT LAKES EDUCATIONAL LOAN SERVICES, INC",
    "GROWTH VENTURES INC",
    "GRYPHON VALLECITOS LABORATORIES, LLC",
    "GTE INTERNETWORKING INC",
    "GUARDIANS OF HONOR, LLC",
    "GUNDERSON MARINE LLC",
    "H2 TECHNOLOGY GROUP, LLC",
    "HAMILTON SUNDSTRAND CORPORATIO",
    "HAMPTON UNIVERSITY",
    "HANESBRANDS INC.",
    "HANWHA OCEAN CO., LTD.",
    "HAROLD LEMAY ENTERPRISES, INCORPORATED",
    "HARRINGTON, MORAN, BARKSDALE, INC.",
    "HARRIS TECHNICAL SERVICES CORPORATION",
    "HDR ARCHITECTURE, INC.",
    "HEARST MEDIA PRODUCTION GROUP LLC",
    "HEIL TRAILER INTERNATIONAL, LLC",
    "HELICOPTER SUPPORT, INC",
    "HELICOPTER TRANSPORT SERVICES, LLC",
    "HELLMUTH, OBATA & KASSABAUM, INC.",
    "HENNINGSON, DURHAM & RICHARDSON, P.C",
    "HIGH DESERT SUPPORT SERVICES, LLC",
    "HIGHER EDUCATION SERVICING CORP",
    "HOME BUILDERS INSTITUTE",
    "HOMECARE PRODUCTS, INC.",
    "HORIZON DJIBOUTI TERMINALS LTD",
    "HUMANITAS, INC.",
    "ICF JACOB & SUNDSTROM, INC.",
    "IDENTITY THEFT GUARD SOLUTIONS, INC.",
    "IHEALTH LABS INC.",
    "IMMEDIATE CREDIT RECOVERY, INC.",
    "IMPACT RESOURCES, INC",
    "IMPSA INTERNATIONAL, INC",
    "INCYTE CORPORATION",
    "INDEPENDENT ROUGH TERRAIN CENTER LLC",
    "INDIANA UNIVERSITY HEALTH CARE ASSOCIATES, INC.",
    "INFICON INC",
    "INFOPOINT LLC",
    "INGLETT & STUBBS INTERNATIONAL, LLC",
    "INNOVAIR LLC",
    "INOMEDIC HEALTH APPLICATIONS, INC.",
    "INTELLIDYNE, L.L.C.",
    "INTELLIGENT WAVES LLC",
    "INTELSAT GENERAL COMMUNICATIONS LLC",
    "INTER-CON SECURITY SYSTEMS, INC.",
    "INTERCHURCH MEDICAL ASSISTANCE, INC.",
    "INTERNATIONAL BUSINESS MACHINES CORPORATION",
    "INTERNATIONAL DEVELOPMENT SOLUTIONS LLC",
    "INTERNATIONAL UNION OF PAINTERS AND ALLIED TRADES",
    "INVERNESS TECHNOLOGIES INC",
    "IRIDIUM GOVERNMENT SERVICES LLC",
    "ISOTEK SYSTEMS, LLC",
    "ISRAEL AEROSPACE INDUSTRIES LTD. AVIATION GROUP LAHAV",
    "IT SHOWS, INC.",
    "IT WORKS! INC.",
    "IUPAT FINISHING TRADES INSTITUTE",
    "JACOB'S EYE, LLC",
    "JANCO FS 2, LLC",
    "JAR ASSETS, LLC",
    "JARIA LLC",
    "JOHN SNOW, INCORPORATED",
    "JOINT TECHNICAL SOLUTIONS, LLC",
    "JSI RESEARCH & TRAINING INSTITUTE INC",
    "JT4 LLC",
    "KALS, LLC",
    "KAMAN AEROSPACE CORPORATION",
    "KAMAN PRECISION PRODUCTS, LLC",
    "KASOTC SPECIAL OPERATIONS TRAINING CENTER",
    "KATMAI QUANTUM LLC",
    "KATO ENGINEERING INC.",
    "KAY AND ASSOCIATES, INC.",
    "KBC ENERGY SOLUTIONS LLC",
    "KEENAN FT DETRICK ENERGY LLC",
    "KELLY AVIATION CENTER, L.P.",
    "KENYA MEDICAL SUPPLIES AUTHOR ITY",
    "KEYSTONE PREPOSITIONING SERVICES INC",
    "KIEWIT INFRASTRUCTURE WEST CO.",
    "KINDER MORGAN TANK STORAGE TERMINALS LLC",
    "KIRA LLC",
    "KNAPP INC",
    "KONGSBERG SATELLITE SERVICES AS",
    "KREISERS, LLC",
    "KWAAN TECH, LLC",
    "KWAJALEIN RANGE SERVICES, LLC",
    "L&M TECHNOLOGIES, INC.",
    "L-1 IDENTITY SOLUTIONS, INC.",
    "L2 SOLUTIONS LLC",
    "LABCON, NORTH AMERICA",
    "LEAVENWORTH-JEFFERSON ELECTRICAL COOPERATIVE INC",
    "LHDTSV LLC",
    "LIFE SCIENCE LOGISTICS LLC",
    "LIFE SCIENCE LOGISTICS, LLC",
    "LINDE SPECIAL PROJECTS LLC",
    "LION-VALLEN LIMITED PARTNERSHIP",
    "LIVEOPS AGENT SERVICES LLC",
    "LMR TECHNICAL GROUP LLC",
    "LOGICORE CORP",
    "LONG TERM CARE PARTNERS LLC",
    "LONGHORN VACCINES AND DIAGNOSTICS, LLC",
    "MACRO COMPANIES, INC.",
    "MAGNIX USA INC",
    "MAINTHIA TECHNOLOGIES INC",
    "MAMMOTH TECH INC",
    "MARK G. ANDERSON CONSULTANTS INC.",
    "MARTIN MARIETTA SPEC COMPONENT",
    "MARVELL GOVERNMENT SOLUTIONS LLC",
    "MASTER SOLUTIONS TRADUCCIONES S A S",
    "MAV6, LLC",
    "MAXIM BIOMEDICAL, INC",
    "MAXIMUS EDUCATION LLC",
    "MBDA INCORPORATED",
    "MCNEIL SECURITY INC",
    "MD HELICOPTERS, LLC",
    "MEDICAL ACCESS UGANDA LIMITED",
    "MEDIVECTOR, INCORPORATED",
    "METRICA, INC.",
    "METRO LOGICS INC",
    "METSON MARINE SERVICES INCORPORATED",
    "METTLER-TOLEDO RAININ, LLC",
    "MHM INNOVATIONS, INC.",
    "MHN GOVERNMENT SERVICES LLC",
    "MIDWEST JET CENTER LLC",
    "MILLENNIUM ENGINEERING AND INTEGRATION SERVICES, LLC",
    "MINA PETROLEUM FZE",
    "MISSION FOR ESSENTIAL DRUGS AND SUPPLIES",
    "MITCHELL VANTAGE SYSTEMS LLC",
    "MNEMONICS INC",
    "MOMENTUS SPACE LLC",
    "MORAN TOWING CORPORATION",
    "MORPHOSIS ARCHITECTS",
    "MORTGAGE CONTRACTING SERVICES LLC",
    "MTC TECHNOLOGIES, INC",
    "MULTIBEAM CORPORATION",
    "MVM, INC.",
    "MYREVELATIONS LLC",
    "N.S.P. VENTURES CORP.",
    "NAKUPUNA SOLUTIONS, LLC",
    "NALGE NUNC INTERNATIONAL CORPORATION",
    "NATIONAL ACADEMY OF SCIENCES",
    "NATIONAL CREDIT SERVICES, INC.",
    "NATIONAL CRIME PREVENTION COUNCIL INC.",
    "NATIONAL NUCLEAR CENTER OF KAZAKHSTAN",
    "NATIONAL PLASTERING INDUSTRYS JOINT APPRENTICESHIP TRUST FUND",
    "NATIONAL RECOVERIES INC",
    "NATIONAL STRATEGIC PROTECTIVE SERVICES, LLC",
    "NAVIENT CORPORATION",
    "NAVQSYS, LLC",
    "NBAF DESIGN PARTNERSHIP",
    "NCS PEARSON INC",
    "NELSON ENVIRONMENTAL REMEDIATION USA LTD",
    "NEPTUNE AVIATION SERVICES, INC.",
    "NETCENTRIC TECHNOLOGY, LLC",
    "NEW HORIZONS AERONAUTICS, LLC",
    "NICE SYSTEMS INCORPORATED",
    "NISA INVESTMENT ADVISORS LLC",
    "NMI ALASKA, INC.",
    "NOREAS ENVIRONMENTAL SERVICES LLC",
    "NORTHSTAR MARITIME DISMANTLEMENT SERVICES, LLC",
    "NOVA SPACE SOLUTIONS, LLC",
    "NOVEOME BIOTHERAPEUTICS INC",
    "NTSI LLC",
    "NTT DATA FEDHEALTH, INC.",
    "NUANCE COMMUNICATIONS, INC.",
    "NUCLEAR FUEL SERVICES INC",
    "NUCLEAR SHIP SUPPORT SERVICES LLC",
    "NUSTAR TERMINALS OPERATIONS PARTNERSHIP L.P.",
    "NV SECURITAS SECURITAS CRITICAL INFRASTRUCTURE SERVICES SOC.",
    "OAKES FARMS FOOD & DISTRIBUTION SERVICES, LLC",
    "OCEAN SHIPS, INC.",
    "OCTO METRIC LLC",
    "OFFSHORE SERVICE VESSELS, L.L.C.",
    "OFORI & ASSOCIATES PC",
    "OKINAWA SOGO SHISETSU KANRI CO., LTD.",
    "OPR LLC",
    "OPTIMOS, LLC",
    "OPTUMHEALTH CARE SOLUTIONS, LLC",
    "ORACLE HEALTH GOVERNMENT SERVICES, INC.",
    "ORBITAL SCIENCES LLC",
    "ORGANIZATIONAL STRATEGIES, LLC",
    "OVERLOOK SYSTEMS TECHNOLOGIES, INC.",
    "PACARCTIC, LLC",
    "PACIFIC COAST FRESH COMPANY",
    "PACIFIC ENERGY SOLUTIONS LLC",
    "PACIFIC INVESTMENT MANAGEMENT COMPANY LLC",
    "PAK QATAR FAMILY TAKAFUL LIMITED",
    "PATHFINDER INTERNATIONAL",
    "PATRIOT CONTRACT SERVICES, LLC",
    "PCX AEROSTRUCTURES, LLC",
    "PHANTOM SPACE CORP",
    "PHILADELPHIA AUTHORITY FOR INDUSTRIAL DEVELOPMENT",
    "PHILIPPINE COASTAL STORAGE & PIPELINE CORPORATION",
    "POWDER RIVER INDUSTRIES, LLC",
    "PRAETORIAN STANDARD, INC.",
    "PREMIERE CREDIT OF NORTH AMERICA, LLC",
    "PRIMUS SOLUTIONS, INC.",
    "PRINTED CIRCUITS CORP.",
    "PROGRESSIVE FINANCIAL SERVICES, INC.",
    "PUGET SOUND COMMERCE CENTER, INC.",
    "QUALITY INSIGHTS, INC",
    "QWK INTEGRATED SOLUTIONS, LLC",
    "RANGE GENERATION NEXT LLC",
    "RECONCRAFT, LLC",
    "REDSTONE SOLAR I, LLC",
    "REED TECHNOLOGY AND INFORMATION SERVICES LLC",
    "RELATIVITY SPACE, INC.",
    "RELIANCE TEST & TECHNOLOGY, LLC",
    "RELIANT CAPITAL SOLUTIONS LLC",
    "REMOTE MEDICINE INC.",
    "RENCO CORPORATION",
    "RENTFROW INCORPORATED",
    "REPKON USA - DEFENSE, LLC",
    "RESCUE ONE TRAINING FOR LIFE INC",
    "RESEARCH DATA AND COMMUNICATION TECHNOLOGIES BENEFIT CORP",
    "RESEARCH PARTNERSHIP TO SECURE ENERGY FOR AMERICA",
    "RESILIENCE ACTION PARTNERS",
    "RHINO HEALTH, INC.",
    "RIGHT TO CARE",
    "RIGHT TO CARE ZAMBIA LIMITED",
    "RMI TITANIUM COMPANY, LLC",
    "ROTHE DEVELOPMENT, INC",
    "S & K SECURITY GROUP LLC",
    "S E I INVESTMENTS MANAGEMENT CORPORATION",
    "S P KOROLEV ROCKET AND SPACE PUBLIC CORPORATION ENERGIA",
    "S1S ALPHA LLC",
    "SALLYPORT GLOBAL SERVICES LTD",
    "SARAWORKS, LLC",
    "SAVANTAGE FINANCIAL SERVICES, INC.",
    "SCALE AI, INC.",
    "SCANDINAVIAN BIOPHARMA HOLDING AB",
    "SCIOLEX CORPORATION",
    "SEAVIN",
    "SECURIGENCE LLC",
    "SEDGWICK GOVERNMENT SOLUTIONS, LLC.",
    "SEDNA DIGITAL SOLUTIONS, LLC",
    "SEKON ENTERPRISE, LLC",
    "SELENE FINANCE LP",
    "SENECA STRATEGIC PARTNERS, LLC",
    "SENECA TECHNOLOGIES, LLC",
    "SENTRY VIEW SYSTEMS, INC.",
    "SERVICESOURCE INC",
    "SHAW ENVIRONMENTAL, INC.",
    "SHIPCOM WIRELESS INC",
    "SHOWA BEST GLOVE, INC.",
    "SIERRA PACIFIC AIRLINES, INC",
    "SIERRA TAHOE ENVIRONMENTAL MANAGEMENT, LLC",
    "SIERRA TECHNICAL SERVICES, INC.",
    "SIGMATECH, INC.",
    "SIGNATURE CHOICE II, LLC",
    "SIGNATURE PERFORMANCE, INC.",
    "SILLER HELICOPTERS, LLC",
    "SMITHGROUP, INC.",
    "SMITHS DETECTION, INC",
    "SOC LLC",
    "SODEXO MANAGEMENT INC.",
    "SOLACE CORPORATION",
    "SOLKOA INC",
    "SOMOSGOV INC",
    "SORINEX EXERCISE EQUIPMENT INC",
    "SOUTH CAROLINA COMMISSION FOR BLIND",
    "SOUTHERN TERRITORIAL HEADQUARTERS OF THE SALVATION ARMY, THE",
    "SOUTHLAND INDUSTRIES",
    "SOUTHWEST RANGE SERVICES LLC",
    "SPACE COAST LAUNCH SERVICES LLC",
    "SPACE NETWORK SOLUTIONS LLC",
    "SPALDING CONSULTING, INC.",
    "SPECIAL OPERATIONS TECHNOLOGY, INC.",
    "SPECTRUM FEDERAL SOLUTIONS LLC",
    "SPECTRUM SECURITY SERVICES, INC.",
    "SRA INTERNATIONAL, INC.",
    "SSC SPACE US INC",
    "STAR ENERGY RESOURCES LTD",
    "STATE STREET CORPORATION",
    "STRATEGIC ALLIANCE SOLUTIONS LLC",
    "STRATEGIC MANAGEMENT SOLUTIONS, LLC",
    "STRATEGIC STORAGE PARTNERS, LLC.",
    "STRATEGIC TEST SOLUTIONS, LLC",
    "STUDIO NOVA LP",
    "SUH'DUTSING CONTRACTING SERVICES",
    "SUPERIOR MARINE WAYS, INC",
    "SWIFT & STALEY INC.",
    "SWIFTSHIPS SHIPBUILDERS, L.L.C.",
    "SYMPLICITY CORPORATION",
    "SYNTERAS LLC",
    "SYSTEM DYNAMICS INTERNATIONAL INCORPORATED",
    "SYSTRAN SOFTWARE, INC.",
    "SYTEX, INC.",
    "TALON EXPEDITIONARY SERVICES, LLC",
    "TAPESTRY TECHNOLOGIES, INC.",
    "TDX INTERNATIONAL, LLC",
    "TECHFLOW, INC.",
    "TECHNIKO LLC",
    "TECHNOLOGY SERVICE CORP",
    "TECHTRANS INTERNATIONAL INC",
    "TELE-CONSULTANTS, INC.",
    "TELEDYNE FLIR DEFENSE, INC.",
    "TELEDYNE, INC",
    "TENAX AERIAL FIRE SUPPORT, LLC",
    "TEPCO ENERGY PARTNER, INCORPORATED",
    "TEXAS INSTRUMENTS INCORPORATED",
    "THE CADMUS GROUP LLC",
    "THE COLLABORATIVE INC",
    "THE PROVIDENCIA GROUP LLC",
    "THE TRAVIS ASSOCIATION FOR THE BLIND",
    "THOMAS SCIENTIFIC, LLC",
    "TITANIUM COBRA SOLUTIONS, LLC",
    "TIYA SUPPORT SERVICES LLC",
    "TOMER - A GOVERNMENT-OWNED COMPANY LTD",
    "TRADE CENTER MANAGEMENT ASSOCIATES L.L.C.",
    "TRANSWORLD SYSTEMS INC.",
    "TRAX INTERNATIONAL, LLC",
    "TRIDEA WORKS, LLC",
    "TRIDENT MILITARY SYSTEMS LLC",
    "TRIDENT VANTAGE SYSTEMS, LLC",
    "TRINITY PROTECTION SERVICES, INC.",
    "TRU SIMULATION + TRAINING INC.",
    "U.S. COMMITTEE FOR REFUGEES AND IMMIGRANTS, INC.",
    "UBC NATIONAL JOB CORPS TRAINING FUND INC",
    "ULTRA ELECTRONICS OCEAN SYSTEMS INC.",
    "ULTRA ELECTRONICS TCS INC",
    "UNITED SAFETY TECHNOLOGY INC",
    "UNITED STATES ENRICHMENT CORPORATION",
    "UNITED TECHNOLOGIES CORPORATION",
    "UNIVERSAL SERVICE ADMINISTRATIVE COMPANY",
    "UNWIN CO",
    "US MEDICAL GLOVE COMPANY L.L.C.",
    "US&S - E2 I, LLC",
    "USA JET AIRLINES INC",
    "USFALCON INC",
    "USS CHARTERING LLC",
    "VALDEZ INTERNATIONAL CORPORATION",
    "VALUE RECOVERY HOLDING, LIMITED LIABILITY COMPANY",
    "VAN RU CREDIT CORPORATION",
    "VANE LINE BUNKERING, LLC",
    "VANTOR SERVICES INC.",
    "VECNA TECHNOLOGIES, INC",
    "VENTURI, LLC",
    "VETERANS ENTERPRISE TECHNOLOGY SOLUTIONS, INC.",
    "VETERANS TECHNOLOGY, L.L.C.",
    "VETTECH LLC",
    "VICINITY ENERGY BALTIMORE COOLING LLP",
    "VIGOR SHIPYARDS, LLC",
    "VIRGINIA COMMERCIAL SPACE FLIGHT AUTHORITY",
    "VISION POINT SYSTEMS, INC.",
    "VS2 LLC",
    "WALSH HEALTHCARE LOGISTICS",
    "WATERTIGHT SOLUTIONS LLC",
    "WESTINGHOUSE GOVERNMENT SERVICES LLC",
    "WILLIAM LOWE & SONS CORP",
    "WINDHAM PROFESSIONALS INC",
    "WORLD EDUCATION, INC.",
    "WORLD TECHNICAL SERVICES INC",
    "WORLDWIDE LANGUAGE RESOURCES, LLC",
    "XPECT SOLUTIONS LLC",
    "XPERT'S LLC",
    "XTERA INC",
    "YORK SPACE SYSTEMS LLC",
    "Z SYSTEMS CORPORATION",
    "ZEIDERS ENTERPRISES, INC.",

    # Added 2026-05-27 from top-300-by-dollar Opus-agent batch investigation.
    # Each entry has a cited public source (10-K, press release, Wikipedia,
    # SEC filing, or government program documentation) verified by the agent.

    # Subsidiaries of major federal primes / public companies
    "EAGLE GROUP INTERNATIONAL LLC",
    "BLACKHORSE, A PARSONS LLC",
    "SPACE GROUND SYSTEM SOLUTIONS, INC.",
    "WYLE INFORMATION SYSTEMS, LLC",
    "URS FEDERAL SERVICES INTERNATIONAL, INC",
    "SONOMA PHOTONICS, INC",
    "D3 TECHNOLOGIES INC.",
    "STG LLC",
    "GULFSTREAM AEROSPACE CORPORATION",
    "DRS SYSTEMS, INC",
    "AGUSTAWESTLAND PHILADELPHIA CORPORATION",
    "AMERICAN RHEINMETALL VEHICLES, LLC",
    "LOC PERFORMANCE PRODUCTS LLC",
    "CUBIC DIGITAL INTELLIGENCE INC",
    "INMARSAT GOVERNMENT SERVICES INC.",
    "BEECHCRAFT CORPORATION",
    "OLIN WINCHESTER LLC",
    "EMERGENT BIODEFENSE OPERATIONS LANSING LLC",
    "AIR TRANSPORT INTERNATIONAL INC",
    "PROCTOR FINANCIAL INC.",
    "WESTERN ASSET MANAGEMENT CO, LLC",
    "WESTERN DIGITAL TECHNOLOGIES, INC.",
    "ALIGHT SOLUTIONS LLC",
    "BUCK CONSULTANTS, INC",
    "BLACK KNIGHT INFOSERV, LLC",
    "COTIVITI GOV SERVICES, LLC",
    "USEC SERVICE CORPORATION",
    "INMAR RX SOLUTIONS INC",
    "ACI PAYMENTS, INC.",
    "SOFTRAMS LLC",
    "ASTRION GROUP, LLC",
    "AMERICAN K-9 DETECTION SERVICES, LLC",
    "EMCORE LLC",
    "TRIUMPH AEROSTRUCTURES, LLC",
    "METRO MACHINE CORP",
    "STANDARD REGISTER, INC.",
    "CENTERLINE LOGISTICS CORP",
    "HP ENTERPRISE SERVICES, LLC",
    "TRANSOCEANIC CABLE SHIP COMPANY LLC",

    # ANCSA Alaska Native Corporation / tribal subsidiaries
    "NORTH WIND TEST LLC",
    "BOWHEAD MARINE SUPPORT SERVICES LLC",
    "SUNITNA RIVER LLC",
    "GLOBAL PRECISION SYSTEMS, LLC",
    "GLOBAL SUPPORT SERVICES LLC",
    "KATMAI NORTH AMERICA, LLC",
    "ROLLING BAY LIMITED LIABILITY COMPANY",
    "DATA NETWORKS, INC.",
    "ECHOTA TECHNOLOGIES CORPORATION",
    "KIRA TRAINING SERVICES LLC",

    # Joint ventures with named partners
    "HYGEIA SOLUTIONS PARTNERS LLC",
    "NOVILO TECHNOLOGY SOLUTIONS, LLC",
    "STANTEC/AECOM LLC",
    "TANTUS/ONPOINT ACCELERATED TRANSFORMATION SOLUTIONS, LLC",
    "DERIVATIVE LLC",
    "APOGEE-SAIC CAPABILITIES INTEGRATOR, LLC",
    "3A, LLC",
    "CIVILEON RESEARCH AND TECHNOLOGY, LLC",

    # Fortune 500 / major commercial brands with new federal UEI
    "CARNIVAL CORPORATION",
    "U.S. BANCORP",
    "U S FOODS INC",
    "NOVAVAX INC",
    "MORGAN, LEWIS & BOCKIUS LLP",
    "BRITISH TELECOMMUNICATIONS PUBLIC LIMITED COMPANY",
    "TRAFIGURA TRADING LLC",
    "SUNOCO PARTNERS MARKETING & TERMINALS L.P.",
    "BASILEA PHARMACEUTICA INTERNATIONAL LTD, ALLSCHWIL",
    "ALLISON TRANSMISSION INC",
    "QATARENERGY",
    "ONE GAS INC",

    # Established federal nonprofits, R&D institutes, FFRDC-class
    "AMERICAN TYPE CULTURE COLLECTION",
    "ALBERT B. SABIN VACCINE INSTITUTE, INC. (THE)",
    "MALARIA CONSORTIUM",
    "NRECA INTERNATIONAL",
    "PATH",
    "JHPIEGO CORP",
    "MANAGEMENT SCIENCES FOR HEALTH, INC.",
    "INTRAHEALTH INTERNATIONAL, INC.",
    "CNFA",
    "BLUMONT GLOBAL DEVELOPMENT, INC.",
    "DEXIS PROFESSIONAL SERVICES, LLC",
    "PHLOW CORP.",
    "MRIGLOBAL",
    "PUBLIC HEALTH INSTITUTE",
    "EDISON WELDING INSTITUTE INC",
    "WESTED",
    "AMERICAN SOCIETY FOR ENGINEERING EDUCATION",
    "CENTER FOR CHILDREN AND FAMILY FUTURES, INC.",
    "THE YOUNG CENTER FOR IMMIGRANT CHILDRENS RIGHTS",
    "SENECA FAMILY OF AGENCIES",
    "SOUTHWEST EDUCATIONAL DEVELOPMENT CORPORATION",
    "NATIONAL RURAL SUPPORT PROGRAMME",
    "UNITED NETWORK FOR ORGAN SHARING",

    # Established federal primes / mid-tier defense+IT firms
    "PLOWSHARE GROUP, INC",
    "WESTON SOLUTIONS INC",
    "CDM FED. PROGRAMS CORP.",
    "PROFESSIONAL PROJECT SERVICES, INC.",
    "TECHNICAL RESOURCES INTERNATIONAL, INC.",
    "GEMINI INDUSTRIES INC.",
    "SOUTHEASTERN COMPUTER CONSULTANTS, INC.",
    "ENVISTACOM, L.L.C",
    "INSIGHT SYSTEMS CORPORATION",
    "HERRICK TECHNOLOGY LABORATORIES INC",
    "SYNECTICS FOR MANAGEMENT DECISIONS, INC.",
    "PALOMAR DISPLAY PRODUCTS, INC.",
    "COSTQUEST ASSOCIATES LLC",
    "NOVAWURKS, INC",
    "BOLTON PARTNERS, INC.",
    "MILLIMAN, INC.",
    "THERADEX SYSTEMS, INC.",
    "EMENTUM INC",
    "AD HOC LLC",
    "O'GARA TRAINING AND SERVICES, LLC.",
    "CONTI FEDERAL SERVICES, LLC",
    "CI2 AVIATION, INC.",
    "PROFESSIONAL BUREAU OF COLLECTIONS OF MARYLAND, INC.",
    "SOLDIERPOINT DIGITAL HEALTH, LLC",
    "BELL AND HOWELL, LLC",
    "INTERNATIONAL MEDICAL GROUP INC",
    "TACTICAL AIR SUPPORT, INC.",
    "WORLDPAY US, LLC",
    "MUTUAL TELECOM SERVICES INC.",
    "VANGUARD INSPECTION SERVICES",
    "AGMA SECURITY SERVICE INC",
    "HOMETELOS, L.P.",
    "GARDAWORLD FEDERAL",
    "GUAM HOTEL AND RESTAURANT ASS OCIATION",
    "PARADISE CRUISE LINE OPERATOR LTD. INC.",
    "SEAWARD SERVICES, INC.",
    "BRODOGRADILISTE VIKTOR LENAC D.D.",
    "BIRD AEROSYSTEMS LTD",
    "ALSALAM AEROSPACE INDUSTRIES COMPANY",
    "THALES AVS FRANCE SAS",
    "HUMAN LEARNING SYSTEMS LLC",
    "OHIO KEPRO, LLC",
    "POINT BLANK ENTERPRISES, INC",
    "POINT BLANK PROTECTIVE APPAREL & UNIFORMS LLC",
    "CHARLES TOMBRAS ADVERTISING, INC.",
    "CAROLINA GROWLER INC",
    "STANDARD TEXTILE CO INC",
    "MEDLINE INDUSTRIES, LP",
    "THINKWELL GROUP, LLC",
    "IT CORPORATION",
    "THE IQ BUSINESS GROUP, INC.",

    # State/local government, AbilityOne, public universities
    "MISSOURI HIGHER EDUCATION LOAN AUTHORITY",
    "MISSOURI DEPARTMENT OF SOCIAL SERVICES",
    "MISSISSIPPI DEPARTMENT OF REHABILITATION SERVICE",
    "FREDERICK COUNTY MARYLAND",
    "BLIND AND VISION IMPAIRED, VIRGINIA DEPARTMENT FOR THE",
    "KENTUCKY LOGISTICS CENTER",
    "LETTERKENNY INDUSTRIAL DEVELOPMENT A",
    "JACKSON HOLE AIRPORT BOARD",
    "WELLTON-MOHAWK CO-OP",
    "EAST MISSISSIPPI ELECTRIC POWER ASSOCIATION",
    "HIGH WEST ENERGY, INC.",
    "ICAHN SCHOOL OF MEDICINE AT MOUNT SINAI",
    "THE UNIVERISTY OF TEXAS M.D. ANDERSON CANCER CENTER",
    "PECKHAM VOCATIONAL INDUSTRIES, INC.",

    # Added 2026-05-28 from dashboard review with operator.
    "NATIONAL INDUSTRIES FOR THE BLIND",
    "LEONARDO US AIRCRAFT, LLC",
    "EMORY UNIVERSITY",
    "CONSORTIUM OF UNIVERSITIES OF THE WASHINGTON METROPOLITAN AREA",

    # Second 2026-05-28 dashboard pass.
    "JVYS",
    "BELLESE TECHNOLOGIES, LLC",
    "RCF INFORMATION SYSTEMS, INC.",

    # Third 2026-05-28 dashboard pass (40 entities verified via Opus agents +
    # training data; SAGE SYSTEMS TECHNOLOGIES, LLC deliberately excluded).
    "AIRCRAFT TRANSPORT SERVICES, INC.",
    "AL RAWABET COMMERCIAL SERVICES & CONTRACTING CO. W.L.L.",
    "APR ENERGY USA, LLC",
    "CASH-WA DISTRIBUTING CO. OF KEARNEY, INC.",
    "CHEROKEE INSIGHTS LLC",
    "CHICKASAW ADVISORY SERVICES, LLC",
    "CONCORDANCE HEALTHCARE SOLUTIONS LLC",
    "CONFEDERATED SALISH & KOOTENAI TRIBES",
    "CONSIGLIO NAZIONALE DELLE RICERCHE - ISTITUTO DI RICERCA GENETICA E BIOMEDICA",
    "DAMASCUS HOUSE COMMUNITY DEVELOPMENT CORP",
    "DISMAS HOUSE OF ST. LOUIS",
    "FORT SMITH REGIONAL AIRPORT",
    "GLASSHOUSE SYSTEMS INC",
    "GOLD COAST MEDICAL SUPPLY LP",
    "GULFSTREAM GOODWILL INDUSTRIES INC",
    "HEMOGLOBIN OXYGEN THERAPEUTICS LLC",
    "HUI HULIAU TECHNOLOGY SERVICES LLC",
    "INTERNATIONAL SOS GOVERNMENT MEDICAL SERVICES, INC.",
    "INTERNATIONAL UNION OF OPERATING ENGINEERS NATIONAL TRAINING FUND",
    "KRATOS S1, INC.",
    "KRATOS SPACE & MISSILE DEFENSE SYSTEMS, INC.",
    "LOS ANGELES CAPITAL MANAGEMENT LLC",
    "MILITARY PRODUCE GROUP LLC",
    "NEW MEXICO INSTITUTE OF MINING AND TECHNOLOGY",
    "NORTHEAST OHIO REGIONAL SEWER DISTRICT",
    "PETROTECHNICAL RESOURCES OF ALASKA, LLC",
    "PUBLIC SERVICE COMPANY OF OKLAHOMA",
    "PUGET SOUND & PACIFIC RAILROAD",
    "RAPE, ABUSE AND INCEST NATIONAL NETWORK RAINN",
    "RESPUBLIKANSKOE GOSUDARSTVENNOE PREDPRIYATIE NA PRAVE KHOZYAISTVENNOGO VEDENIYA NATSIONALNYI YADERNYI TSENTR RESPUBLIKI",
    "ROBIN HILL FARM INC",
    "SCHRODER INVESTMENT MANAGEMENT NORTH AMERICA INC",
    "SMITHSONIAN INSTITUTION",
    "TECNICA Y PROYECTOS TYPSA FOR ENGINEERING SERVICES",
    "THE SOLID WASTE DISPOSAL AUTHORITY OF THE CITY OF HUNTSVILLE",
    "TRIHAWK, LLC",
    "TRUST FOR DEMOCRATIC EDUCATION & ACCOUNTABILITY",
    "VINCENT FARMS INC.",
    "WALSH CONSTRUCTION COMPANY II, LLC",
    "WASTE CONTROL SPECIALISTS LLC",
    "OPPORTUNITY VILLAGE ASSOCIATION FOR RETARDED CITIZENS",
    "CHAUTAUQUA COUNTY CHAPTER OF NYSARC, INC",
    "NEW YORK UNIVERSITY",
    "NEW JERSEY INSTITUTE OF TECHNOLOGY",
    "THE MENTAL HEALTH ASSOCIATION OF NEW YORK CITY, INC.",
    "SAVE THE CHILDREN FEDERATION, INC.",
    "AL RAHA GROUP FOR TECHNICAL SERVICES (RGTS)",
    "GOODWILL INDUSTRIES OF SOUTHERN CALIFORNIA",
    "LICKING-KNOX GOODWILL INDUSTRIES, INC.",
    "MD HELICOPTERS INC",
    "ALABAMA DEPARTMENT OF REHABILITATION SERVICES",
    "GEORGIA VOCATIONAL REHABILITATION AGENCY",
    "PROLOGIC, INC.",
    "PUBLIC HEALTH, CALIFORNIA DEPARTMENT OF",

    # Fourth 2026-05-28 dashboard pass.
    "UNITED SOLUTIONS AND SERVICES LLC",
    "DEPARTMENT OF PUBLIC HEALTH CONNECTICUT",
    "OSU-UNIVERSITY MULTISPECTRAL LABORATORIES LLC",
    "AEROSPACE ENGINEERING SPECTRUM LTD",
    "ACELRX PHARMACEUTICALS, INC.",
    "DALE ROGERS TRAINING CENTER, INC.",
    "DERCO AEROSPACE, INC.",

    # 2026-05-30: ERI Services, Inc. (UEI NJ8DHCK185A5) is the legacy legal
    # entity for what now operates as NORESCO, LLC under Carrier Global Corp.
    # NORESCO maintains two fully active SAM registrations under different
    # UEIs (YJYYEDBU9Y69, N76WVJGHHQH4), both renewed Aug 2025. The flagged
    # activity is mods to a 2008 NAVFAC ESPC IDV (PIID N4740800D8117 order
    # 0002) with statutory term through 2028-11-30. Root cause is a missing
    # novation per FAR 42.1204, not contractor misconduct. Flag re-fires on
    # every annual ESPC mod through 2028 and would dominate the dashboard
    # for ~3 more years on a single vehicle. Compliance-hygiene pattern,
    # not fraud signal.
    "ERI SERVICES, INC.",
})


def is_curated_safe_recipient(ctx) -> bool:
    name = (ctx.get("award", {}).get("recipient_name") or "").upper().strip()
    if not name:
        return False
    return name in CURATED_SAFE_RECIPIENT_NAMES


# --- Address normalization ---
# Standardize address strings to tolerate common variations: "Suite" vs
# "STE", "Street" vs "ST", trailing punctuation, double spaces, case.
# Not wired into the current three-flag framework but kept as a utility
# for any future address-comparison rule.

_ADDR_TOKENS = [
    (re.compile(r'\bSUITE\b', re.IGNORECASE), 'STE'),
    (re.compile(r'\bSTREET\b', re.IGNORECASE), 'ST'),
    (re.compile(r'\bAVENUE\b', re.IGNORECASE), 'AVE'),
    (re.compile(r'\bDRIVE\b', re.IGNORECASE), 'DR'),
    (re.compile(r'\bBOULEVARD\b', re.IGNORECASE), 'BLVD'),
    (re.compile(r'\bROAD\b', re.IGNORECASE), 'RD'),
    (re.compile(r'\bLANE\b', re.IGNORECASE), 'LN'),
    (re.compile(r'\bCOURT\b', re.IGNORECASE), 'CT'),
    (re.compile(r'\bHIGHWAY\b', re.IGNORECASE), 'HWY'),
    (re.compile(r'\bPARKWAY\b', re.IGNORECASE), 'PKWY'),
    (re.compile(r'\bCIRCLE\b', re.IGNORECASE), 'CIR'),
    (re.compile(r'\bPLACE\b', re.IGNORECASE), 'PL'),
    (re.compile(r'\bBUILDING\b', re.IGNORECASE), 'BLDG'),
    (re.compile(r'\bFLOOR\b', re.IGNORECASE), 'FL'),
    (re.compile(r'\bNORTH\b', re.IGNORECASE), 'N'),
    (re.compile(r'\bSOUTH\b', re.IGNORECASE), 'S'),
    (re.compile(r'\bEAST\b', re.IGNORECASE), 'E'),
    (re.compile(r'\bWEST\b', re.IGNORECASE), 'W'),
    (re.compile(r'[\.\,]'), ''),
    (re.compile(r'\s+'), ' '),
]


def normalize_address(addr: str) -> str:
    if not addr:
        return addr
    out = addr.upper().strip()
    for pattern, replacement in _ADDR_TOKENS:
        out = pattern.sub(replacement, out)
    return out.strip()


def normalize_award_address(ctx):
    """Context normalizer: replace recipient_address with normalized form."""
    award = ctx.get("award")
    if not award:
        return ctx
    addr = award.get("recipient_address")
    if addr:
        award["recipient_address"] = normalize_address(addr)
    return ctx


# --- Established-entity gate ---
# Populated once at rescore time by cw_rescore_fast.py:load_lookups().
# Maps UEI -> {'award_count': int, 'total_oblig': float}. The
# is_established_entity check uses this lookup so we don't need to query
# the DB inside the per-award flag loop.
_UEI_LIFETIME_STATS = {}


def is_established_entity(ctx) -> bool:
    """An entity is 'established' when their cumulative federal history in
    the 9-year archive already shows substantial volume. Used to strip
    structural-pattern flags that would otherwise fire on entities at scale."""
    uei = ctx.get("award", {}).get("recipient_uei")
    if not uei:
        return False
    stats = _UEI_LIFETIME_STATS.get(uei)
    if not stats:
        return False
    return (stats.get("total_oblig", 0) >= ESTABLISHED_ENTITY_MIN_OBLIG
            or stats.get("award_count", 0) >= ESTABLISHED_ENTITY_MIN_AWARDS)


# --- DOE national-lab operators (M&O contractors) ---
# DOE periodically re-competes the management contracts for each national lab.
# Each time, a fresh LLC consortium is spun up to operate the lab; that LLC has
# no federal contract history under its new UEI but receives a multi-billion
# dollar M&O contract on day one. Structurally normal for the lab system, not
# anomalous. Common naming conventions plus specific known operator LLCs.

NATIONAL_LAB_OPERATOR_PATTERNS = (
    # Operator-name conventions
    "NATIONAL LABORATORY", "NATIONAL SECURITY",
    "NUCLEAR SOLUTIONS", "NUCLEAR PARTNERSHIP", "NUCLEAR PRODUCTION",
    "WASTE PARTNERSHIP", "WASTE SOLUTIONS",
    "SCIENCE ASSOCIATES", "RESEARCH ALLIANCE",
    "FORWARD DISCOVERY GROUP", "DISCOVERY GROUP",
    "MISSION SUPPORT ALLIANCE", "MISSION SUPPORT",
    "AEROSPACE TESTING ALLIANCE", "AEROSPACE SOLUTIONS",
    "UT-BATTELLE", "BWXT", "UCOR LLC", "CB&I AREVA",
    "WASHINGTON CLOSURE",  # Hanford site cleanup operator
    "WASHINGTON TRU SOLUTIONS",
    # DOE physical site names: any LLC carrying a DOE facility name in its
    # corporate identity is structurally a site M&O contractor.
    "OAK RIDGE", "SAVANNAH RIVER", "HANFORD",
    "PANTEX", "WASTE ISOLATION", "IDAHO CLEANUP",
    "FERMI", "LOS ALAMOS", "BROOKHAVEN", "ARGONNE",
    "PADUCAH", "PORTSMOUTH",  # Kentucky and Ohio gaseous diffusion plant sites
)


def is_national_lab_operator(ctx) -> bool:
    name = (ctx.get("award", {}).get("recipient_name") or "").upper()
    if not name:
        return False
    return any(p in name for p in NATIONAL_LAB_OPERATOR_PATTERNS)


# --- Government-owned facility operator (PSC class M) ---

def is_government_facility_operator(ctx) -> bool:
    """Match awards with PSC code in class M (Operation of Government-Owned
    Facilities) above a dollar threshold. PSC-M is the FPDS classification
    for M&O contracts: by definition the recipient is operating a federal
    facility under a fresh LLC, not a new entrant. The dollar gate prevents
    small custodial/operations contracts from being swept in."""
    award = ctx.get("award", {})
    psc = (award.get("psc_code") or "").strip().upper()
    if not psc.startswith("M"):
        return False
    try:
        ob = float(award.get("current_total_value_of_award") or 0)
    except (TypeError, ValueError):
        return False
    return ob >= 100_000_000


# --- Excluded awarding agencies (operator-configured at ingestion) ---

def is_excluded_agency(ctx) -> bool:
    """Strip flags when the awarding agency is on the operator's exclusion
    list (CONTRACTWATCH_EXCLUDED_AGENCIES env var). Mirrors the scanner's
    ingestion-time skip so historical records that pre-date the env var
    don't sit on the dashboard."""
    if not EXCLUDED_AWARDING_AGENCIES:
        return False
    agency = (ctx.get("award", {}).get("awarding_agency") or "").strip()
    return agency in EXCLUDED_AWARDING_AGENCIES


# --- Heavy construction NAICS ---
# First-time federal contracts in heavy-construction NAICS at the $50M+
# threshold are nearly always construction JVs or established commercial
# builders. The F01/F02/F03 patterns rarely produce signal in these NAICS at this scale.
_HEAVY_CONSTRUCTION_PREFIXES = ("236", "237")


def is_heavy_construction(ctx) -> bool:
    naics = (ctx.get("award", {}).get("naics_code") or "").strip()
    return any(naics.startswith(p) for p in _HEAVY_CONSTRUCTION_PREFIXES)


# --- DOE environmental remediation (NAICS 562910 + DOE) ---
# Site-bound cleanup contractors at DOE legacy sites. Each new contract round
# spins up a fresh consortium LLC by structural design.

def is_doe_remediation(ctx) -> bool:
    award = ctx.get("award", {})
    naics = (award.get("naics_code") or "").strip()
    agency = (award.get("awarding_agency") or "").strip()
    return naics == "562910" and agency == "Department of Energy"


# --- Obligation sanity check ---

def is_obligation_out_of_band(ctx) -> bool:
    """Strip any flag on an award whose obligation is outside the defensive
    cap range. Catches historical data poisoning (e.g. IDIQ ceilings
    misreported as obligation values) that pre-dates the ingestion-time cap
    in clients.normalize_award."""
    from engine.config import MIN_OBLIGATION
    OBLIGATION_CEIL = 10_000_000_000
    ob = ctx.get("award", {}).get("current_total_value_of_award") or 0
    try:
        ob = float(ob)
    except (TypeError, ValueError):
        return True
    return ob < MIN_OBLIGATION or ob > OBLIGATION_CEIL


def is_obligation_placeholder_ceiling(ctx) -> bool:
    """Strip awards whose obligation falls in the placeholder-$1B band.

    USASpending exposes FPDS fields like base_exercised_options and
    current_total_value_of_award that contracting officers sometimes
    populate with a junk no-ceiling marker around $1B when no real
    ceiling exists. A real Inmagic DB/Text software renewal does not
    obligate $1,000,031,100; the IRS office put a placeholder there.
    Bulk-loader picked it up. This rule strips that one specific
    artifact band so a routine $14,800 software renewal doesn't
    masquerade as a $1B anomaly. Narrow band by design (real
    obligations within $999M-$1.001B are rare and would also be
    suspect)."""
    ob = ctx.get("award", {}).get("current_total_value_of_award") or 0
    try:
        ob = float(ob)
    except (TypeError, ValueError):
        return False
    return 999_000_000 <= ob <= 1_000_100_000


# --- The registry ---

STRUCTURAL_RULES: List[StructuralRule] = [
    StructuralRule(
        id="major_prime_structural",
        description="Major federal primes and their subsidiaries",
        rationale=(
            "Major federal primes (Lockheed, Boeing, RTX, General Dynamics, "
            "Northrop, BAE, L3Harris, Leidos, Booz Allen, SAIC, CACI, KBR, "
            "Amentum, Peraton, Jacobs, Bechtel, Battelle, MITRE, FFRDCs, "
            "DOE national lab operators, federal IT resellers, etc.) have "
            "established public corporate structures. They routinely spin up "
            "new LLC subsidiaries with fresh SAM entity-start-dates for "
            "specific contract vehicles. The new subsidiary will have no prior "
            "federal contracts under its UEI even though the parent has decades "
            "of history. Stripped by structural filter: F01 (no-history "
            "sole-source above $10M), F02 (no-history one-offer above $25M), "
            "F03 (no-history first contract above $25M). All three flags would "
            "fire mechanically on prime subsidiaries without indicating any anomaly."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_major_prime_subsidiary,
        added_date="2026-05-23",
    ),
    StructuralRule(
        id="structural_pre_existing_relationship",
        description="Major commercial brands, US utilities, government recipients, foreign government entities, healthcare providers",
        rationale=(
            "Five categories of recipients are structurally not new to federal "
            "business: "
            "(1) major commercial brands (Bank of America, AWS, Eli Lilly, GM, "
            "McKesson, PwC, etc.) where prior history exists under different "
            "subsidiary UEIs; "
            "(2) US utilities (SDG&E, FPL, American Water, etc.), local "
            "monopolies where competition does not exist in their territory; "
            "(3) state/local government and public universities receiving "
            "federal grant pass-through; "
            "(4) foreign government and foreign-owned entities on FMS or "
            "international arrangements; "
            "(5) hospitals, health systems, and major insurance carriers "
            "operating as federal healthcare benefit administrators (TRICARE, "
            "FEHB) under rebranded subsidiary UEIs. "
            "All three flags (F01 sole-source, F02 one-offer, F03 first-large-"
            "award) would fire mechanically on these structures without "
            "indicating an anomaly."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_structural_pre_existing,
        added_date="2026-05-24",
    ),
    StructuralRule(
        id="bridge_contract_extension",
        description="Bridge contracts (sole-source extensions filling the gap to a delayed follow-on award)",
        rationale=(
            "A bridge contract is a short-term sole-source extension awarded "
            "to the incumbent contractor to fill the gap between an expiring "
            "contract and a delayed follow-on procurement. The recipient "
            "already had federal work under the prior contract; the bridge "
            "is often issued under a fresh task-order UEI or contract vehicle, "
            "which mechanically trips F01 (no-history sole-source) and F03 "
            "(no-history first contract). Description patterns include "
            "'BRIDGE CONTRACT', 'BRIDGE PERIOD', 'BRIDGE EXTENSION', "
            "'BRIDGE STAFFING', 'MONTH BRIDGE', and 'BRIDGE OPTION' (SBIR "
            "Phase II Bridge). Distinguished from literal bridge "
            "construction (BRIDGE REPAIR, BRIDGE CRANE, BRIDGE BEARING) "
            "by requiring those specific multi-word phrases."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_bridge_contract_extension,
        added_date="2026-05-28",
    ),
    StructuralRule(
        id="joint_venture_structural",
        description="Joint ventures (SBA 8(a) Mentor-Protégé, SDVOSB, HUBZone, construction primes)",
        rationale=(
            "A Joint Venture (recipient name containing 'JV' or 'JOINT VENTURE') "
            "is by program design a newly-formed entity standing up for a "
            "specific procurement. SBA 8(a) Mentor-Protégé JVs, SDVOSB JVs, "
            "HUBZone JVs, and large prime construction JVs all share the same "
            "structural properties: no prior contract history because the entity "
            "is fresh, brand-new SAM entity-start-date, and sole-source or "
            "one-offer competitive eligibility under set-aside authority. "
            "All three flags would fire mechanically. None is an anomaly."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_joint_venture,
        added_date="2026-05-26",
    ),
    StructuralRule(
        id="anc_tribal_subsidiary",
        description="Alaska Native Corporations and tribal 8(a) subsidiaries",
        rationale=(
            "Alaska Native Corporations (Tyonek, Ahtna, NANA, Doyon, Chenega, "
            "ASRC, Sealaska, Calista, etc.) and federally-recognized tribes "
            "(Cherokee Federal, Eastern Shawnee, Choctaw, Ho-Chunk, etc.) "
            "operate via clusters of LLC subsidiaries that share parent "
            "leadership and are sole-source eligible under SBA 8(a) set-asides. "
            "Each subsidiary has its own UEI with no prior federal history and "
            "a fresh entity-start-date. All three flags would fire mechanically. "
            "All are program-sanctioned."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_anc_or_tribal_subsidiary,
        added_date="2026-05-26",
    ),
    StructuralRule(
        id="government_facility_operator",
        description="PSC-M (Operation of Government-Owned Facilities) above $100M",
        rationale=(
            "Awards classified under PSC class M are by definition operation "
            "of government-owned facilities. The recipient is a freshly-formed "
            "LLC consortium taking over a federal site (DOE labs, DOD test "
            "ranges, NASA centers, VA hospitals operated under M&O). The 'no "
            "prior federal history under this UEI' is structural to how M&O "
            "contracts are awarded. Dollar gate of $100M excludes small "
            "operations-and-maintenance contracts that don't fit this pattern."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_government_facility_operator,
        added_date="2026-05-27",
    ),
    StructuralRule(
        id="national_lab_operator",
        description="DOE national-laboratory M&O operator LLCs",
        rationale=(
            "DOE periodically re-competes the operating contract for each "
            "national lab. A fresh consortium LLC is formed for the new term "
            "(Lawrence Livermore National Security, UT-Battelle, Brookhaven "
            "Science Associates, Fermi Research Alliance, Jefferson Science "
            "Associates, etc.) and immediately receives a multi-billion dollar "
            "M&O award. The LLC has no prior federal history under its UEI but "
            "the parent universities and primes do. Structurally normal for the "
            "lab system."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_national_lab_operator,
        added_date="2026-05-27",
    ),
    StructuralRule(
        id="excluded_agency",
        description="Awarding agency on the operator-configured exclusion list",
        rationale=(
            "Mirrors the scanner's ingestion-time skip (CONTRACTWATCH_EXCLUDED_AGENCIES "
            "env var). Catches historical records that pre-date the env var and are "
            "already in the awards table. Default exclusion list is empty in the "
            "public repo; operators populate it via env var at deploy time."
        ),
        applies_to_flags=["*"],
        match=is_excluded_agency,
        added_date="2026-05-27",
    ),
    StructuralRule(
        id="doe_remediation",
        description="DOE environmental remediation services (NAICS 562910)",
        rationale=(
            "Cleanup contracts at DOE legacy sites (Paducah, Portsmouth, "
            "Oak Ridge, Hanford, Idaho, Savannah River, Nevada Test Site) are "
            "competed periodically and won by site-bound LLC consortia. Each "
            "new round spins up a fresh LLC with no prior federal history "
            "under that UEI. NAICS 562910 + DOE awarding agency catches these "
            "structurally, regardless of which site or which consortium."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_doe_remediation,
        added_date="2026-05-27",
    ),
    StructuralRule(
        id="heavy_construction",
        description="Heavy-construction NAICS (236xxx, 237xxx) above the F03 floor",
        rationale=(
            "First-time federal contracts at $50M+ in heavy-construction NAICS "
            "(commercial building, heavy civil, highway construction) are nearly "
            "always project-specific JVs (TIC-Kiewit-Cianbro, Kokosing-Alberici-"
            "Traylor, Clark-McCarthy, Southwest Valley Constructors) or "
            "established commercial builders entering federal work. The "
            "patterns F01/F02/F03 surface do not typically appear in this "
            "NAICS bucket at this dollar threshold."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_heavy_construction,
        added_date="2026-05-27",
    ),
    StructuralRule(
        id="obligation_out_of_band",
        description="Award obligation outside [$1M, $10B] defensive cap",
        rationale=(
            "Defensive sanity check: any award with current_total_value_of_award below "
            "$1M or above $10B is treated as data poisoning (typically an IDIQ "
            "ceiling misreported as the obligation value, or a USASpending "
            "parsing artifact). The ingestion-time cap in clients.normalize_award "
            "catches new inflows; this rule catches historical records that "
            "pre-date the cap or were ingested before its addition."
        ),
        applies_to_flags=["*"],
        match=is_obligation_out_of_band,
        added_date="2026-05-27",
    ),
    StructuralRule(
        id="obligation_placeholder_ceiling",
        description="Award obligation in the placeholder $1B band ($999M-$1.0001B)",
        rationale=(
            "USASpending exposes FPDS fields (base_exercised_options, "
            "current_total_value_of_award) that contracting officers sometimes "
            "populate with a placeholder near $1B when there is no real ceiling. "
            "Bulk-loader inherits the placeholder. A routine $14,800 Inmagic "
            "DB/Text software renewal then appears as a $1,000,031,100 award "
            "and fires F03 by data-quality artifact alone. This narrow band "
            "strips that pattern; real obligations in this range are rare and "
            "also warrant manual review."
        ),
        applies_to_flags=["*"],
        match=is_obligation_placeholder_ceiling,
        added_date="2026-05-27",
    ),
    StructuralRule(
        id="curated_safe_recipient",
        description="Curated exact-match block of recipient names with documented structural reasons for F03 firing",
        rationale=(
            "Each name in CURATED_SAFE_RECIPIENT_NAMES was reviewed against "
            "public sources and confirmed to have a structural explanation for "
            "appearing on the dashboard (defense-prime subsidiary, named joint "
            "venture, household-name operating company, foreign government / "
            "FMS recipient, ANC or tribal subsidiary, DOE national-lab adjacent "
            "operator, Ed Department private collection agency panel member, "
            "USDA Farmers-to-Families food-box vendor, Job Corps operator, "
            "AbilityOne nonprofit, HUD M&M asset manager, etc.). Names where "
            "the lack of prior federal history is itself the signal (dormant "
            "shell, brand-new entity tied to a controversial award) are "
            "deliberately excluded from this list and remain on the dashboard."
        ),
        applies_to_flags=["F01", "F02", "F03"],
        match=is_curated_safe_recipient,
        added_date="2026-05-27",
    ),
]


PUBLISH_FILTERS: List[PublishFilter] = [
    PublishFilter(
        id="action_date_too_old",
        description="Drop awards with latest action before 2017-10-01 from publishable feed",
        rationale=(
            "Pre-backfill spillover (e.g. a 1978 UC national-lab base contract "
            "with $35B cumulative value) distorts charts and is not currently-"
            "actionable signal. The 2017-10-01 cutoff matches the earliest "
            "fiscal year backfilled (FY18)."
        ),
        match=lambda ctx: (ctx.get("award", {}).get("action_date") or "") < "2017-10-01",
        added_date="2026-05-23",
    ),
]


# --- Application functions ---

def apply_structural_filter(triggered_flags, ctx):
    """Strip flags that match any enabled structural rule.

    Returns (survivors, filter_log). Each entry in filter_log is
    {'flag': <original flag dict>, 'rule_id': <id>}.
    """
    survivors = []
    filter_log = []
    for f in triggered_flags:
        rule_hit = None
        for s in STRUCTURAL_RULES:
            if not s.enabled:
                continue
            if not s.matches_flag(f["code"]):
                continue
            if s.match(ctx):
                rule_hit = s
                break
        if rule_hit:
            filter_log.append({"flag": f, "rule_id": rule_hit.id})
        else:
            survivors.append(f)
    return survivors, filter_log


def should_publish(award_ctx) -> bool:
    """True if no PublishFilter excludes this award."""
    for pf in PUBLISH_FILTERS:
        if pf.enabled and pf.match(award_ctx):
            return False
    return True


# --- Audit / introspection ---

def describe_all():
    """Print a human-readable summary of every active structural rule."""
    def _box(title):
        line = "=" * 72
        return f"\n{line}\n  {title}\n{line}"

    print(_box(f"STRUCTURAL_RULES ({len(STRUCTURAL_RULES)} registered)"))
    for s in STRUCTURAL_RULES:
        flag = "[on] " if s.enabled else "[OFF]"
        print(f"\n  {flag} {s.id}")
        print(f"        applies to: {', '.join(s.applies_to_flags)}")
        print(f"        description: {s.description}")
        print(f"        added: {s.added_date}")
        # Wrap rationale
        words = s.rationale.split()
        line = "        rationale: "
        out = []
        for w in words:
            if len(line) + len(w) > 70:
                out.append(line)
                line = "                   " + w
            else:
                line = (line + " " + w) if not line.endswith(": ") else (line + w)
        out.append(line)
        for L in out:
            print(L)

    print(_box(f"PUBLISH_FILTERS ({len(PUBLISH_FILTERS)} registered)"))
    for pf in PUBLISH_FILTERS:
        flag = "[on] " if pf.enabled else "[OFF]"
        print(f"\n  {flag} {pf.id}")
        print(f"        description: {pf.description}")
        print(f"        added: {pf.added_date}")
    print()


if __name__ == "__main__":
    describe_all()
