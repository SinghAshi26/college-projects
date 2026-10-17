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

# Appnix Lead Finder

A streamlined lead generation, website auditing, and manual outreach platform designed for local businesses.

Built with **Python**, **Streamlit**, and **Supabase PostgreSQL**. One private shared workspace.

See [setup, migrations, security, usage semantics, and tests](docs/SETUP.md)
and the [pre-implementation audit](docs/AUDIT.md).

If lead search fails, the app displays Google's error and a suggested fix. In the
Google Cloud project that owns your key, enable **Places API (New)**, link an
active billing account, and allow Places API (New) in the key's API restrictions.
This app sends requests from its Python server, so application restrictions must
allow the server's public IP; website referrer restrictions are not suitable.
See Google's [setup guide](https://developers.google.com/maps/documentation/places/web-service/get-api-key)
and [key restriction guidance](https://developers.google.com/maps/api-security-best-practices).

Configure `SUPABASE_DB_URL` with verified TLS, `APP_PASSWORD`, and `GOOGLE_API_KEY`
in server secrets. Only the PostgreSQL URI is required for Supabase; Data API keys
are not used. Set `SEARCH_DAILY_LIMIT` explicitly; its default of zero pauses searches.
`SEARCH_MONTHLY_LIMIT` initially defaults to the former value of 900; this is not a
free-tier guarantee. Saved limits then take precedence over environment defaults.

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env securely, review the SQL migration, then use a migration-admin connection:
python migrate.py --apply
# Switch to the dedicated backend login described in docs/SETUP.md before starting:
python -m streamlit run app.py
```

Leads, audits, outreach updates, preferences, search history and usage persist in
PostgreSQL. Daily/monthly quota reservations are concurrency-safe. All five tabs,
bilingual templates, manual outreach links, table editing and CSV exports remain.
Database outages expose unsaved edits for retry/export and stop new search requests.

Run `python -m unittest discover -v`. Integration tests require an isolated local
`APPNIX_TEST_DB_URL`; otherwise those tests explicitly skip. Provider requests are
mocked and do not incur charges. Production Supabase setup is a separate manual step.
