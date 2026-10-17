"""Private, single-workspace PostgreSQL storage. No provider payloads in usage logs."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from safe_logging import safe_event


class StorageError(RuntimeError):
    pass


DATABASE_DIAGNOSTICS = {
    "certificate_missing": "Database certificate missing. Set SUPABASE_SSL_ROOT_CERT to an existing CA certificate file, then refresh.",
    "invalid_configuration": "Database configuration invalid. Check the PostgreSQL URI and required settings in .env or deployment secrets.",
    "authentication": "Database credentials rejected. Check the restricted backend login and password; URL-encode special characters in the URI.",
    "network": "Database unavailable: network or DNS failure. Check the database host, port, network access and project availability. Use the session pooler if direct IPv6 is unavailable.",
    "tls": "Database TLS verification failed. Check the database CA certificate and hostname. Keep sslmode=verify-full enabled.",
    "permission": "Database access denied. Ask an administrator to verify the restricted backend login's appnix_backend membership and grants.",
    "connection_failed": "Database unavailable. No save was confirmed. Check the connection settings and retry.",
}


class DatabaseConnectionError(StorageError):
    def __init__(self, category):
        self.category = category if category in DATABASE_DIAGNOSTICS else "connection_failed"
        super().__init__(DATABASE_DIAGNOSTICS[self.category])


def connection_error_category(exc):
    """Inspect driver diagnostics locally; return only an allowlisted category."""
    if exc.sqlstate in {"28P01", "28000"}:
        return "authentication"
    if exc.sqlstate == "42501":
        return "permission"
    # libpq connection failures often have no SQLSTATE. Never emit this text.
    detail = str(exc).lower()
    if any(s in detail for s in ("password authentication failed", "tenant or user not found", "authentication failed")):
        return "authentication"
    if "root certificate file" in detail and any(s in detail for s in ("does not exist", "could not open", "no such file")):
        return "certificate_missing"
    if any(s in detail for s in ("ssl", "tls", "certificate")):
        return "tls"
    if any(s in detail for s in ("could not translate host", "name or service not known", "getaddrinfo", "no such host", "connection refused", "timeout expired", "connection timed out", "network is unreachable", "could not resolve", "connection reset")):
        return "network"
    return "connection_failed"


class DatabaseNotConfigured(StorageError):
    pass


class MigrationRequired(StorageError):
    pass


class QuotaReached(StorageError):
    pass


class EditConflict(StorageError):
    pass


@dataclass(repr=False)
class DatabaseConfig:
    url: str = field(repr=False)
    root_cert: str = ""
    allow_local_test: bool = False

    def options(self):
        try:
            options = conninfo_to_dict(self.url)
        except Exception:
            raise DatabaseConnectionError("invalid_configuration") from None
        if not options.get("host") or not options.get("dbname") or not options.get("user"):
            raise DatabaseConnectionError("invalid_configuration")
        local_test = self.allow_local_test and options["host"] in ("127.0.0.1", "localhost") and options["dbname"].startswith("appnix_test")
        if not local_test:
            mode = options.get("sslmode", "verify-full")
            if mode not in ("verify-full", "require"):
                raise DatabaseConnectionError("tls")
            if mode != "require":
                options["sslmode"] = "verify-full"
                default_cert = (Path(os.environ.get("APPDATA", str(Path.home()))) / "postgresql/root.crt"
                                if os.name == "nt" else Path.home() / ".postgresql/root.crt")
                certificate = self.root_cert or options.get("sslrootcert") or os.environ.get("PGSSLROOTCERT") or str(default_cert)
                if certificate != "system":
                    cert_path = Path(certificate).expanduser()
                    if not cert_path.is_absolute():
                        cert_path = Path(__file__).parent / cert_path
                    if not cert_path.is_file():
                        raise DatabaseConnectionError("certificate_missing")
                    certificate = str(cert_path.resolve())
                options["sslrootcert"] = certificate
        # Fixed timeouts; callers cannot smuggle startup SQL or unlimited waits through DSN options.
        options.pop("options", None)
        options.update(connect_timeout=5, application_name="appnix", options="-c statement_timeout=8000 -c lock_timeout=5000 -c timezone=UTC")
        return options


def clean_data(value):
    if isinstance(value, dict):
        return {str(k): clean_data(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [clean_data(v) for v in value]
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return ""
    if hasattr(value, "item"):
        return clean_data(value.item())
    return value


def source_key(data):
    if data.get("place_id"):
        return "google:" + str(data["place_id"])
    if data.get("_id"):
        return "internal:" + str(data["_id"])
    identity = [str(data.get(k, "")).strip().casefold() for k in ("name", "address", "phone", "website")]
    if not any(identity):
        raise StorageError("Cannot save a lead without a source ID or business identity.")
    return "business:" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()


PREFERENCE_KEYS = {"sender_name", "company_name", "website_url"}
PROVIDERS = {"google_places", "google_pagespeed", "website"}
OUTCOMES = {"success", "http_error", "timeout", "network_error", "invalid_response"}


class Store:
    def __init__(self, config):
        self.config = config
        self._options = config.options()

    @contextmanager
    def transaction(self):
        try:
            with psycopg.connect(**self._options, row_factory=dict_row, prepare_threshold=None) as conn:
                yield conn
        except psycopg.Error as exc:
            if exc.sqlstate in {"42P01", "42703", "3F000"}:
                safe_event("database_check_failed", error_category="migration_required")
                raise MigrationRequired("Database migration required. Ask an administrator to apply the reviewed migration, then refresh.") from None
            category = connection_error_category(exc)
            safe_event("database_check_failed", error_category=category)
            raise DatabaseConnectionError(category) from None

    def check_connection(self, require_restricted=False):
        """Verify connectivity and the expected migration, without changing schema or data."""
        with self.transaction() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SELECT 1").fetchone()
            migration = conn.execute("SELECT version FROM appnix.schema_migrations WHERE version=%s", ("001_persistence",)).fetchone()
            if not migration:
                safe_event("database_check_failed", error_category="migration_required")
                raise MigrationRequired("Database migration required. Ask an administrator to apply the reviewed migration, then refresh.")
            if require_restricted:
                role = conn.execute("""SELECT r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole
                    OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname='appnix' AND n.nspowner=r.oid) AS privileged,
                    EXISTS (SELECT 1 FROM pg_roles g WHERE g.rolname='appnix_backend'
                            AND pg_has_role(current_user,g.oid,'MEMBER')) AS backend
                    FROM pg_roles r WHERE r.rolname=current_user""").fetchone()
                if not role or role["privileged"] or not role["backend"]:
                    raise DatabaseConnectionError("permission")

    @staticmethod
    def periods(conn):
        # Database clock and UTC; never browser time or host-local dates.
        row = conn.execute("""SELECT date_trunc('day', now(), 'UTC') AS day,
            date_trunc('month', now(), 'UTC') AS month,
            date_trunc('day', now(), 'UTC') + interval '1 day' AS day_end,
            (date_trunc('month', now() AT TIME ZONE 'UTC') + interval '1 month') AT TIME ZONE 'UTC' AS month_end""").fetchone()
        return [("day", row["day"], row["day_end"]), ("month", row["month"], row["month_end"])]

    def initialize(self, daily=0, monthly=900):
        if daily < 0 or monthly < 0:
            raise StorageError("Search limits must be non-negative integers.")
        with self.transaction() as conn:
            conn.execute("INSERT INTO appnix.app_settings(id,daily_limit,monthly_limit) VALUES (1,%s,%s) ON CONFLICT DO NOTHING", (daily, monthly))

    def settings(self):
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM appnix.app_settings WHERE id=1").fetchone()
            if not row:
                raise StorageError("Database settings are not initialized.")
            return row

    def save_settings(self, preferences=None, daily=None, monthly=None):
        preferences = preferences or {}
        if set(preferences) - PREFERENCE_KEYS:
            raise StorageError("Only non-secret sender preferences may be saved.")
        if any(not isinstance(v, str) or len(v) > 500 for v in preferences.values()):
            raise StorageError("Sender settings must be text of at most 500 characters.")
        if any(v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0) for v in (daily, monthly)):
            raise StorageError("Search limits must be non-negative integers.")
        with self.transaction() as conn:
            conn.execute("""UPDATE appnix.app_settings SET preferences=preferences || %s,
                daily_limit=coalesce(%s,daily_limit),monthly_limit=coalesce(%s,monthly_limit),updated_at=now() WHERE id=1""",
                (Jsonb(preferences), daily, monthly))

    def load_leads(self, limit=None, offset=0):
        with self.transaction() as conn:
            query = "SELECT id,data FROM appnix.leads ORDER BY created_at,id"
            params = []
            if limit is not None:
                query += " LIMIT %s OFFSET %s"
                params = [limit, offset]
            rows = conn.execute(query, params).fetchall()
            return [dict(r["data"], _id=str(r["id"])) for r in rows]

    @staticmethod
    def insert_leads(conn, leads):
        for lead in leads:
            # Repeat searches must not reset manual edits, audit data, or outreach.
            conn.execute("INSERT INTO appnix.leads(source_key,data) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                         (source_key(lead), Jsonb(clean_data(lead))))

    def save_new_leads(self, leads):
        with self.transaction() as conn:
            self.insert_leads(conn, leads)

    def patch_leads(self, changes):
        """Atomic batch, field-level optimistic conflict detection; no stale whole-row overwrite."""
        with self.transaction() as conn:
            for lead_id, before, patch in sorted(changes, key=lambda item: item[0]):
                patch = clean_data(patch)
                if "place_id" in patch:
                    raise StorageError("Source IDs cannot be edited.")
                row = conn.execute("SELECT data FROM appnix.leads WHERE id=%s FOR UPDATE", (lead_id,)).fetchone()
                if not row:
                    raise EditConflict("This lead is no longer available. Reload before editing.")
                for key, value in patch.items():
                    current = row["data"].get(key, "")
                    if current != clean_data(before.get(key, "")) and current != value:
                        raise EditConflict("Another session changed the same lead field. Reload and reapply your edit.")
                conn.execute("UPDATE appnix.leads SET data=data || %s,updated_at=now() WHERE id=%s", (Jsonb(patch), lead_id))

    @staticmethod
    def ensure_counters(conn, provider, periods):
        for period, start, end in periods:
            conn.execute("""INSERT INTO appnix.usage_counters(provider,period,period_start,reset_at)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (provider, period, start, end))

    def start_search(self, search_id, category, city, pages):
        with self.transaction() as conn:
            periods = self.periods(conn)
            row = conn.execute("""INSERT INTO appnix.search_history(id,category,city,requested_pages,day_start,month_start)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                (search_id, category, city, pages, periods[0][1], periods[1][1])).fetchone()
            if not row:
                return False
            self.ensure_counters(conn, "google_places", periods)
            for period, start, _ in periods:
                conn.execute("UPDATE appnix.usage_counters SET search_jobs=search_jobs+1 WHERE provider='google_places' AND period=%s AND period_start=%s", (period,start))
            return True

    def finish_search(self, search_id, status, error_category=None):
        if status not in {"success", "partial", "failed", "blocked"}:
            raise StorageError("Invalid search outcome.")
        category = error_category if error_category in {"quota", "provider", "network", "database", "invalid_response"} else None
        with self.transaction() as conn:
            conn.execute("UPDATE appnix.search_history SET status=%s,error_category=%s,ended_at=now() WHERE id=%s AND status='running'", (status, category, search_id))

    def reserve_request(self, provider, operation, search_id=None, attempt=1, monthly_cap=None):
        if provider not in PROVIDERS or operation not in {"text_search", "performance", "website_get", "website_head"}:
            raise StorageError("Invalid request metadata.")
        request_id = str(uuid.uuid4())
        with self.transaction() as conn:
            # Shared settings lock prevents limit changes racing with quota reservation.
            settings = conn.execute("SELECT daily_limit,monthly_limit FROM appnix.app_settings WHERE id=1 FOR SHARE").fetchone()
            if not settings:
                raise StorageError("Configure database settings before sending requests.")
            periods = self.periods(conn)
            self.ensure_counters(conn, provider, periods)
            for period, start, _ in periods:  # fixed lock order across processes
                counter = conn.execute("SELECT * FROM appnix.usage_counters WHERE provider=%s AND period=%s AND period_start=%s FOR UPDATE", (provider,period,start)).fetchone()
                limit = None
                if provider == "google_places":
                    limit = settings["daily_limit" if period == "day" else "monthly_limit"]
                    if period == "month" and monthly_cap is not None:
                        limit = min(limit, monthly_cap)
                    if counter["request_count"] + counter["pending_count"] >= limit:
                        raise QuotaReached(f"Business search {period} limit reached ({limit}). Review your saved limits before searching again.")
                conn.execute("UPDATE appnix.usage_counters SET pending_count=pending_count+1,configured_limit=%s WHERE provider=%s AND period=%s AND period_start=%s", (limit,provider,period,start))
            conn.execute("""INSERT INTO appnix.api_usage_logs(id,search_id,provider,operation,attempt,day_start,month_start)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (request_id,search_id,provider,operation,attempt,periods[0][1],periods[1][1]))
        return request_id

    def complete_request(self, request_id, outcome, http_status=None, leads=None):
        if outcome not in OUTCOMES:
            raise StorageError("Invalid request outcome.")
        with self.transaction() as conn:
            log = conn.execute("SELECT * FROM appnix.api_usage_logs WHERE id=%s FOR UPDATE", (request_id,)).fetchone()
            if not log:
                raise StorageError("Request reservation was not found.")
            if log["outcome"] != "pending":
                return  # idempotent retry after an uncertain commit
            good = outcome == "success"
            conn.execute("""UPDATE appnix.api_usage_logs SET outcome=%s,http_status=%s,success=%s,
                error_category=%s,request_units=1,completed_at=now() WHERE id=%s""",
                (outcome,http_status,good,None if good else outcome,request_id))
            for period, start in (("day",log["day_start"]),("month",log["month_start"])):
                conn.execute("""UPDATE appnix.usage_counters SET pending_count=pending_count-1,
                    request_count=request_count+1,successful_requests=successful_requests+%s,
                    failed_requests=failed_requests+%s,leads_returned=leads_returned+%s
                    WHERE provider=%s AND period=%s AND period_start=%s""",
                    (int(good),int(not good),len(leads or []),log["provider"],period,start))
            if leads:
                self.insert_leads(conn, leads)
                if log["search_id"]:
                    conn.execute("UPDATE appnix.search_history SET leads_returned=leads_returned+%s WHERE id=%s", (len(leads),log["search_id"]))
        safe_event("request_completed", provider=log["provider"], operation=log["operation"], outcome=outcome, http_status=http_status, request_id=request_id)

    def usage(self):
        with self.transaction() as conn:
            periods = self.periods(conn)
            return {period: conn.execute("SELECT * FROM appnix.usage_counters WHERE provider='google_places' AND period=%s AND period_start=%s", (period,start)).fetchone()
                    or {"request_count":0,"pending_count":0,"reset_at":end,"search_jobs":0,"leads_returned":0}
                    for period,start,end in periods}

    def recent_searches(self, limit=10):
        with self.transaction() as conn:
            return conn.execute("""SELECT s.*,count(l.id) FILTER (WHERE l.outcome <> 'pending') AS requests,
                count(l.id) FILTER (WHERE l.outcome = 'pending') AS pending_requests
                FROM appnix.search_history s LEFT JOIN appnix.api_usage_logs l ON l.search_id=s.id
                GROUP BY s.id ORDER BY s.started_at DESC LIMIT %s""", (min(max(limit,1),100),)).fetchall()
