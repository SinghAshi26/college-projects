"""Server-side configuration; .env is local convenience, deployment secrets take priority."""
import os
from pathlib import Path

from dotenv import dotenv_values

from storage import DatabaseConfig, DatabaseNotConfigured, StorageError, Store

ENV_FILE = Path(__file__).with_name(".env")


def config_value(name, default=""):
    """Read fresh local values without caching them in the process environment."""
    if name in os.environ:
        val = os.environ[name]
    else:
        val = dotenv_values(ENV_FILE, encoding="utf-8-sig").get(name)
        if val is None or val == "":
            val = default
    if isinstance(val, str):
        val = val.strip().strip("'\"")
    return val


def configured_store(getter=None):
    get = getter or config_value
    url = get("SUPABASE_DB_URL", "")
    if not url:
        raise DatabaseNotConfigured("Database not configured. Set SUPABASE_DB_URL and apply the SQL migration to enable saved leads and search usage control.")
    store = Store(DatabaseConfig(url, get("SUPABASE_SSL_ROOT_CERT", "")))
    try:
        daily = int(get("SEARCH_DAILY_LIMIT", "0"))
        monthly = int(get("SEARCH_MONTHLY_LIMIT", "900"))
    except (TypeError, ValueError):
        raise StorageError("SEARCH_DAILY_LIMIT and SEARCH_MONTHLY_LIMIT must be integers.") from None
    store.initialize(daily, monthly)
    return store
