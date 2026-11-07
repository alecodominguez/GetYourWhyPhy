"""
File: migrations.py
Purpose: Versioned, idempotent schema migrations for data/campus_wifi.db.

SQLAlchemy's create_all() only creates tables that don't exist; it never adds
a column to a table that already does. So every schema change to an existing
table needs an explicit ALTER TABLE, and every change to stored data needs an
explicit UPDATE. This module does both, records what it applied in a
`schema_migrations` table, and backs the database up before touching it.

server.py calls upgrade() at startup (after create_all), so a deploy-on-push
migrates the live database automatically. You can also run it by hand:

    python migrations.py data/campus_wifi.db --dry-run   # report, change nothing
    python migrations.py data/campus_wifi.db             # back up, then apply

Every step is safe to re-run. Each migration runs in one transaction (SQLite
DDL is transactional), so a failure leaves the database exactly as it was.
"""
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

import scoring

LEGACY_CONTRIBUTOR = "legacy-import-2026-11"
WINDOWS = "Windows"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _add_column(conn, table, column, decl, log):
    if table in _tables(conn) and column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        log(f"  + {table}.{column} {decl}")


def _migration_1_data_quality(conn, db_path, log):
    """Nov 2026 data-quality pass."""
    tables = _tables(conn)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    summary = {}

    # -- New tables (normally already made by create_all; kept here so the
    #    script also works standalone against a copied database) --------------
    conn.execute("CREATE TABLE IF NOT EXISTS contributors (id VARCHAR PRIMARY KEY, "
                 "first_seen DATETIME, last_seen DATETIME)")
    conn.execute("CREATE TABLE IF NOT EXISTS bssid_votes ("
                 "contributor_id VARCHAR NOT NULL REFERENCES contributors(id), "
                 "bssid VARCHAR NOT NULL, building VARCHAR NOT NULL, voted_at DATETIME, "
                 "PRIMARY KEY (contributor_id, bssid))")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_bssid_votes_bssid ON bssid_votes (bssid)")
    conn.execute("CREATE TABLE IF NOT EXISTS bssid_seeds (bssid VARCHAR PRIMARY KEY, "
                 "building VARCHAR NOT NULL, seeded_at DATETIME)")

    # -- wifi_logs: new columns -------------------------------------------------
    if "wifi_logs" in tables:
        for col, decl in [("contributor_id", "VARCHAR REFERENCES contributors(id)"),
                          ("client_score", "FLOAT"), ("jitter_valid", "BOOLEAN"),
                          ("packet_loss_valid", "BOOLEAN"), ("client_version", "INTEGER"),
                          ("bssid", "VARCHAR"), ("score_adjusted", "FLOAT"),
                          ("device_os", "VARCHAR"), ("device_wifi_standard", "VARCHAR"),
                          ("source", "VARCHAR DEFAULT 'native'")]:
            _add_column(conn, "wifi_logs", col, decl, log)
        conn.execute("UPDATE wifi_logs SET source = 'native' WHERE source IS NULL")

        # Flag known-bad raw values. Every pre-fix native client reported
        # jitter = 0.0 (ping-parsing loop bug), and macOS/Linux ones reported
        # packet_loss = 20.0 (the ping COUNT). Web Lite measured both correctly.
        cur = conn.execute("""
            UPDATE wifi_logs SET jitter_valid =
                CASE WHEN source = 'web_lite' THEN 1
                     WHEN jitter IS NULL OR jitter = 0 THEN 0 ELSE 1 END
            WHERE jitter_valid IS NULL""")
        summary["jitter_rows_assessed"] = cur.rowcount
        cur = conn.execute(f"""
            UPDATE wifi_logs SET packet_loss_valid =
                CASE WHEN source = 'web_lite' THEN 1
                     WHEN packet_loss IS NULL THEN 0
                     WHEN packet_loss = 20 AND COALESCE(device_os, '') <> '{WINDOWS}' THEN 0
                     ELSE 1 END
            WHERE packet_loss_valid IS NULL""")
        summary["packet_loss_rows_assessed"] = cur.rowcount
        summary["jitter_flagged"] = conn.execute(
            "SELECT COUNT(*) FROM wifi_logs WHERE jitter_valid = 0").fetchone()[0]
        summary["packet_loss_flagged"] = conn.execute(
            "SELECT COUNT(*) FROM wifi_logs WHERE packet_loss_valid = 0").fetchone()[0]

        # Keep what the client claimed, then recompute the derived scores from
        # the raw inputs with the bad metrics left out. This is the payoff of
        # having stored raw metrics next to the derived score.
        conn.execute("UPDATE wifi_logs SET client_score = score WHERE client_score IS NULL")
        rows = conn.execute("""SELECT id, download, upload, latency, jitter, packet_loss,
                                      jitter_valid, packet_loss_valid, device_wifi_standard
                               FROM wifi_logs""").fetchall()
        changed = 0
        for (rid, dl, ul, lat, jit, loss, jv, lv, std) in rows:
            raw = {"download": dl, "upload": ul, "latency": lat,
                   "jitter": jit if jv else None, "packet_loss": loss if lv else None}
            new_score = scoring.compute_score(raw)
            new_adj = scoring.compute_adjusted_score(raw, std)
            cur = conn.execute("UPDATE wifi_logs SET score = ?, score_adjusted = ? WHERE id = ? "
                               "AND (score IS NOT ? OR score_adjusted IS NOT ?)",
                               (new_score, new_adj, rid, new_score, new_adj))
            changed += cur.rowcount
        summary["scores_recomputed"] = changed

        conn.execute("CREATE INDEX IF NOT EXISTS ix_wifi_logs_location_timestamp "
                     "ON wifi_logs (location, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_wifi_logs_contributor_id ON wifi_logs (contributor_id)")

    # -- Old running-count table -> votes ---------------------------------------
    # bssid_mappings.confirmations was inflated (each run counted twice, and
    # passive benchmarks self-confirmed) and has no contributor behind it, so it
    # can't be trusted or recomputed. Each old mapping becomes ONE vote from a
    # placeholder contributor; the old table is kept, renamed, for reference.
    # Mappings that were "confirmed" are written to a CSV so an admin can
    # re-seed the ones they know are right via POST /admin/seed-bssid.
    if "bssid_mappings" in tables:
        legacy = conn.execute("SELECT bssid, building, confirmations FROM bssid_mappings").fetchall()
        conn.execute("INSERT OR IGNORE INTO contributors (id, first_seen, last_seen) VALUES (?, ?, ?)",
                     (LEGACY_CONTRIBUTOR, now, now))
        for bssid, building, _ in legacy:
            if bssid and building:
                conn.execute("INSERT OR IGNORE INTO bssid_votes (contributor_id, bssid, building, voted_at) "
                             "VALUES (?, ?, ?, ?)", (LEGACY_CONTRIBUTOR, bssid.lower(), building, now))
        conn.execute("ALTER TABLE bssid_mappings RENAME TO bssid_mappings_legacy")
        review = [r for r in legacy if (r[2] or 0) >= 3]
        summary["legacy_mappings_converted"] = len(legacy)
        summary["legacy_mappings_previously_confirmed"] = len(review)
        if review and db_path:
            out = os.path.join(os.path.dirname(os.path.abspath(db_path)), "legacy_bssid_review.csv")
            with open(out, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["bssid", "building", "old_confirmations"])
                w.writerows(review)
            summary["review_csv"] = out

    # -- drop_events: dedupe, then enforce the natural key ----------------------
    if "drop_events" in tables:
        _add_column(conn, "drop_events", "contributor_id", "VARCHAR REFERENCES contributors(id)", log)
        cur = conn.execute("""DELETE FROM drop_events WHERE id NOT IN
                              (SELECT MIN(id) FROM drop_events GROUP BY building, started_at)""")
        summary["duplicate_drop_events_removed"] = cur.rowcount
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_drop_events_building_started "
                     "ON drop_events (building, started_at)")

    return summary


