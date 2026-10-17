# Setup and operating notes

## Configuration

Install with `python -m pip install -r requirements.txt`. Copy `.env.example` to
`.env` and edit locally, or configure deployment secrets / `.streamlit/secrets.toml`.
Streamlit secrets take precedence over environment variables, followed by `.env`.
`python-dotenv` reads the project-root `.env` on each configuration lookup; it
does not inject cached values into the process environment. A root `secrets.toml`
and `.env.example` are not automatically loaded. Blank deployment secret entries
also take precedence; remove stale entries if you intend to use `.env`.
CLI tools read environment / `.env`, not Streamlit secrets.

| Variable | Required use |
| --- | --- |
| `SUPABASE_DB_URL` | PostgreSQL URI for a dedicated backend login from Supabase **Connect**; session pooler for IPv4, or a reachable direct connection. Include `sslmode=verify-full`; URL-encode special characters in passwords. |
| `SUPABASE_SSL_ROOT_CERT` | Downloaded database CA certificate path, unless libpq already has the appropriate trusted certificate configured. |
| `APP_PASSWORD` | Long random shared-workspace password; required with database configuration. |
| `GOOGLE_API_KEY` | Server-side Places API (New) key. |
| `PSI_API_KEY` | Optional performance-insights key. |
| `SEARCH_DAILY_LIMIT` | Initial daily cap; `0` pauses search until explicitly configured. |
| `SEARCH_MONTHLY_LIMIT` | Initial monthly cap; default `900` preserves the former setting, not a billing guarantee. |

`SUPABASE_URL`, publishable keys, and secret/service-role Data API keys are not
required. Do not put credentials in the preferences table or browser widgets.
After this code update, stop the running app with **Ctrl+C** in its terminal, then
restart from the project folder:

```powershell
python -m streamlit run app.py
```

Later `.env` edits take effect with **Connections & Administration → Refresh
connection**. Restart after changing Streamlit secrets or process environment
variables; refresh does not restart the Python process. After initialization, saved limits
are authoritative; edit them under **Business Search Usage → Search limits**.

For a read-only check using local `.env` / process environment (the CLI does not
read Streamlit secrets), run:

```powershell
python diagnose_db.py
```

This validates the CA path and `sslmode=verify-full`, opens a read-only transaction,
checks the `001_persistence` migration marker and restricted backend role, and
prints only sanitized status. It never initializes settings or applies migrations.
Missing configuration prevents a connection attempt. An administrative URI is
rejected by this diagnostic; use the restricted backend login. A successful local
test or mocked test does not establish production connectivity.

Relative certificate paths resolve against the project folder. Explicit
`SUPABASE_SSL_ROOT_CERT` takes precedence over URI `sslrootcert`, then
`PGSSLROOTCERT`, then libpq's default certificate location. Windows paths can use
forward slashes, e.g. `C:/certificates/database-ca.crt`. The driver receives the
resolved path and uses hostname verification. A missing certificate, rejected
credentials, network/DNS error, TLS verification error, access denial, and missing
migration each have distinct sanitized guidance. No raw driver errors are shown.

