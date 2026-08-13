"""The Supplier Ledger: what this business's own history says is normal.

This is the asset. Every strong check is a function over it, because the
reliable fraud signal is never "this looks odd in general", it is "this
differs from what this supplier has always done with us".

SQLite from the standard library. No server, no service, no subscription,
and the file lives inside the customer's own perimeter.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplier (
    supplier_key   TEXT PRIMARY KEY,
    display_name   TEXT,
    abn            TEXT,
    first_seen     TEXT,
    last_seen      TEXT,
    invoice_count  INTEGER NOT NULL DEFAULT 0,
    paid_count     INTEGER NOT NULL DEFAULT 0,
    notes          TEXT
);

-- Every payment destination ever seen for a supplier, and crucially
-- whether we ever actually PAID it. The #1 control compares against the
-- last destination that was paid, not merely the last one that appeared.
CREATE TABLE IF NOT EXISTS payment_destination (
    supplier_key   TEXT NOT NULL,
    fingerprint    TEXT NOT NULL,
    detail_json    TEXT NOT NULL,
    first_seen     TEXT,
    last_seen      TEXT,
    seen_count     INTEGER NOT NULL DEFAULT 0,
    paid_count     INTEGER NOT NULL DEFAULT 0,
    last_paid_at   TEXT,
    PRIMARY KEY (supplier_key, fingerprint)
);

CREATE TABLE IF NOT EXISTS sender (
    supplier_key   TEXT NOT NULL,
    domain         TEXT NOT NULL,
    first_seen     TEXT,
    last_seen      TEXT,
    seen_count     INTEGER NOT NULL DEFAULT 0,
    is_relay       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (supplier_key, domain)
);

CREATE TABLE IF NOT EXISTS document (
    doc_id         TEXT PRIMARY KEY,
    content_hash   TEXT,
    supplier_key   TEXT,
    invoice_number TEXT,
    issue_date     TEXT,
    total_cents    INTEGER,
    currency       TEXT,
    channel        TEXT,
    received_at    TEXT,
    paid_at        TEXT,
    verdict        TEXT,
    payload_json   TEXT
);

CREATE INDEX IF NOT EXISTS ix_doc_supplier ON document(supplier_key);
CREATE INDEX IF NOT EXISTS ix_doc_number   ON document(supplier_key, invoice_number);
CREATE INDEX IF NOT EXISTS ix_doc_hash     ON document(content_hash);
CREATE INDEX IF NOT EXISTS ix_doc_total    ON document(supplier_key, total_cents);

-- Registry answers are cached WITH the date they were asked, because a
-- supplier's status must be evaluated as at the invoice date, not today.
CREATE TABLE IF NOT EXISTS registry_cache (
    key            TEXT NOT NULL,
    asked_at       TEXT NOT NULL,
    response_json  TEXT NOT NULL,
    PRIMARY KEY (key, asked_at)
);

-- Append-only evidence trail. Defends a tax position and a reasonable-steps
-- position later, which is why it is never updated or deleted.
CREATE TABLE IF NOT EXISTS evidence (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    at             TEXT NOT NULL,
    doc_id         TEXT,
    actor          TEXT,
    action         TEXT NOT NULL,
    detail_json    TEXT
);
"""