MIGRATIONS = [
    (1, "Nov 2026 data quality: contributors, bssid_votes, validity flags, score recompute",
     _migration_1_data_quality),
]


def _backup(conn, db_path, version, log):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = f"{db_path}.pre-v{version}-{stamp}.bak"
    with sqlite3.connect(dest) as backup_conn:
        conn.backup(backup_conn)   # consistent online copy, even while in use
    log(f"[migrations] backed up database to {dest}")
    return dest


def upgrade(db_path, dry_run=False, log=print):
    conn = sqlite3.connect(db_path, isolation_level=None)  # we manage transactions
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT)")
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        pending = [m for m in MIGRATIONS if m[0] not in applied]
        if not pending:
            return {}
        has_data = "wifi_logs" in _tables(conn) and \
            conn.execute("SELECT COUNT(*) FROM wifi_logs").fetchone()[0] > 0
        results = {}
        for version, description, fn in pending:
            if has_data and not dry_run:
                _backup(conn, db_path, version, log)
            log(f"[migrations] {'(dry run) ' if dry_run else ''}applying v{version}: {description}")
            conn.execute("BEGIN")
            try:
                summary = fn(conn, None if dry_run else db_path, log)
                conn.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)",
                             (version, description, datetime.now(timezone.utc).isoformat()))
                conn.execute("ROLLBACK" if dry_run else "COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            for k, v in summary.items():
                log(f"    {k}: {v}")
            results[version] = summary
        return results
    finally:
        conn.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "campus_wifi.db")
    if not os.path.exists(path):
        sys.exit(f"No database at {path}")
    upgrade(path, dry_run="--dry-run" in sys.argv)