Official references: [Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[verified TLS](https://supabase.com/docs/guides/platform/ssl-enforcement),
[Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).

## Migration and access control

Review `migrations/001_persistence.sql`. The app does not apply it automatically.
It creates a private `appnix` schema, six tables (five application tables plus
migration history), indexes, RLS policies, and the `appnix_backend` NOLOGIN group.
It contains no DROP/DELETE or changes to public tables and is idempotent. Inspect
any pre-existing `appnix` schema before applying; incompatible definitions need
a separate additive migration.

Temporarily configure an administrative migration connection in local `.env` or
the environment, then explicitly run:

```powershell
python migrate.py --apply
```

Alternatively run the reviewed SQL in Supabase SQL Editor. Create a dedicated
backend login through Supabase's administration tools, then grant it membership
in the application group and connection access. For a login named `appnix_app`:

```sql
GRANT appnix_backend TO appnix_app;
GRANT CONNECT ON DATABASE postgres TO appnix_app;
```

Configure the app with that login's URI, not the administrative/owner URI.
The group has SELECT/INSERT/UPDATE, but no DELETE or DDL. PUBLIC, `anon`, and
`authenticated` have no access. RLS permits only the backend group; owners can
bypass RLS for administration. Do not expose `appnix` through Supabase Data API.
All authenticated app operators share records and caps. This retains the existing
password gate; it is not per-user Supabase Auth or tenant isolation.

```powershell
python -m streamlit run app.py
```

## Data model and compatibility

- `leads`: stable UUID, unique source key, JSONB payload, timestamps. All 30 original
  fields and audit additions (including nested findings) survive round trips.
  Place IDs are uniquely indexed; missing IDs use a normalized business-identity
  hash. Repeat searches never overwrite edits, contact history, or audit results.
- `search_history`: category, city, requested pages, returned count, start/end,
  outcome, safe error category; actual requests are joined from the request log.
- `api_usage_logs`: provider/operation, attempt, search ID, timestamps, HTTP status,
  outcome, request units and nullable billable units. No URLs, payloads, headers,
  provider response bodies, or secrets.
- `usage_counters`: day/month period boundaries, request/pending/success/failure
  counts, jobs, returned leads, and configured cap snapshot.
- `app_settings`: allowlisted sender preferences, search caps, and tracking start.

Each retrieved search page commits its leads and request outcome together. Audit
results save as they complete. Inline edits use changed-field patches with
conflict detection, avoiding stale whole-row overwrites. Status, contact date,
outreach logs and preferences restore after refresh/restart. The in-memory audit
cache remains transient; persisted findings do not expire with it.

Existing live-session leads without internal IDs are imported on connection.
To add earlier CSV exports without overwriting saved records:

```powershell
python import_leads.py saved_leads.csv
```

Original CLI workflows are retained:

```powershell
python lead_finder.py "clinic" "Noida" --max-pages 2 --out leads.csv
python website_audit.py leads.csv --out audited.csv --psi
```

The audit CLI retains CSV output; app audits persist to lead records. Both track
HTTP requests through the database. CSV exports omit only internal UI identifiers.

## Accounting and recovery

One submission is a search job; each fetched page is a separate HTTP attempt.
Places search has no automatic provider retries. Website fetches retain one
bounded retry for network failures; each retry and redirect hop is recorded.
PageSpeed and website requests have separate counters and do not consume business
search quotas. SSL socket checks are not HTTP/API requests.

Before sending, a transaction locks both UTC period counters and reserves capacity
within the saved daily/monthly caps. All app processes share these locks. Limits
changed in another session are checked again at reservation. Confirmed attempts
include timeouts and connection failures; successful/failed requests are separate.
Completion is assigned to the reservation's period, even across midnight. A job
token rejects repeated execution of the same submission. Reruns do not count as
requests. PostgreSQL's UTC clock determines period/reset boundaries.

No transaction can atomically cover PostgreSQL and Google's HTTP server. A crash
or failed completion write can leave a `pending` reservation. Its network outcome
is unknown and it continues consuming capacity. It is not counted as a confirmed
request or silently released on restart. Interrupted jobs can remain `running` in
history. Administrators must investigate abandoned records instead of resetting
them blindly. Billable units stay NULL: request attempts do not prove invoices.
Provider billing controls and quotas remain independent safeguards.

Database outages stop further searches and show the last-read snapshot for export.
A fresh session cannot load records while storage is down. Failed edits and search
completions retained in a session can be retried explicitly; request completion IDs
make retries idempotent without another provider call. Unsaved changes offer a
recovery CSV. Keep the session open or download it before refreshing. Conflicting
edits require reload/reapplication; a discard control is provided. Database
connections, statements and lock waits have timeouts; there are no automatic
database retry loops or hidden provider retries.

`usage.json` is preserved as historical evidence. Its monthly aggregates cannot
reconstruct dates, request outcomes, daily totals or jobs. New counters start at
the displayed tracking start; old values are not fabricated into request logs.
Choose initial caps with prior provider usage in mind.

## Tests

```powershell
python -m unittest discover -v
```

Database/UI integration tests explicitly skip unless `APPNIX_TEST_DB_URL` is set.
Create a disposable loopback PostgreSQL database with a dedicated local test
administrator, then run:

```powershell
$env:APPNIX_TEST_DB_URL='postgresql://TEST_USER@127.0.0.1:55439/appnix_test_integration?sslmode=disable'
python -m unittest discover -v
```

Tests refuse remote hosts and names outside `appnix_test*`. They apply migrations
and truncate application tables only inside that isolated database. Never point
tests at production. External providers are mocked; there are no paid calls.
Production configuration cannot enable the test-only insecure loopback mode.

## Deployment limits

Production Supabase connection, existing remote schema, CA trust, login grants,
and actual provider authorization require manual setup and were not verified here.
The existing auditor follows website links; deploy with an outbound network policy
blocking private/internal addresses. Request accounting does not constitute a
complete SSRF sandbox. This is a shared trusted-operator application, not an
internet-facing multi-tenant product. No deployment or production migration is
performed by the UI or test suite.