class Ledger:
    """Thin, explicit store. No ORM, no magic, no migrations framework."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with closing(self._conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
            cur.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),))
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- suppliers ---------------------------------------------------------

    def upsert_supplier(self, supplier_key: str, display_name: str | None,
                        abn: str | None, when: str):
        with closing(self._conn.cursor()) as cur:
            cur.execute("""
                INSERT INTO supplier(supplier_key, display_name, abn,
                                     first_seen, last_seen, invoice_count)
                VALUES(?,?,?,?,?,1)
                ON CONFLICT(supplier_key) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, supplier.display_name),
                    abn          = COALESCE(excluded.abn, supplier.abn),
                    last_seen    = excluded.last_seen,
                    invoice_count = supplier.invoice_count + 1
            """, (supplier_key, display_name, abn, when, when))
        self._conn.commit()

    def get_supplier(self, supplier_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM supplier WHERE supplier_key=?", (supplier_key,)
        ).fetchone()
        return dict(row) if row else None

    def is_known_supplier(self, supplier_key: str) -> bool:
        return self.get_supplier(supplier_key) is not None

    # -- payment destinations ---------------------------------------------

    def record_destination(self, supplier_key: str, fingerprint: str,
                           detail: dict, when: str):
        with closing(self._conn.cursor()) as cur:
            cur.execute("""
                INSERT INTO payment_destination(
                    supplier_key, fingerprint, detail_json,
                    first_seen, last_seen, seen_count)
                VALUES(?,?,?,?,?,1)
                ON CONFLICT(supplier_key, fingerprint) DO UPDATE SET
                    last_seen  = excluded.last_seen,
                    seen_count = payment_destination.seen_count + 1
            """, (supplier_key, fingerprint, json.dumps(detail, sort_keys=True),
                  when, when))
        self._conn.commit()

    def mark_destination_paid(self, supplier_key: str, fingerprint: str,
                              when: str):
        """Record that money actually moved to this destination.

        The distinction matters: an attacker's account will have been *seen*
        the moment their invoice arrives. Only a destination we genuinely
        paid is a trustworthy baseline.
        """
        with closing(self._conn.cursor()) as cur:
            cur.execute("""
                UPDATE payment_destination
                   SET paid_count = paid_count + 1, last_paid_at = ?
                 WHERE supplier_key = ? AND fingerprint = ?
            """, (when, supplier_key, fingerprint))
            cur.execute(
                "UPDATE supplier SET paid_count = paid_count + 1 "
                "WHERE supplier_key = ?", (supplier_key,))
        self._conn.commit()

    def paid_destinations(self, supplier_key: str) -> list[dict]:
        """Destinations this supplier has actually been paid at, newest first."""
        rows = self._conn.execute("""
            SELECT * FROM payment_destination
             WHERE supplier_key = ? AND paid_count > 0
             ORDER BY last_paid_at DESC
        """, (supplier_key,)).fetchall()
        return [dict(r) for r in rows]

    def seen_destinations(self, supplier_key: str) -> list[dict]:
        rows = self._conn.execute("""
            SELECT * FROM payment_destination
             WHERE supplier_key = ?
             ORDER BY last_seen DESC
        """, (supplier_key,)).fetchall()
        return [dict(r) for r in rows]

    # -- senders -----------------------------------------------------------

    def record_sender(self, supplier_key: str, domain: str, when: str,
                      is_relay: bool = False):
        with closing(self._conn.cursor()) as cur:
            cur.execute("""
                INSERT INTO sender(supplier_key, domain, first_seen,
                                   last_seen, seen_count, is_relay)
                VALUES(?,?,?,?,1,?)
                ON CONFLICT(supplier_key, domain) DO UPDATE SET
                    last_seen  = excluded.last_seen,
                    seen_count = sender.seen_count + 1
            """, (supplier_key, domain.lower(), when, when, 1 if is_relay else 0))
        self._conn.commit()

    def known_senders(self, supplier_key: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT domain FROM sender WHERE supplier_key=?", (supplier_key,)
        ).fetchall()
        return [r["domain"] for r in rows]

    # -- documents ---------------------------------------------------------

    def record_document(self, doc_id: str, content_hash: str,
                        supplier_key: str | None, invoice_number: str | None,
                        issue_date: str | None, total_cents: int | None,
                        currency: str | None, channel: str,
                        received_at: str | None, verdict: str | None = None,
                        payload: dict | None = None):
        with closing(self._conn.cursor()) as cur:
            cur.execute("""
                INSERT INTO document(doc_id, content_hash, supplier_key,
                    invoice_number, issue_date, total_cents, currency,
                    channel, received_at, verdict, payload_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    verdict = excluded.verdict
            """, (doc_id, content_hash, supplier_key, invoice_number,
                  issue_date, total_cents, currency, channel, received_at,
                  verdict, json.dumps(payload or {}, sort_keys=True)))
        self._conn.commit()

    def mark_document_paid(self, doc_id: str, when: str):
        with closing(self._conn.cursor()) as cur:
            cur.execute("UPDATE document SET paid_at=? WHERE doc_id=?",
                        (when, doc_id))
        self._conn.commit()

    def find_by_invoice_number(self, supplier_key: str,
                               invoice_number: str) -> list[dict]:
        rows = self._conn.execute("""
            SELECT * FROM document
             WHERE supplier_key = ? AND invoice_number = ?
        """, (supplier_key, invoice_number)).fetchall()
        return [dict(r) for r in rows]

    def find_by_content_hash(self, content_hash: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM document WHERE content_hash=?", (content_hash,)
        ).fetchall()
        return [dict(r) for r in rows]

    def find_similar_amount(self, supplier_key: str, total_cents: int,
                            tolerance_cents: int = 0) -> list[dict]:
        rows = self._conn.execute("""
            SELECT * FROM document
             WHERE supplier_key = ?
               AND total_cents BETWEEN ? AND ?
        """, (supplier_key, total_cents - tolerance_cents,
              total_cents + tolerance_cents)).fetchall()
        return [dict(r) for r in rows]

    def supplier_documents(self, supplier_key: str) -> list[dict]:
        rows = self._conn.execute("""
            SELECT * FROM document WHERE supplier_key = ?
             ORDER BY issue_date
        """, (supplier_key,)).fetchall()
        return [dict(r) for r in rows]

    # -- registry cache ----------------------------------------------------

    def cache_registry(self, key: str, asked_at: str, response: dict):
        with closing(self._conn.cursor()) as cur:
            cur.execute("""
                INSERT OR REPLACE INTO registry_cache(key, asked_at, response_json)
                VALUES(?,?,?)
            """, (key, asked_at, json.dumps(response, sort_keys=True)))
        self._conn.commit()

    def registry_answers(self, key: str) -> list[dict]:
        rows = self._conn.execute("""
            SELECT asked_at, response_json FROM registry_cache
             WHERE key = ? ORDER BY asked_at DESC
        """, (key,)).fetchall()
        return [{"asked_at": r["asked_at"],
                 "response": json.loads(r["response_json"])} for r in rows]

    # -- evidence ----------------------------------------------------------

    def add_evidence(self, at: str, action: str, doc_id: str | None = None,
                     actor: str = "billguard", detail: dict | None = None):
        with closing(self._conn.cursor()) as cur:
            cur.execute("""
                INSERT INTO evidence(at, doc_id, actor, action, detail_json)
                VALUES(?,?,?,?,?)
            """, (at, doc_id, actor, action,
                  json.dumps(detail or {}, sort_keys=True)))
        self._conn.commit()

    def evidence_for(self, doc_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM evidence WHERE doc_id=? ORDER BY id", (doc_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- stats -------------------------------------------------------------

    def stats(self) -> dict:
        def one(sql):
            return self._conn.execute(sql).fetchone()[0]
        return {
            "suppliers": one("SELECT COUNT(*) FROM supplier"),
            "documents": one("SELECT COUNT(*) FROM document"),
            "destinations": one("SELECT COUNT(*) FROM payment_destination"),
            "paid_destinations":
                one("SELECT COUNT(*) FROM payment_destination WHERE paid_count>0"),
            "evidence_rows": one("SELECT COUNT(*) FROM evidence"),
        }
