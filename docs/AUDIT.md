# Read-only audit, completed before implementation

| Area | Observed baseline |
| --- | --- |
| Entry points | `app.py` Streamlit; search/audit CLIs in `lead_finder.py` and `website_audit.py` |
| Dependencies | Streamlit, pandas, requests, BeautifulSoup; no database client |
| Search | Places New text search, IN region, 20 results/page, up to three POSTs; 30-second timeout, no retries; partial-error handling from the preceding fix |
| Accounting | Monthly JSON aggregate with memory fallback; non-atomic check/write; monthly default 900 and hardcoded “safe tier”; no daily cap, history, or request log |
| Lead data | 30 standard columns plus extra audit fields; session-state DataFrame; deduplication by Place ID |
| Audits | Four workers, session domain cache, one website retry, up to 15 link checks, optional PageSpeed; SSL, responsiveness, performance, SEO, email/social extraction and bilingual findings |
| Outreach | Five templates per language; sender mirror callbacks; manual WhatsApp/Gmail/mailto links; contact-date warnings and status/history updates in session |
| Export | DataFrame CSV download and preview; CLI CSV output; no UI CSV import |
| Configuration | Streamlit secrets then environment; password input fallbacks; optional app password; root `secrets.toml` present but not automatically loaded; only setting names inspected |
| Deployment | No database, migration, hosting manifest, theme config, or dotenv loading; secrets and `.env` already ignored |
| UI | All five tabs, metrics, filters/editor/inspector; CSS targeting Streamlit internals |

No Supabase DB URL was available. The remote schema could not be inspected. The
new private `appnix` schema avoids assuming the shape of existing public tables;
inspect any pre-existing schema with that name before applying migrations.

Plan: direct Psycopg/PostgreSQL transactions; JSONB records preserving every lead
field; private backend role with RLS; conservative quota reservations; per-page
and per-audit persistence; field-level updates; backend tests before a native
Streamlit theme and application-scoped CSS. Preserve the existing shared-workspace
model and require its password gate for database access. Credentials move out of
browser widgets to satisfy the explicit backend-only configuration requirement.
