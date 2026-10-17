"""Read-only local configuration/connection probe. Never initializes or migrates data."""
from configuration import config_value
from safe_logging import safe_event
from storage import DatabaseConfig, DatabaseNotConfigured, MigrationRequired, StorageError, Store


def main():
    try:
        url = config_value("SUPABASE_DB_URL")
        if not url:
            raise DatabaseNotConfigured("Not configured: set SUPABASE_DB_URL in .env or environment, not .env.example.")
        store = Store(DatabaseConfig(url, config_value("SUPABASE_SSL_ROOT_CERT")))
        if store._options["user"].split(".")[0] in {"postgres", "supabase_admin"}:
            print("Restricted backend login required. Replace the administrative URI before running this check.")
            return 1
        store.check_connection(require_restricted=True)
        print("Connected: verified TLS connection, expected migration and restricted backend role confirmed.")
        return 0
    except (DatabaseNotConfigured, MigrationRequired) as exc:
        print(str(exc))
    except StorageError as exc:
        safe_event("database_probe_failed", error_category=getattr(exc, "category", "connection_failed"))
        print(str(exc))
    except Exception:
        safe_event("database_probe_failed", error_category="connection_failed")
        print("Connection check failed. Review local configuration and retry; no database changes were made.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
