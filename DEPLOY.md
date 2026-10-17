# Hugging Face Spaces & Production Deployment Guide

## 1. Supabase PostgreSQL Configuration

For serverless and containerized cloud platforms (such as Hugging Face Spaces or Streamlit Community Cloud), standard direct PostgreSQL connections may fail due to IPv6 routing restrictions or connection pooling exhaustion.

### Recommended Connection String
Configure `SUPABASE_DB_URL` using the **Supabase Session Pooler** (IPv4):
- **Port**: `6543` (Session Pooler)
- **SSL Mode**: `sslmode=require` (or `sslmode=verify-full` with root certificate)
- **Format**:
  ```text
  postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres?sslmode=require
  ```

### Key Requirements
1. **IPv4 Session Pooler (Port 6543)**: Hugging Face Spaces containers often lack native IPv6 egress. The pooler endpoint provides reliable IPv4 connectivity.
2. **Prepared Statement Handling**: Psycopg is configured with `prepare_threshold=None` in `storage.py`, ensuring compatibility with PgBouncer / Supabase transaction and session poolers on port 6543.
3. **Connection Failure Diagnostics**: If the database is unreachable at runtime, the application displays `Database unreachable (<host>:<port>)` and provides an interactive retry button. Database credentials are never printed or logged.

---

## 2. Secrets & Environment Variables

Configure the following secrets in your Hugging Face Space settings (**Settings > Variables and secrets**):

| Secret Name | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` | **Yes** | Google Cloud API key with **Places API (New)** enabled. Must NOT have IP or website referrer restrictions that block cloud server egress. |
| `SUPABASE_DB_URL` | **Yes** | PostgreSQL connection URI to Supabase (Session Pooler on port 6543 recommended). |
| `APP_PASSWORD` | **Yes** | Shared access password to authenticate operators into the private workspace. |
| `SEARCH_DAILY_LIMIT` | **Yes** | Positive integer (e.g. `100`). **Warning**: Defaulting to `0` pauses/blocks searches. |
| `SEARCH_MONTHLY_LIMIT` | Optional | Monthly hard cap (default: `900`). |
| `PSI_API_KEY` | Optional | Google PageSpeed Insights API key for mobile performance scoring. |

---

## 3. Deployment Artifacts & Synchronization

The repository root is configured as Space-ready with:
- `README.md` containing Hugging Face YAML metadata:
  ```yaml
  ---
  title: Appnix Lead Finder
  emoji: ◈
  colorFrom: blue
  colorTo: indigo
  sdk: streamlit
  sdk_version: 1.66.0
  python_version: 3.12
  app_file: app.py
  pinned: false
  ---
  ```
- `requirements.txt` with exact pinned versions.
- `.streamlit/config.toml` configuring server and theme settings.

To generate a standalone copy in `hf_space/` without drifting from the root:
```powershell
python make_hf_space.py
```
**Recommended Upload Target**: You can either push the repository root directly to your Hugging Face Space Git repository, or upload the contents of the generated `hf_space/` directory. Both are identical copies.
