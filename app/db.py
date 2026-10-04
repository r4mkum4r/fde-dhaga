"""
Database access. Two backends, one set of queries:

- Postgres, when DATABASE_URL is set (the team's `dhaga` database, or a hosted one
  such as Neon or Supabase). Corrections survive restarts.
- SQLite otherwise. Built from csv/ on first start, so a stranger needs no database
  to run the app. On a Hugging Face Space the disk resets on restart, so corrections
  saved to SQLite there are temporary; the app says so on screen.
"""
import csv
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = ROOT / "csv"

ENRICHED_SQL = """
SELECT r.return_id, r.return_date, r.reason_dropdown, r.other_text,
       o.ship_city, o.ship_state, oi.size,
       p.sku, p.product_name, p.department, p.subcategory,
       v.vendor_id, v.vendor_name
FROM returns r
JOIN orders o       ON o.order_id = r.order_id
JOIN order_items oi ON oi.order_item_id = r.order_item_id
JOIN products p     ON p.sku = r.sku
JOIN vendors v      ON v.vendor_id = p.vendor_id
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (vendor_id TEXT PRIMARY KEY, vendor_name TEXT, vendor_city TEXT,
    size_chart_version TEXT, lead_time_days_note TEXT);
CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, product_name TEXT, department TEXT, subcategory TEXT,
    vendor_id TEXT, colour_raw TEXT, fabric_raw TEXT, price_inr INTEGER, size_scheme TEXT, launch_date TEXT);
CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, customer_id TEXT, order_date TEXT, payment_mode TEXT,
    order_status TEXT, delivered_date TEXT, courier TEXT, ship_city TEXT, ship_state TEXT, ship_pincode TEXT,
    city_tier INTEGER, order_total_inr INTEGER);
CREATE TABLE IF NOT EXISTS order_items (order_item_id TEXT PRIMARY KEY, order_id TEXT, sku TEXT, size TEXT,
    quantity INTEGER, unit_price_inr INTEGER);
CREATE TABLE IF NOT EXISTS returns (return_id TEXT PRIMARY KEY, order_id TEXT, order_item_id TEXT, sku TEXT,
    return_date TEXT NOT NULL, raised_via TEXT, reason_dropdown TEXT NOT NULL, other_text TEXT, return_status TEXT);
CREATE TABLE IF NOT EXISTS classified_returns (return_id TEXT PRIMARY KEY, issue_type TEXT NOT NULL,
    confidence REAL, evidence_phrase TEXT, source TEXT NOT NULL, model_name TEXT,
    classified_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY AUTOINCREMENT, return_id TEXT, model_issue_type TEXT,
    is_correct INTEGER NOT NULL, corrected_issue TEXT, note TEXT, corrected_by TEXT DEFAULT 'neha',
    corrected_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO settings (key, value) VALUES ('confidence_threshold', '0.70'), ('min_returns_per_hotspot', '20');
"""
# Only what the dashboard reads. customers, reviews and eval_return_labels are left out on purpose:
# the answer sheet must never sit next to the app that shows labels.
SQLITE_TABLES = ["vendors", "products", "orders", "order_items", "returns"]


class DatabaseError(Exception):
    """Raised with a sentence safe to show on screen (never contains the password)."""


