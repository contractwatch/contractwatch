"""SQLite database layer for ContractWatch historical tracking."""

import sqlite3
from contextlib import contextmanager
from engine.config import DB_PATH


def init_db(db_path=None):
    """Create tables if they don't exist.

    Semantics convention for the awards table:
      Each unique contract_award_unique_key gets ONE row, tagged with the
      LATEST action_date seen across all sources (bulk archive loader +
      live scanner catch-up). A multi-year IDV with transactions in
      FY18-FY26 has its row tagged with the FY26 date, not FY18. This
      keeps row counts honest (one row per award, no per-FY duplication)
      and pairs with the flag pipeline's has_prior_awards() check, which
      compares action_date back to the awards table to decide "is this
      entity new". Per-FY award counts in this DB therefore reflect
      'awards whose latest activity was in this FY', not 'awards active
      in this FY'. USASpending's per-FY counts use the latter convention
      and will be 3-4x higher because multi-year contracts get counted
      in every FY they touched.
    """
    path = db_path or DB_PATH
    with _connect(path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS awards (
                award_id TEXT PRIMARY KEY,
                generated_unique_award_id TEXT,
                piid TEXT,
                recipient_name TEXT,
                recipient_uei TEXT,
                recipient_address TEXT,
                recipient_state TEXT,
                awarding_agency TEXT,
                awarding_office TEXT,
                naics_code TEXT,
                psc_code TEXT,
                type_of_contract TEXT,
                competition_type TEXT,
                number_of_offers INTEGER,
                current_total_value_of_award REAL,
                action_date TEXT,
                start_date TEXT,
                end_date TEXT,
                description TEXT,
                sole_source INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                award_id TEXT,
                flag_code TEXT,
                severity TEXT,
                detail TEXT,
                scan_date TEXT,
                FOREIGN KEY (award_id) REFERENCES awards(award_id)
            );

            CREATE TABLE IF NOT EXISTS scan_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_flags_award ON flags(award_id);
            CREATE INDEX IF NOT EXISTS idx_flags_code ON flags(flag_code);
            CREATE INDEX IF NOT EXISTS idx_awards_office ON awards(awarding_office);
        """)


@contextmanager
def _connect(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_award(award, db_path=None):
    """Insert or update an award record. Returns True if new."""
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT award_id FROM awards WHERE award_id = ?",
            (award["award_id"],)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE awards SET
                    current_total_value_of_award = ?,
                    end_date = ?
                WHERE award_id = ?
            """, (
                award.get("current_total_value_of_award"),
                award.get("end_date"),
                award["award_id"],
            ))
            return False
        else:
            conn.execute("""
                INSERT INTO awards (
                    award_id, generated_unique_award_id, piid,
                    recipient_name, recipient_uei, recipient_address,
                    recipient_state,
                    awarding_agency, awarding_office,
                    naics_code, psc_code, type_of_contract,
                    competition_type, number_of_offers,
                    current_total_value_of_award,
                    action_date, start_date, end_date, description,
                    sole_source
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
            """, (
                award.get("award_id"), award.get("generated_unique_award_id"),
                award.get("piid"),
                award.get("recipient_name"), award.get("recipient_uei"),
                award.get("recipient_address"), award.get("recipient_state"),
                award.get("awarding_agency"), award.get("awarding_office"),
                award.get("naics_code"), award.get("psc_code"),
                award.get("type_of_contract"),
                award.get("competition_type"), award.get("number_of_offers"),
                award.get("current_total_value_of_award"),
                award.get("action_date"), award.get("start_date"),
                award.get("end_date"), award.get("description"),
                award.get("sole_source", 0),
            ))
            return True


def store_flags(award_id, flags, scan_date, db_path=None):
    """Store flag results for an award, fully replacing any prior flags.

    Deletes by award_id alone (not award_id + scan_date) so a re-scan on a later
    calendar day replaces the award's flags instead of accumulating a second
    set."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM flags WHERE award_id = ?", (award_id,))
        for f in flags:
            conn.execute(
                "INSERT INTO flags (award_id, flag_code, severity, detail, scan_date) VALUES (?, ?, ?, ?, ?)",
                (award_id, f["code"], f["severity"], f["detail"], scan_date)
            )


# --- Query helpers for flag checks ---

def has_prior_awards(recipient_uei, before_date, db_path=None):
    """Check if entity has any prior awards before given date."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM awards WHERE recipient_uei = ? AND action_date < ?",
            (recipient_uei, before_date)
        ).fetchone()
        return row["cnt"] > 0 if row else False


def get_office_vendor_concentration(awarding_office, recipient_uei, db_path=None):
    """For one awarding office, return the count and obligated dollars of its
    full and open (competition code D) awards, and the count and dollars held
    by one recipient. Computed over awards already in the database. Returns
    None if office or UEI is missing.

    Restricting to full and open competition is deliberate: sole-source
    concentration (FFRDC and M&O offices) is structurally expected and is
    not the anomaly this flag is looking for."""
    if not awarding_office or not recipient_uei:
        return None
    with _connect(db_path) as conn:
        office = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(current_total_value_of_award), 0) AS dollars "
            "FROM awards WHERE awarding_office = ? AND competition_type = 'D'",
            (awarding_office,)
        ).fetchone()
        vendor = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(current_total_value_of_award), 0) AS dollars "
            "FROM awards WHERE awarding_office = ? AND recipient_uei = ? "
            "AND competition_type = 'D'",
            (awarding_office, recipient_uei)
        ).fetchone()
        return {
            "office_count": office["n"],
            "office_dollars": office["dollars"],
            "vendor_count": vendor["n"],
            "vendor_dollars": vendor["dollars"],
        }


# --- Scan state (incremental cursor) ---

def get_scan_state(key, db_path=None):
    """Read a persisted scan-state value, or None."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT value FROM scan_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_scan_state(key, value, db_path=None):
    """Persist a scan-state value."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO scan_state (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
