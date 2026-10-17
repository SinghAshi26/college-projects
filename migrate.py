"""Explicit, non-destructive migration runner. Never runs on UI startup."""
import argparse
import os
from pathlib import Path

from configuration import config_value
from storage import DatabaseConfig, StorageError, Store


def main():
    parser = argparse.ArgumentParser(description="Apply additive Appnix SQL migrations using a privileged migration connection.")
    parser.add_argument("--apply", action="store_true", help="Required confirmation to apply migrations")
    args = parser.parse_args()
    if not args.apply:
        parser.error("Review migrations/001_persistence.sql, then pass --apply.")
    url = config_value("SUPABASE_DB_URL", "")
    if not url:
        raise SystemExit("Set SUPABASE_DB_URL in your environment or .env before applying migrations.")
    try:
        store = Store(DatabaseConfig(url, config_value("SUPABASE_SSL_ROOT_CERT", "")))
        with store.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(714628310)")
            for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
                conn.execute(path.read_text(encoding="utf-8"))
        print("Migration transaction committed successfully.")
    except StorageError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