class Database:
    def __init__(self, url=None, sqlite_path=None):
        self.url = url if url is not None else os.environ.get("DATABASE_URL", "").strip()
        self.kind = "postgres" if self.url.startswith(("postgres://", "postgresql://")) else "sqlite"
        default_path = Path(os.environ.get("DATA_DIR", ROOT / "var")) / "dhaga.sqlite"
        self.sqlite_path = Path(sqlite_path) if sqlite_path else default_path

    # ---------- connection ----------
    def describe(self):
        if self.kind == "postgres":
            from urllib.parse import urlparse
            u = urlparse(self.url)
            return f"Postgres {u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}"
        return f"SQLite {self.sqlite_path.name} (built from csv/)"

    def _connect(self):
        if self.kind == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as e:
                raise DatabaseError("DATABASE_URL is set but psycopg isn't installed. Run: pip install -r requirements.txt") from e
            try:
                return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5)
            except Exception as e:  # noqa: BLE001 - shown to the user as one sentence
                raise DatabaseError(f"Can't reach the database at {self.describe()}: {type(e).__name__}") from e
        if not self.sqlite_path.exists():
            self.build_sqlite()
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _q(self, sql):
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def _all(self, sql, params=()):
        conn = self._connect()
        try:
            cur = conn.execute(self._q(sql), params)
            return [dict(r) for r in cur.fetchall()]
        except DatabaseError:
            raise
        except Exception as e:  # noqa: BLE001
            raise DatabaseError(f"Database query failed: {e}") from e
        finally:
            conn.close()

    def build_sqlite(self):
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.sqlite_path.with_suffix(".building")
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(tmp)
        try:
            conn.executescript(SQLITE_SCHEMA)
            for table in SQLITE_TABLES:
                path = CSV_DIR / f"{table}.csv"
                if not path.exists():
                    raise DatabaseError(f"Missing {path.relative_to(ROOT)}. Run: python3 generate_data.py")
                with open(path, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    cols = reader.fieldnames
                    rows = [tuple(r[c] if r[c] != "" else None for c in cols) for r in reader]
                conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
            # The AI pipeline's output, if it has been committed (csv/classified_returns.csv).
            from .analytics import CLASSIFIED_COLUMNS, read_classified_csv
            classified = read_classified_csv(CSV_DIR / "classified_returns.csv")
            if classified:
                conn.executemany(
                    f"INSERT INTO classified_returns ({','.join(CLASSIFIED_COLUMNS)}) VALUES ({','.join('?' * len(CLASSIFIED_COLUMNS))})",
                    [tuple(r[c] for c in CLASSIFIED_COLUMNS) for r in classified.values()])
            conn.commit()
        finally:
            conn.close()
        tmp.replace(self.sqlite_path)

    # ---------- reads ----------
    def enriched(self):
        return self._all(ENRICHED_SQL)

    def classified(self):
        rows = self._all("SELECT return_id, issue_type, confidence, evidence_phrase, source, model_name FROM classified_returns")
        return {r["return_id"]: r for r in rows}

    def classified_version(self):
        """Changes whenever the pipeline writes; used to know when to recompute."""
        r = self._all("SELECT COUNT(*) AS n, MAX(classified_at) AS latest FROM classified_returns")[0]
        return (r["n"], str(r["latest"]))

    def settings(self):
        from .analytics import DEFAULT_SETTINGS
        out = dict(DEFAULT_SETTINGS)
        for r in self._all("SELECT key, value FROM settings"):
            if r["key"] in out:
                out[r["key"]] = float(r["value"]) if r["key"] == "confidence_threshold" else int(float(r["value"]))
        return out

    def table_counts(self):
        out = {}
        for t in ["returns", "orders", "products", "vendors", "classified_returns", "corrections"]:
            out[t] = self._all(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        return out

    def return_exists(self, return_id):
        return bool(self._all("SELECT 1 AS x FROM returns WHERE return_id = ?", (return_id,)))

    # ---------- corrections ----------
    def corrections(self):
        """Latest mark per return."""
        rows = self._all("SELECT return_id, model_issue_type, is_correct, corrected_issue, corrected_at FROM corrections ORDER BY id")
        latest = {}
        for r in rows:
            latest[r["return_id"]] = {
                "return_id": r["return_id"],
                "model_issue_type": r["model_issue_type"],
                "is_correct": bool(r["is_correct"]),
                "corrected_issue": r["corrected_issue"],
                "corrected_at": str(r["corrected_at"]),
            }
        return latest

    def add_correction(self, c):
        conn = self._connect()
        try:
            is_correct = c["is_correct"] if self.kind == "postgres" else int(c["is_correct"])
            conn.execute(self._q("INSERT INTO corrections (return_id, model_issue_type, is_correct, corrected_issue) VALUES (?, ?, ?, ?)"),
                         (c["return_id"], c["model_issue_type"], is_correct, c.get("corrected_issue")))
            conn.commit()
        except Exception as e:  # noqa: BLE001
            raise DatabaseError(f"Couldn't save the correction: {e}") from e
        finally:
            conn.close()

    def clear_corrections(self):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM corrections")
            conn.commit()
        finally:
            conn.close()

    @property
    def corrections_permanent(self):
        return self.kind == "postgres"
