"""
Appnix Lead Finder - Main Application
------------------------------------
Find local business leads via Google Places API (New), audit websites,
extract emails, and perform bilingual WhatsApp and Email outreach manually.
"""

import datetime
import hmac
import os
import uuid
from urllib.parse import quote

import pandas as pd
import streamlit as st

from lead_finder import PlacesSearchError, place_to_lead, search
from configuration import config_value, configured_store
from lead_model import CSV_COLUMNS, csv_export, dashboard_metrics, lead_frame, followups_due
from storage import DATABASE_DIAGNOSTICS, DatabaseNotConfigured, MigrationRequired, StorageError, clean_data
from safe_logging import safe_event
from templates import TEMPLATES, api_benefit, format_email, format_message
from ui import DATABASE_STATUS_MESSAGES, apply_custom_css, render_database_status, render_header, render_metrics, render_sidebar_brand
from website_audit import audit_batch

# CSV_COLUMNS remains available here for compatibility; canonical fields live in lead_model.

# Page configuration
st.set_page_config(
    page_title="Appnix Lead Finder",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Styles are scoped to our own markup; Streamlit components use the native theme.
apply_custom_css()


def secret(name: str, default: str = "") -> str:
    """Safely fetch sensitive credentials from st.secrets or environment variables."""
    try:
        val = st.secrets[name]
    except Exception:
        val = config_value(name, default)
    if isinstance(val, str):
        val = val.strip().strip("'\"")
    return val


# Password Gate
APP_PASSWORD = secret("APP_PASSWORD")
ALLOW_NO_PASSWORD = secret("ALLOW_NO_PASSWORD", "false").lower() in ("true", "1", "yes")

if secret("SUPABASE_DB_URL") and not APP_PASSWORD and not ALLOW_NO_PASSWORD:
    st.error("Configuration required: Set APP_PASSWORD in server secrets before opening this shared workspace. Database records are private.")
    st.stop()

if APP_PASSWORD:
    now_ts = datetime.datetime.now().timestamp()
    if st.session_state.get("authed", False):
        login_ts = st.session_state.get("auth_time", now_ts)
        if now_ts - login_ts > 86400:
            st.session_state["authed"] = False
            st.session_state.pop("auth_time", None)
            st.warning("Session expired. Please sign in again.")
            st.rerun()

    if not st.session_state.get("authed", False):
        lockout_until = st.session_state.get("lockout_until", 0)
        if now_ts < lockout_until:
            wait_sec = max(1, int(lockout_until - now_ts))
            st.error(f"Too many failed login attempts (5). Locked out for {wait_sec} seconds. Please wait.")
            st.stop()

        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        col_center = st.columns([1, 2, 1])[1]
        with col_center:
            st.subheader("🔒 Appnix Lead Finder")
            st.caption("Please enter your access password to continue.")
            with st.form("login_form"):
                entered_pw = st.text_input("Password", type="password", key="gate_password")
                submit_login = st.form_submit_button("Sign in", type="primary")
                if submit_login:
                    if entered_pw and hmac.compare_digest(entered_pw.encode(), APP_PASSWORD.encode()):
                        st.session_state["authed"] = True
                        st.session_state["auth_time"] = now_ts
                        st.session_state["failed_attempts"] = 0
                        st.session_state["lockout_until"] = 0
                        st.rerun()
                    else:
                        attempts = st.session_state.get("failed_attempts", 0) + 1
                        st.session_state["failed_attempts"] = attempts
                        if attempts >= 5:
                            st.session_state["lockout_until"] = now_ts + 60
                            st.error("Too many failed login attempts (5). Account locked for 60 seconds.")
                        else:
                            st.error(f"Incorrect password. Please try again. ({5 - attempts} attempts remaining)")
        st.stop()

# PostgreSQL is authoritative; session state holds only a last-read snapshot and pending edits.
store = None
database_ready = False
database_error = ""
database_state = "connection_failed"
database_diagnostic = ""
saved_settings = {}
usage_snapshot = None
recent_searches = []
try:
    store = configured_store(secret)
    store.check_connection()
    saved_settings = store.settings()
    legacy_leads = st.session_state.get("leads")
    if legacy_leads is not None and not legacy_leads.empty and "_id" not in legacy_leads.columns:
        store.save_new_leads(legacy_leads.to_dict("records"))
    persisted_records = store.load_leads()
    usage_snapshot = store.usage()
    recent_searches = store.recent_searches()
    st.session_state.leads = lead_frame(persisted_records)
    database_ready = True
    database_state = "connected"
except Exception as exc:
    # Do not forward exception strings: they may contain credentials or driver diagnostics.
    if isinstance(exc, DatabaseNotConfigured):
        database_state = "not_configured"
    elif isinstance(exc, MigrationRequired):
        database_state = "migration_required"
    database_diagnostic = getattr(exc, "category", "")
    safe_event("database_status", error_category=database_diagnostic if database_diagnostic in DATABASE_DIAGNOSTICS else database_state)
    target_info = ""
    db_url = secret("SUPABASE_DB_URL")
    if db_url:
        try:
            from psycopg.conninfo import conninfo_to_dict
            opts = conninfo_to_dict(db_url)
            h = opts.get("host")
            p = opts.get("port", "5432")
            if h:
                target_info = f" ({h}:{p})"
        except Exception:
            pass
    if database_state == "connection_failed":
        database_error = f"Database unreachable{target_info}. Check network connection, pooler access, credentials and retry."
    else:
        database_error = DATABASE_DIAGNOSTICS.get(database_diagnostic, DATABASE_STATUS_MESSAGES[database_state][1])

if "leads" not in st.session_state:
    st.session_state.leads = lead_frame([])


def persist_changes(before, after):
    """Save changed fields only; retain failed edits for explicit retry/export."""
    originals = {row["_id"]: row for row in before.to_dict("records") if row.get("_id")}
    changes = []
    for row in after.to_dict("records"):
        old = originals.get(row.get("_id"))
        if old is None:
            continue
        changeset = {key: clean_data(value) for key, value in row.items()
                     if not key.startswith("_") and key != "place_id"
                     and clean_data(value) != clean_data(old.get(key, ""))}
        if changeset:
            changes.append((row["_id"], old, changeset))
    if not changes:
        return True
    try:
        if not database_ready:
            raise StorageError("Database is unavailable. These changes have not been saved.")
        store.patch_leads(changes)
        st.session_state.leads = lead_frame(store.load_leads())
        return True
    except StorageError as exc:
        st.session_state["pending_changes"] = changes
        st.session_state["unsaved_leads"] = after
        st.error(str(exc))
        return False


def save_sender_preferences():
    preferences = {key: st.session_state.get(key, "") for key in ("sender_name", "company_name", "website_url")}
    try:
        if not database_ready:
            raise StorageError("Sender changes have not been saved. Reconnect the database and retry.")
        store.save_settings(preferences)
        st.session_state.pop("pending_preferences", None)
    except StorageError as exc:
        st.session_state["pending_preferences"] = preferences
        st.session_state["save_error"] = str(exc)


if database_ready and not st.session_state.get("settings_restored"):
    st.session_state.update(saved_settings.get("preferences", {}))
    st.session_state["settings_restored"] = True

if "audit_cache" not in st.session_state:
    st.session_state.audit_cache = {}

# Initialize Sender details in session state
if "sender_name" not in st.session_state:
    st.session_state["sender_name"] = "Jitendra"
if "company_name" not in st.session_state:
    st.session_state["company_name"] = "Appnix Technologies"
if "website_url" not in st.session_state:
    st.session_state["website_url"] = "appnix.org"

# Initialize mirror keys for tab expanders to prevent widget ID collisions
for pfx in ("wa_exp", "email_exp"):
    if f"{pfx}_sender_name" not in st.session_state:
        st.session_state[f"{pfx}_sender_name"] = st.session_state["sender_name"]
    if f"{pfx}_company_name" not in st.session_state:
        st.session_state[f"{pfx}_company_name"] = st.session_state["company_name"]
    if f"{pfx}_website_url" not in st.session_state:
        st.session_state[f"{pfx}_website_url"] = st.session_state["website_url"]


# Callbacks for seamless two-way synchronization between sidebar and tab expanders
def on_sidebar_sender_change():
    for pfx in ("wa_exp", "email_exp"):
        st.session_state[f"{pfx}_sender_name"] = st.session_state["sender_name"]
        st.session_state[f"{pfx}_company_name"] = st.session_state["company_name"]
        st.session_state[f"{pfx}_website_url"] = st.session_state["website_url"]
    save_sender_preferences()


def on_expander_sender_change(source_prefix: str):
    st.session_state["sender_name"] = st.session_state[f"{source_prefix}_sender_name"]
    st.session_state["company_name"] = st.session_state[f"{source_prefix}_company_name"]
    st.session_state["website_url"] = st.session_state[f"{source_prefix}_website_url"]
    other_prefix = "email_exp" if source_prefix == "wa_exp" else "wa_exp"
    st.session_state[f"{other_prefix}_sender_name"] = st.session_state["sender_name"]
    st.session_state[f"{other_prefix}_company_name"] = st.session_state["company_name"]
    st.session_state[f"{other_prefix}_website_url"] = st.session_state["website_url"]
    save_sender_preferences()


def get_full_sender() -> str:
    """Build single source of truth sender string from session state."""
    name = (st.session_state.get("sender_name") or "Jitendra").strip()
    company = (st.session_state.get("company_name") or "Appnix Technologies").strip()
    if name and company:
        return f"{name}, {company}"
    return name or company or "Appnix Technologies"


def get_website_link() -> str:
    """Build clean website link from session state."""
    raw_url = (st.session_state.get("website_url") or "appnix.org").strip()
    if not raw_url.startswith(("http://", "https://")):
        return f"https://{raw_url}"
    return raw_url


def render_sender_expander(prefix: str):
    """Safety net: renders sender details inside an expander at the top of outreach tabs."""
    with st.expander("👤 Sender Details (Quick Edit)", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.text_input(
            "Sender Name",
            key=f"{prefix}_sender_name",
            on_change=on_expander_sender_change,
            args=(prefix,),
        )
        c2.text_input(
            "Company Name",
            key=f"{prefix}_company_name",
            on_change=on_expander_sender_change,
            args=(prefix,),
        )
        c3.text_input(
            "Website URL",
            key=f"{prefix}_website_url",
            on_change=on_expander_sender_change,
            args=(prefix,),
        )


# Sidebar: Settings & API Quota
with st.sidebar:
    render_sidebar_brand()
    st.divider()
    st.subheader("Business Search Usage")
    hard_limit = int(saved_settings.get("monthly_limit", 900))
    daily_limit = int(saved_settings.get("daily_limit", 0))
    if database_ready:
        day, month = usage_snapshot["day"], usage_snapshot["month"]
        today_col, month_col = st.columns(2)
        today_col.metric("Today", day["request_count"])
        month_col.metric("This month", month["request_count"])
        remaining = max(0, min(daily_limit - day["request_count"] - day["pending_count"],
                               hard_limit - month["request_count"] - month["pending_count"]))
        st.caption(f"{remaining:,} requests remaining within both limits")
        st.progress(min(1.0, (month["request_count"] + month["pending_count"]) / hard_limit) if hard_limit else 1.0,
                    text=f"Monthly maximum · {hard_limit:,}")
        st.caption(f"Daily maximum · {daily_limit:,} · Resets at 00:00 UTC")
        if month["pending_count"]:
            st.warning(f"{month['pending_count']} request(s) are in flight or unconfirmed. These still consume capacity.")
        with st.expander("Search limits"):
            with st.form("search_limits"):
                new_daily = st.number_input("Daily request maximum", min_value=0, value=daily_limit, step=1)
                new_monthly = st.number_input("Monthly request maximum", min_value=0, value=hard_limit, step=1,
                                              help="Monthly request maximum. Zero pauses searches. Provider billing limits remain separate.")
                if st.form_submit_button("Save limits"):
                    try:
                        store.save_settings(daily=int(new_daily), monthly=int(new_monthly))
                        st.rerun()
                    except StorageError as exc:
                        safe_event("settings_save_failed", error_category="database")
                        st.error("Limits were not confirmed saved. Check the connection and try again.")
        if recent_searches:
            with st.expander("Recent search activity"):
                for activity in recent_searches[:3]:
                    st.write(f"{activity['category']} · {activity['city']}")
                    st.caption(f"{activity['status'].title()} · {activity['requests']} requests · {activity['leads_returned']} leads")
        with st.expander("About usage"):
            st.caption(f"Tracking since {saved_settings['tracking_started_at']:%d %b %Y, %H:%M} UTC. Earlier requests are not reconstructed.")
            st.caption("Each search page uses one request. Provider billing and quotas are separate safeguards.")
    else:
        st.caption("Usage unavailable. Connect storage to enable tracked searches.")

    st.divider()
    with st.expander("Sender Details", expanded=False):
        st.text_input(
            "Sender Name", key="sender_name", on_change=on_sidebar_sender_change,
            help="Your personal or contact name",
        )
        st.text_input(
            "Company Name", key="company_name", on_change=on_sidebar_sender_change,
            help="Your agency or company name",
        )
        st.text_input(
            "Website URL", key="website_url", on_change=on_sidebar_sender_change,
            help="Domain included in email signatures",
        )

    with st.expander("Connections & Administration", expanded=not database_ready):
        st.caption("DATABASE")
        render_database_status(database_state, database_diagnostic)
        if st.button("Refresh connection", width="stretch"):
            st.rerun()
        st.divider()
        st.caption("SYSTEM HEALTH")
        g_status = "set (length OK)" if secret("GOOGLE_API_KEY") else "not set"
        db_status = "set (length OK)" if secret("SUPABASE_DB_URL") else "not set"
        pw_status = "set (length OK)" if secret("APP_PASSWORD") else "not set"
        psi_status = "set (length OK)" if secret("PSI_API_KEY") else "not set"
        st.caption(f"GOOGLE_API_KEY: {g_status}")
        st.caption(f"SUPABASE_DB_URL: {db_status}")
        st.caption(f"APP_PASSWORD: {pw_status}")
        st.caption(f"PSI_API_KEY: {psi_status}")
        st.caption(f"Schema: appnix · Migration: 001_persistence")
        if database_ready:
            st.caption(f"Usage: {usage_snapshot['day']['request_count']}/{daily_limit} today · {usage_snapshot['month']['request_count']}/{hard_limit} month")
        if st.button("Test Google key", key="test_google_key_sidebar"):
            test_k = secret("GOOGLE_API_KEY")
            if not test_k:
                st.warning("GOOGLE_API_KEY is not configured.")
            else:
                try:
                    search("test", test_k, 1, store=None, category="test", city="test")
                    st.success("Google Places API key is valid and active!")
                except PlacesSearchError as exc:
                    if "API key" in str(exc) or "INVALID_ARGUMENT" in str(exc) or "rejected" in str(exc):
                        st.error("Google rejected the API key: Replace GOOGLE_API_KEY in environment or .env with an active key.")
                    else:
                        st.info(f"Key test result: {exc}")
                except Exception:
                    st.error("Key test failed.")
        st.divider()
        st.caption("SEARCH & INSIGHTS")
        api_key = secret("GOOGLE_API_KEY").strip()
        psi_key = secret("PSI_API_KEY").strip()
        st.caption("Business search configured" if api_key else "Set GOOGLE_API_KEY in server secrets to enable business search.")
        st.caption("Performance insights configured" if psi_key else "Optional: configure PSI_API_KEY for performance insights.")
        st.caption("Search provider: Google Maps Places API (New). Provider billing and quotas are independent safeguards.")
        st.caption("One private shared workspace. All signed-in operators share leads and limits.")

    if st.session_state.get("authed", False):
        st.divider()
        if st.button("🚪 Log out", key="logout_sidebar"):
            st.session_state.clear()
            st.rerun()


# Render main app header with header logout button
col_header_text, col_header_logout = st.columns([6, 1])
with col_header_text:
    render_header(
        title="Appnix Lead Finder",
        subtitle="Discover your next client. Understand their business. Start a better conversation.",
    )
with col_header_logout:
    if st.session_state.get("authed", False):
        st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)
        if st.button("🚪 Log out", key="logout_header"):
            st.session_state.clear()
            st.rerun()

if database_error:
    st.warning(database_error)
    if not database_ready and st.button("🔄 Retry database connection", key="retry_db_btn"):
        st.rerun()
    if not st.session_state.leads.empty:
        st.caption("Showing the last saved snapshot. It may be out of date; exports remain available.")
if st.session_state.get("save_error"):
    st.error(st.session_state.pop("save_error"))
if any(st.session_state.get(k) is not None for k in ("pending_changes", "pending_preferences", "pending_completion")):
    st.warning("Some changes are not confirmed saved. Keep this session open to retry or download a recovery copy.")
    if st.button("Retry unsaved changes", disabled=not database_ready):
        try:
            if st.session_state.get("pending_completion"):
                store.complete_request(**st.session_state["pending_completion"])
                del st.session_state["pending_completion"]
            if st.session_state.get("pending_changes"):
                store.patch_leads(st.session_state["pending_changes"])
                del st.session_state["pending_changes"]
            if st.session_state.get("pending_preferences"):
                store.save_settings(st.session_state["pending_preferences"])
                del st.session_state["pending_preferences"]
            st.session_state.pop("unsaved_leads", None)
            st.rerun()
        except StorageError as exc:
            st.error(str(exc))
if "unsaved_leads" in st.session_state:
    st.download_button("Download unsaved recovery CSV", csv_export(st.session_state["unsaved_leads"]), "appnix_unsaved.csv", "text/csv")
    if st.session_state.get("pending_changes") and st.button("Discard unsaved edits and reload"):
        st.session_state.pop("pending_changes", None)
        st.session_state.pop("unsaved_leads", None)
        st.rerun()

df = st.session_state.leads

# Ensure all expected columns exist in df
for col in CSV_COLUMNS:
    if col not in df.columns:
        df[col] = ""
st.session_state.leads = df

# Calculate summary metrics
if database_ready or not df.empty:
    render_metrics(**dashboard_metrics(df))
else:
    st.info("Connect storage to load saved leads and dashboard totals.")

st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

# Main Application Tabs
tab_dash, tab_find, tab_leads, tab_outreach, tab_email, tab_activity = st.tabs(
    ["Dashboard", "Find Leads", "Leads", "Outreach", "Email", "Activity"]
)

# -------------------- TAB 1: DASHBOARD --------------------
with tab_dash:
    st.subheader("Workspace Overview")
    st.caption("Live pipeline overview, priority follow-ups, and recent activity.")

    due_today_df = followups_due(df)
    render_metrics(
        **dashboard_metrics(df),
        followups_due=len(due_today_df),
    )

    col_d1, col_d2 = st.columns([1, 1])
    with col_d1:
        st.markdown("#### 📅 Follow-ups Due Today")
        if due_today_df.empty:
            st.info("No follow-ups due today. You are fully caught up!")
        else:
            st.warning(f"🔔 {len(due_today_df)} follow-up(s) scheduled for today!")
            disp_cols = [c for c in ["name", "phone", "call_status", "call_notes", "status"] if c in due_today_df.columns]
            st.dataframe(due_today_df[disp_cols], width="stretch", hide_index=True)

    with col_d2:
        st.markdown("#### 🔍 Recent Searches")
        if recent_searches:
            st.dataframe(
                pd.DataFrame(recent_searches)[["category", "city", "requested_pages", "leads_returned", "requests", "status"]],
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("No recent searches recorded yet.")

    st.markdown("---")
    st.markdown("#### Pipeline Status Breakdown")
    if not df.empty:
        status_counts = df["status"].value_counts().to_dict()
    else:
        status_counts = {}
    st_cols = st.columns(5)
    st_cols[0].markdown(f"**New:** `{status_counts.get('New', 0)}`")
    st_cols[1].markdown(f"**Contacted:** `{status_counts.get('Contacted', 0)}`")
    st_cols[2].markdown(f"**Meeting:** `{status_counts.get('Meeting', 0)}`")
    st_cols[3].markdown(f"**Closed:** `{status_counts.get('Closed', 0)}`")
    st_cols[4].markdown(f"**Not Interested:** `{status_counts.get('Not interested', 0)}`")


# -------------------- TAB 2: FIND LEADS --------------------
with tab_find:
    st.subheader("Find your next opportunity")
    st.caption("Search local businesses by category and area. Each page uses one search request and returns up to 20 leads.")
    search_warning = st.session_state.pop("search_warning", None)
    if search_warning:
        st.warning(search_warning)
    if st.session_state.get("search_success"):
        st.success(st.session_state.pop("search_success"))
    if "search_submission_id" not in st.session_state:
        st.session_state["search_submission_id"] = str(uuid.uuid4())
    with st.form("lead_search"):
        c1, c2, c3 = st.columns([3, 3, 1])
        category_in = c1.text_input("Business Category", placeholder="Dental clinic, coaching center, restaurant")
        city_in = c2.text_input("City or Neighborhood", placeholder="Lucknow Gomti Nagar, Noida Sector 62")
        pages_in = c3.selectbox("Pages", [1, 2, 3], index=2, help="Up to 20 businesses per page")
        submitted = st.form_submit_button("Search Leads", type="primary",
            disabled=not database_ready or st.session_state.get("search_running", False)
                     or bool(st.session_state.get("pending_completion")))
    if submitted:
        if not api_key:
            st.error("Business search is not configured. Set GOOGLE_API_KEY in server secrets.")
        elif not category_in.strip() or not city_in.strip():
            st.error("Please provide both a business category and a target city/area.")
        else:
            st.session_state["active_search"] = (category_in.strip(), city_in.strip(), pages_in)
            st.session_state["search_running"] = True
            st.rerun()

    if st.session_state.get("active_search"):
        category_in, city_in, pages_in = st.session_state["active_search"]
        search_progress = st.progress(0.0, text="Preparing your search...")
        places = None
        try:
            with st.spinner("Finding businesses and saving your leads..."):
                places = search(
                    f"{category_in.strip()} in {city_in.strip()}", api_key, pages_in,
                    usage_limit=hard_limit, store=store, category=category_in.strip(), city=city_in.strip(),
                    search_id=st.session_state["search_submission_id"],
                    progress_callback=lambda page, total, count: search_progress.progress(
                        page / total, text=f"Page {page}/{total} · {count} leads retrieved and saved"),
                )
            if places:
                st.session_state["search_success"] = f"Retrieved {len(places)} leads. Saved businesses are ready below."
            else:
                st.session_state["search_warning"] = "No matching listings. Try a broader business category or nearby city/area."
        except PlacesSearchError as exc:
            st.session_state["search_warning"] = str(exc)
            if exc.partial_results:
                st.session_state["search_warning"] = f"Search was incomplete. Retrieved {len(exc.partial_results)} leads. {exc}"
            if exc.pending_completion:
                st.session_state["pending_completion"] = exc.pending_completion
                st.session_state["unsaved_leads"] = lead_frame(
                    [place_to_lead(p, category_in.strip(), city_in.strip()) for p in exc.partial_results])
        finally:
            st.session_state["search_running"] = False
            st.session_state.pop("active_search", None)
            st.session_state["search_submission_id"] = str(uuid.uuid4())
            search_progress.empty()
        st.rerun()

    if recent_searches:
        with st.expander("Search history"):
            history = pd.DataFrame(recent_searches)
            st.dataframe(history[["category", "city", "requested_pages", "leads_returned", "requests", "pending_requests", "status", "started_at"]],
                         hide_index=True, width="stretch")

    st.markdown("---")
    st.subheader("Current Leads")
    if df.empty:
        st.info("No leads found yet. Enter a business type and area above to start searching.")
    else:
        list_query = st.text_input("Find in saved leads", placeholder="Business name, category, or area")
        listed = df
        if list_query.strip():
            listed = df[df[["name", "category", "address"]].astype(str).apply(
                lambda column: column.str.contains(list_query.strip(), case=False, regex=False)).any(axis=1)]
        st.caption(f"{len(listed):,} businesses · Sort by selecting a column heading")
        st.dataframe(
            listed[["name", "category", "phone", "website", "lead_type", "rating", "reviews", "status"]],
            width="stretch",
            hide_index=True,
            column_config={"name": st.column_config.TextColumn("Business", width="medium"),
                           "website": st.column_config.LinkColumn("Website"),
                           "status": st.column_config.TextColumn("Status"),
                           "rating": st.column_config.NumberColumn("Rating", format="%.1f")},
        )


# -------------------- TAB 3: LEADS --------------------
with tab_leads:
    st.subheader("Audited Leads Management")
    if df.empty:
        st.info("No leads yet. Search a business type and area to begin.")
    else:
        st.write(
            "Performs deep audit checks: SSL certificate expiry, mobile viewport, internal broken links (up to 15), "
            "page speed, HTML basics, and email extraction (plain-text, obfuscated, and JSON-LD schema)."
        )

        col_audit_btn, col_stop_btn, col_cache_info = st.columns([2, 1, 3])
        cached_count = len(st.session_state.audit_cache)
        if col_audit_btn.button("Audit Websites & Find Emails", type="primary", disabled=not database_ready or bool(st.session_state.get("pending_changes"))):
            st.session_state["stop_audit"] = False
            progress_bar = st.progress(0.0, text="Reviewing business websites…")

            def on_progress(done: int, total: int, current_name: str) -> None:
                progress_bar.progress(done / total, text=f"Audited {done}/{total}: {current_name}")

            leads_list = df.to_dict(orient="records")
            try:
                def save_audit_result(record):
                    original = df[df["_id"] == record["_id"]]
                    if not persist_changes(original, lead_frame([record])):
                        raise StorageError("An audit result could not be saved. Retry pending changes.")

                audited_records = audit_batch(
                    leads=leads_list, use_psi=bool(psi_key), psi_key=psi_key, max_workers=4,
                    progress_callback=on_progress, cache=st.session_state.audit_cache,
                    store=store, result_callback=save_audit_result,
                )
                if persist_changes(df, lead_frame(audited_records)):
                    st.session_state["search_success"] = "Website audits completed and saved."
                    st.rerun()
            except StorageError as exc:
                st.error(str(exc))
            finally:
                progress_bar.empty()

        if col_stop_btn.button("⏹️ Stop", key="stop_audit_btn"):
            st.session_state["stop_audit"] = True
            st.info("Audit stop requested.")

        col_cache_info.caption(f"{cached_count} websites cached in this session. Saved audit findings remain available after reload.")

        st.markdown("---")

        # Table filter controls (search by name, phone, address, or email)
        f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([2.5, 1.8, 1.8, 1.8, 1.2])
        search_query = f_col1.text_input("Search Leads", placeholder="Search name, phone, email...")
        lead_type_filter = f_col2.selectbox("Lead Type", ["All", "Hot leads only", "Has website"])
        status_filter = f_col3.selectbox(
            "Status",
            ["All", "New", "Contacted", "Replied", "Meeting", "Closed", "Not interested"],
        )
        due_today_only = f_col4.checkbox("Follow-ups due today", value=False)
        has_email_only = f_col5.checkbox("Has Email", value=False)

        # Apply table filters
        filtered_df = df.copy()
        if search_query.strip():
            q = search_query.strip().lower()
            filtered_df = filtered_df[
                filtered_df["name"].astype(str).str.lower().str.contains(q, regex=False)
                | filtered_df["phone"].astype(str).str.lower().str.contains(q, regex=False)
                | filtered_df["address"].astype(str).str.lower().str.contains(q, regex=False)
                | filtered_df["email"].astype(str).str.lower().str.contains(q, regex=False)
            ]
        if lead_type_filter == "Hot leads only":
            filtered_df = filtered_df[filtered_df["lead_type"].str.startswith("HOT")]
        elif lead_type_filter == "Has website":
            filtered_df = filtered_df[~filtered_df["lead_type"].str.startswith("HOT")]
        if status_filter != "All":
            filtered_df = filtered_df[filtered_df["status"] == status_filter]
        if due_today_only:
            today_iso = datetime.date.today().isoformat()
            filtered_df = filtered_df[filtered_df["followup_date"].astype(str).str.strip() == today_iso]
        if has_email_only:
            filtered_df = filtered_df[filtered_df["email"].fillna("").astype(str).str.strip() != ""]

        # Share filtered indices with Outreach and Email tabs (ISS-13)
        st.session_state["filtered_lead_indices"] = filtered_df.index.tolist()

        status_options = ["New", "Contacted", "Replied", "Meeting", "Closed", "Not interested"]

        disabled_cols = True if not database_ready or st.session_state.get("pending_changes") else ["_id", "place_id", "lead_type", "maps_link", "audit_score", "broken_links", "audit_time", "findings"]
        edited_data = st.data_editor(
            filtered_df,
            key="lead_editor",
            width="stretch",
            hide_index=True,
            column_config={
                "website": st.column_config.LinkColumn("Website"),
                "maps_link": st.column_config.LinkColumn("Google Maps"),
                "status": st.column_config.SelectboxColumn("Status", options=status_options),
                "call_status": st.column_config.SelectboxColumn("Call Status", options=["Not called", "Interested", "Follow-up", "Busy", "Not interested", "Closed"]),
                "followup_date": st.column_config.TextColumn("Follow-up (YYYY-MM-DD)"),
                "call_notes": st.column_config.TextColumn("Call Notes"),
                "audit_score": st.column_config.NumberColumn("Score (0-100)"),
                "ssl_days_left": st.column_config.TextColumn("SSL Days"),
                "broken_links": st.column_config.NumberColumn("Broken Links"),
                "email_confidence": st.column_config.TextColumn("Email Confidence"),
                "_id": None,
                "findings": None,
            },
            disabled=disabled_cols,
        )

        # Synchronize inline edits using edited_rows change detection (ISS-12)
        editor_state = st.session_state.get("lead_editor", {})
        edited_rows = editor_state.get("edited_rows", {})
        if edited_rows:
            updated = df.copy(deep=True)
            today_str = datetime.date.today().isoformat()
            has_real_diff = False
            for row_idx, changes in edited_rows.items():
                if isinstance(row_idx, int) and row_idx < len(filtered_df):
                    actual_idx = filtered_df.index[row_idx]
                else:
                    actual_idx = row_idx
                for col_name, new_val in changes.items():
                    if clean_data(new_val) != clean_data(df.at[actual_idx, col_name]):
                        has_real_diff = True
                        updated.at[actual_idx, col_name] = new_val
                        if col_name == "status" and new_val == "Contacted" and not df.at[actual_idx, "contacted_date"]:
                            updated.at[actual_idx, "contacted_date"] = today_str
            if has_real_diff and persist_changes(df, updated):
                st.rerun()

        # Quick diagnostic inspector
        if not df.empty:
            st.markdown("#### Diagnostic Findings Inspector")
            selected_insp = st.selectbox("Select Lead to Inspect Findings", df.index.tolist(),
                                        format_func=lambda i: f"{df.at[i, 'name'] or 'Unnamed business'} · {df.at[i, 'address']}", key="inspect_lead_sel")
            inspect_row = df.loc[selected_insp]

            col_i1, col_i2, col_i3, col_i4 = st.columns(4)
            col_i1.metric("Audit Score", f"{inspect_row.get('audit_score', 0)}/100")
            col_i2.metric("SSL Days Left", str(inspect_row.get("ssl_days_left", "N/A")))
            col_i3.metric("Broken Links", str(inspect_row.get("broken_links", 0)))
            col_i4.metric("PageSpeed Mobile", str(inspect_row.get("psi_mobile", "N/A")))

            with st.expander("View Full Diagnostic Summary & Email Sources"):
                st.markdown(f"**English Issue:** {inspect_row.get('problem_en') or 'None detected'}")
                st.markdown(f"**Hinglish Issue:** {inspect_row.get('problem_hi') or 'None detected'}")
                st.markdown(f"**Discovered Email:** `{inspect_row.get('email') or 'None'}` (Source: {inspect_row.get('email_source') or 'N/A'}, Confidence: {inspect_row.get('email_confidence') or 'N/A'})")
                if inspect_row.get("email_guess"):
                    st.markdown(f"**Unverified Email Guess:** `{inspect_row.get('email_guess')}`")
                if inspect_row.get("outreach_log"):
                    st.markdown(f"**Outreach History:** `{inspect_row.get('outreach_log')}`")

        st.markdown("---")
        csv_bytes = csv_export(df)
        st.download_button(
            label="⬇️ Download Leads CSV",
            data=csv_bytes,
            file_name=f"appnix_leads_{datetime.date.today().isoformat()}.csv",
            mime="text/csv",
            type="primary",
        )


# -------------------- TAB 4: OUTREACH --------------------
with tab_outreach:
    st.subheader("3. Outreach & WhatsApp Generator")

    render_sender_expander("wa_exp")

    if df.empty:
        st.info("No leads yet. Search a business type and area to begin.")
    else:
        today_iso = datetime.date.today().isoformat()
        contacted_today = int((df["contacted_date"] == today_iso).sum())

        metric_col, warning_col = st.columns([1, 2])
        metric_col.metric("Daily Outreach Counter", f"{contacted_today} / 25 sent today")

        if contacted_today >= 25:
            warning_col.warning(
                "⚠️ Sending too many cold WhatsApp messages in a day can get your number blocked. "
                "We recommend pacing yourself to 20-25 messages per day."
            )
        else:
            warning_col.info(
                "Maintain a safe sending pace (under 25 conversations per day) to preserve phone number reputation."
            )

        st.markdown("---")

        target_indices = [i for i in st.session_state.get("filtered_lead_indices", df.index.tolist()) if i in df.index]
        if not target_indices:
            target_indices = df.index.tolist()
        selected_lead_name = st.selectbox(
            "Select Target Lead",
            target_indices,
            format_func=lambda i: f"{df.at[i, 'name'] or 'Unnamed business'} · {df.at[i, 'address']}",
            key="wa_lead_sel",
        )
        lead_row = df.loc[selected_lead_name]
        lead_id = str(lead_row.get("_id") or lead_row.get("place_id") or selected_lead_name)

        last_contact = str(lead_row.get("contacted_date", "")).strip()
        is_recently_contacted = False
        if last_contact:
            try:
                last_dt = datetime.date.fromisoformat(last_contact)
                days_diff = (datetime.date.today() - last_dt).days
                if days_diff < 3:
                    is_recently_contacted = True
                    st.warning(
                        f"⚠️ Notice: This lead was already contacted on {last_contact} ({days_diff} day(s) ago). "
                        "Contacting them again so soon may seem repetitive."
                    )
            except Exception:
                pass

        has_site = bool(lead_row.get("website")) and not str(lead_row.get("lead_type")).startswith("HOT")
        prob_en = (lead_row.get("problem_en") or lead_row.get("problem") or "").strip()
        prob_hi = (lead_row.get("problem_hi") or "").strip()

        if not has_site:
            default_tpl = "No website"
        elif not prob_en or prob_en.startswith("Site looks healthy"):
            default_tpl = "WhatsApp API"
        else:
            default_tpl = "Website redesign"

        lang_col, tpl_col = st.columns([1, 2])
        selected_lang = lang_col.radio("Language", ["English", "Hinglish"], horizontal=True, key="wa_lang_radio")

        template_choices = list(TEMPLATES[selected_lang].keys())
        default_index = template_choices.index(default_tpl) if default_tpl in template_choices else 0
        selected_template = tpl_col.selectbox(
            f"Template (Auto-selected: {default_tpl})",
            template_choices,
            index=default_index,
            key=f"tpl_select_{lead_id}",
        )

        problem_text = prob_hi if selected_lang == "Hinglish" else prob_en
        if not problem_text:
            problem_text = (
                "kuch technical improvements ki gunjaish hai"
                if selected_lang == "Hinglish"
                else "minor technical improvements are needed"
            )

        benefit_text = api_benefit(lead_row.get("category", ""), language=selected_lang)
        full_sender = get_full_sender()

        raw_template = TEMPLATES[selected_lang][selected_template]
        initial_message = format_message(
            raw_template,
            name=lead_row["name"],
            sender=full_sender,
            problem=problem_text,
            benefit=benefit_text,
        )

        st.caption("Note: This message is a starting point. Review and personalize the details before sending.")
        # Stable widget key for WhatsApp message (ISS-07)
        wa_key = f"wa_msg_{lead_id}"
        message_body = st.text_area(
            "Message Content",
            value=initial_message,
            height=200,
            key=wa_key,
        )

        char_count = len(message_body)
        word_count = len(message_body.split())
        st.markdown(
            f"<div class='meta-hint'>Length: <strong>{char_count}</strong> characters | "
            f"<strong>{word_count}</strong> words</div>",
            unsafe_allow_html=True,
        )

        st.markdown("#### Message Preview & Quick Copy")
        st.code(message_body, language=None)

        phone_raw = str(lead_row.get("phone", ""))
        digits = "".join(ch for ch in phone_raw if ch.isdigit())

        btn_col1, btn_col2 = st.columns([1, 1])
        if len(digits) >= 10:
            formatted_phone = "91" + digits[-10:]
            wa_link = f"https://wa.me/{formatted_phone}?text={quote(message_body)}"
            btn_col1.link_button("📲 Open WhatsApp", wa_link, type="primary")
        else:
            btn_col1.info("No valid phone number available for this business.")

        btn_label = "Mark Contacted Anyway" if is_recently_contacted else "Mark as Contacted Today"
        if btn_col2.button(btn_label, key=f"btn_contact_{lead_id}", disabled=not database_ready or bool(st.session_state.get("pending_changes"))):
            updated = df.copy(deep=True)
            lead_idx = selected_lead_name
            updated.at[lead_idx, "status"] = "Contacted"
            updated.at[lead_idx, "contacted_date"] = today_iso
            current_log = str(updated.at[lead_idx, "outreach_log"] or "")
            entry = f"[{today_iso} | WhatsApp | {selected_template}]"
            updated.at[lead_idx, "outreach_log"] = f"{current_log}; {entry}" if current_log else entry
            if persist_changes(df, updated):
                st.toast(f"Marked '{lead_row['name']}' as Contacted via WhatsApp!")
                st.rerun()

        # Call Tracker: Log Phone Call Form
        st.markdown("---")
        st.markdown("#### 📞 Log Phone Call")
        with st.form(f"call_log_form_{lead_id}"):
            cc1, cc2 = st.columns([1, 1])
            c_status = cc1.selectbox("Call Status", ["Interested", "Follow-up", "Busy / Callback", "No Answer", "Wrong Number", "Not Interested", "Closed / Won"])
            c_date = cc2.date_input("Follow-up Date (optional)", value=None)
            c_notes = st.text_area("Call Notes", placeholder="Spoke with owner, discussed website needs...", height=80)
            if st.form_submit_button("Save Call Outcome"):
                updated = df.copy(deep=True)
                lead_idx = selected_lead_name
                updated.at[lead_idx, "call_status"] = c_status
                updated.at[lead_idx, "call_notes"] = c_notes
                if c_date:
                    updated.at[lead_idx, "followup_date"] = c_date.isoformat()
                today_call = datetime.date.today().isoformat()
                cur_log = str(updated.at[lead_idx, "outreach_log"] or "")
                entry = f"[{today_call} | Call | {c_status}]: {c_notes}"
                updated.at[lead_idx, "outreach_log"] = f"{cur_log}; {entry}" if cur_log else entry
                if c_status in ("Interested", "Closed / Won"):
                    updated.at[lead_idx, "status"] = "Meeting" if c_status == "Interested" else "Closed"
                if persist_changes(df, updated):
                    st.toast("Call log saved successfully!")
                    st.rerun()


# -------------------- TAB 5: EMAIL --------------------
with tab_email:
    st.subheader("4. Email Outreach Generator")

    render_sender_expander("email_exp")

    st.warning(
        "⚠️ Best Practice: Send manually from your verified business domain, keep volume to 20-30 emails/day max, "
        "and ensure SPF, DKIM, and DMARC are properly configured on your domain to prevent spam placement."
    )

    if df.empty:
        st.info("No leads yet. Search a business type and area to begin.")
    else:
        target_indices = [i for i in st.session_state.get("filtered_lead_indices", df.index.tolist()) if i in df.index]
        if not target_indices:
            target_indices = df.index.tolist()
        selected_email_lead = st.selectbox(
            "Select Target Lead for Email",
            target_indices,
            format_func=lambda i: f"{df.at[i, 'name'] or 'Unnamed business'} · {df.at[i, 'address']}",
            key="email_lead_sel",
        )
        lead_row = df.loc[selected_email_lead]
        lead_id = str(lead_row.get("_id") or lead_row.get("place_id") or selected_email_lead)

        last_contact = str(lead_row.get("contacted_date", "")).strip()
        is_recently_contacted = False
        if last_contact:
            try:
                last_dt = datetime.date.fromisoformat(last_contact)
                days_diff = (datetime.date.today() - last_dt).days
                if days_diff < 3:
                    is_recently_contacted = True
                    st.warning(
                        f"⚠️ Notice: This lead was contacted recently on {last_contact} ({days_diff} day(s) ago). "
                        "Avoid contacting them too frequently to maintain professional relations."
                    )
            except Exception:
                pass

        has_site = bool(lead_row.get("website")) and not str(lead_row.get("lead_type")).startswith("HOT")
        prob_en = (lead_row.get("problem_en") or lead_row.get("problem") or "").strip()
        prob_hi = (lead_row.get("problem_hi") or "").strip()

        if not has_site:
            default_email_tpl = "No website"
        elif not prob_en or prob_en.startswith("Site looks healthy"):
            default_email_tpl = "WhatsApp API"
        else:
            default_email_tpl = "Website audit findings"

        email_lang_col, email_tpl_col = st.columns([1, 2])
        selected_email_lang = email_lang_col.radio(
            "Language", ["English", "Hinglish"], horizontal=True, key="email_lang_radio"
        )

        email_tpl_options = [
            "No website",
            "Website audit findings",
            "WhatsApp API",
            "Follow-up (after 3 days)",
            "Final nudge (after 7 days)",
        ]
        default_tpl_idx = email_tpl_options.index(default_email_tpl) if default_email_tpl in email_tpl_options else 0
        selected_email_tpl = email_tpl_col.selectbox(
            f"Template (Auto-selected: {default_email_tpl})",
            email_tpl_options,
            index=default_tpl_idx,
            key=f"email_tpl_sel_{lead_id}",
        )

        problem_text = prob_hi if selected_email_lang == "Hinglish" else prob_en
        if not problem_text:
            problem_text = (
                "kuch technical improvements ki gunjaish hai"
                if selected_email_lang == "Hinglish"
                else "minor technical improvements are needed"
            )

        benefit_text = api_benefit(lead_row.get("category", ""), language=selected_email_lang)
        full_sender = get_full_sender()
        website_link = get_website_link()

        initial_sub, initial_body = format_email(
            language=selected_email_lang,
            template_name=selected_email_tpl,
            name=lead_row["name"],
            sender=full_sender,
            site=lead_row.get("website", ""),
            problem=problem_text,
            benefit=benefit_text,
            psi_score=lead_row.get("psi_mobile", ""),
            website_url=website_link,
        )

        st.caption("Note: This email is a tailored starting point. Review and personalize the details before sending.")

        # Stable widget keys for subject and body (ISS-07)
        sub_key = f"email_sub_{lead_id}"
        body_key = f"email_body_{lead_id}"

        subject_val = st.text_input(
            "Subject Line",
            value=initial_sub,
            key=sub_key,
        )

        body_val = st.text_area(
            "Email Body",
            value=initial_body,
            height=260,
            key=body_key,
        )

        words_count = len(body_val.split())
        chars_count = len(body_val)
        if 70 <= words_count <= 130:
            word_hint = "✅ Optimal length"
        elif words_count > 130:
            word_hint = "⚠️ Too long (>130 words)"
        else:
            word_hint = "ℹ️ Short (<70 words)"
        st.markdown(
            f"<div class='meta-hint'>Length: <strong>{words_count}</strong> words | "
            f"<strong>{chars_count}</strong> characters ({word_hint} - Target: 80-120 words)</div>",
            unsafe_allow_html=True,
        )

        st.markdown("#### Message Preview & Quick Copy")
        st.code(body_val, language=None)

        lead_email = str(lead_row.get("email", "")).strip()

        col_act1, col_act2, col_act3 = st.columns([1.5, 1.5, 2])
        if lead_email:
            gmail_link = (
                f"https://mail.google.com/mail/?view=cm&fs=1&to={quote(lead_email)}"
                f"&su={quote(subject_val)}&body={quote(body_val)}"
            )
            mailto_link = f"mailto:{lead_email}?subject={quote(subject_val)}&body={quote(body_val)}"

            col_act1.link_button("✉️ Open in Gmail", gmail_link, type="primary")
            col_act2.link_button("📨 Open in Mail App", mailto_link)
        else:
            col_act1.info("No verified email address found for this lead.")
            if lead_row.get("email_guess"):
                col_act2.caption(f"Pattern guess: {lead_row['email_guess']}")

        btn_email_label = "Mark Contacted Anyway" if is_recently_contacted else "Mark as Contacted via Email"
        if col_act3.button(btn_email_label, key=f"btn_contact_email_{lead_id}", disabled=not database_ready or bool(st.session_state.get("pending_changes"))):
            updated = df.copy(deep=True)
            lead_idx = selected_email_lead
            updated.at[lead_idx, "status"] = "Contacted"
            updated.at[lead_idx, "contacted_date"] = datetime.date.today().isoformat()
            current_log = str(updated.at[lead_idx, "outreach_log"] or "")
            entry = f"[{datetime.date.today().isoformat()} | Email | {selected_email_tpl}]"
            updated.at[lead_idx, "outreach_log"] = f"{current_log}; {entry}" if current_log else entry
            if persist_changes(df, updated):
                st.toast(f"Marked '{lead_row['name']}' as Contacted via Email!")
                st.rerun()


# -------------------- TAB 6: ACTIVITY --------------------
with tab_activity:
    st.subheader("5. System Health & Activity Logs")

    st.markdown("#### 🩺 System Health Panel")
    h_col1, h_col2 = st.columns([1, 1])
    with h_col1:
        st.markdown("**Configuration Secrets Status** *(names only)*")
        g_status = "set (length OK)" if secret("GOOGLE_API_KEY") else "not set"
        db_status = "set (length OK)" if secret("SUPABASE_DB_URL") else "not set"
        pw_status = "set (length OK)" if secret("APP_PASSWORD") else "not set"
        psi_status = "set (length OK)" if secret("PSI_API_KEY") else "not set"
        st.markdown(f"- `GOOGLE_API_KEY`: **{g_status}**")
        st.markdown(f"- `SUPABASE_DB_URL`: **{db_status}**")
        st.markdown(f"- `APP_PASSWORD`: **{pw_status}**")
        st.markdown(f"- `PSI_API_KEY`: **{psi_status}**")

    with h_col2:
        st.markdown("**Database & Runtime Diagnostics**")
        st.markdown(f"- Database: **{DATABASE_STATUS_MESSAGES.get(database_state, ('Unknown', ''))[0]}**")
        st.markdown(f"- Schema: `appnix`")
        st.markdown(f"- Active Migration: `001_persistence`")
        if database_ready:
            st.markdown(f"- Quota: `{usage_snapshot['day']['request_count']} / {daily_limit}` today · `{usage_snapshot['month']['request_count']} / {hard_limit}` monthly")

    st.markdown("---")
    st.markdown("#### 🔑 Test Google Places API Key")
    if st.button("Run Test Search Request", key="test_google_key_activity"):
        test_k = secret("GOOGLE_API_KEY")
        if not test_k:
            st.warning("GOOGLE_API_KEY is not configured.")
        else:
            try:
                search("test", test_k, 1, store=None, category="test", city="test")
                st.success("Google Places API key is valid and active!")
            except PlacesSearchError as exc:
                if "API key" in str(exc) or "INVALID_ARGUMENT" in str(exc) or "rejected" in str(exc):
                    st.error("Google rejected the API key: Replace GOOGLE_API_KEY in environment or .env with an active key.")
                else:
                    st.info(f"Key test result: {exc}")
            except Exception:
                st.error("Key test failed.")

    st.markdown("---")
    st.markdown("#### 📜 Search History")
    if recent_searches:
        st.dataframe(
            pd.DataFrame(recent_searches)[["category", "city", "requested_pages", "leads_returned", "requests", "status", "started_at"]],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No search records found.")

    st.markdown("---")
    st.markdown("#### 📊 Dataset Preview")
    if df.empty:
        st.info("No leads available.")
    else:
        st.dataframe(df.drop(columns=[c for c in df if c.startswith("_")]), width="stretch", hide_index=True)
