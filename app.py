"""
Appnix Lead Finder - Main Application
------------------------------------
Find local business leads via Google Places API (New), audit websites,
extract emails, and perform bilingual WhatsApp and Email outreach manually.
"""

import datetime
import hmac
import os
from urllib.parse import quote

import pandas as pd
import streamlit as st

from lead_finder import get_monthly_usage, lead_type, search
from templates import TEMPLATES, api_benefit, format_email, format_message
from ui import apply_custom_css, render_header, render_metrics
from website_audit import audit_batch

# Standard column list (backward-compatible, existing columns preserved, new columns added)
CSV_COLUMNS = [
    "name",
    "category",
    "phone",
    "website",
    "lead_type",
    "rating",
    "reviews",
    "address",
    "maps_link",
    "place_id",
    "email",
    "problem",
    "problem_en",
    "problem_hi",
    "contacted_date",
    "status",
    "psi_mobile",
    "lcp_sec",
    "cls",
    "ssl_days_left",
    "broken_links",
    "audit_score",
    "audit_time",
    "instagram",
    "facebook",
    "linkedin",
    "email_source",
    "email_confidence",
    "email_guess",
    "outreach_log",
]

# Page configuration
st.set_page_config(
    page_title="Appnix Lead Finder",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Apply custom styling (only hides #MainMenu and footer, keeps sidebar controls visible)
apply_custom_css()


def secret(name: str, default: str = "") -> str:
    """Safely fetch sensitive credentials from st.secrets or environment variables."""
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name, default)


# Password Gate
APP_PASSWORD = secret("APP_PASSWORD")
if APP_PASSWORD:
    if not st.session_state.get("authed", False):
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        col_center = st.columns([1, 2, 1])[1]
        with col_center:
            st.subheader("🔒 Appnix Lead Finder")
            st.caption("Please enter your access password to continue.")
            entered_pw = st.text_input("Password", type="password", key="gate_password")
            if entered_pw:
                if hmac.compare_digest(entered_pw, APP_PASSWORD):
                    st.session_state["authed"] = True
                    st.rerun()
                else:
                    st.error("Incorrect password. Please try again.")
        st.stop()

# Initialize session state for lead data and audit cache
if "leads" not in st.session_state:
    st.session_state.leads = pd.DataFrame(columns=CSV_COLUMNS)

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


def on_expander_sender_change(source_prefix: str):
    st.session_state["sender_name"] = st.session_state[f"{source_prefix}_sender_name"]
    st.session_state["company_name"] = st.session_state[f"{source_prefix}_company_name"]
    st.session_state["website_url"] = st.session_state[f"{source_prefix}_website_url"]
    other_prefix = "email_exp" if source_prefix == "wa_exp" else "wa_exp"
    st.session_state[f"{other_prefix}_sender_name"] = st.session_state["sender_name"]
    st.session_state[f"{other_prefix}_company_name"] = st.session_state["company_name"]
    st.session_state[f"{other_prefix}_website_url"] = st.session_state["website_url"]


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
    st.header("Settings")

    # Places API monthly usage counter
    used_calls, is_persistent = get_monthly_usage()
    free_limit = 1000
    usage_pct = min(1.0, used_calls / free_limit)

    st.subheader("Places API Usage")
    st.progress(usage_pct, text=f"Calls this month: {used_calls} / {free_limit} safe tier")
    if used_calls >= 800:
        st.warning("⚠️ Warning: You have used over 80% of your free 1,000 monthly Places API calls.")

    if not is_persistent:
        st.caption("ℹ️ Running in memory-only mode; counter resets on app restart.")

    hard_limit = st.number_input(
        "Block searches when calls >=",
        min_value=50,
        max_value=2000,
        value=900,
        step=50,
        help="Hard stop to avoid accidental billing when approaching Google's monthly free tier limit.",
    )

    st.markdown("---")
    st.subheader("Sender Details")
    st.text_input(
        "Sender Name",
        key="sender_name",
        on_change=on_sidebar_sender_change,
        help="Your personal or contact name",
    )
    st.text_input(
        "Company Name",
        key="company_name",
        on_change=on_sidebar_sender_change,
        help="Your agency or company name",
    )
    st.text_input(
        "Website URL",
        key="website_url",
        on_change=on_sidebar_sender_change,
        help="Domain included in email signatures",
    )

    st.markdown("---")
    st.subheader("API Credentials")

    # API Keys status / fallback inputs
    env_google_key = secret("GOOGLE_API_KEY")
    if env_google_key:
        api_key = env_google_key
        st.success("Google Places API: Configured securely")
    else:
        api_key = st.text_input(
            "Google Places API Key",
            type="password",
            help="Enter your Google Maps Places API (New) key",
            key="input_google_key",
        )

    env_psi_key = secret("PSI_API_KEY")
    if env_psi_key:
        psi_key = env_psi_key
        st.success("PageSpeed API: Configured securely")
    else:
        psi_key = st.text_input(
            "PageSpeed API Key (Optional)",
            type="password",
            help="Optional Google PageSpeed Insights API key",
            key="input_psi_key",
        )


# Render main app header
render_header(
    title="Appnix Lead Finder",
    subtitle="Find local business leads, perform deep website audits, and run personalized manual WhatsApp and Email outreach.",
)

df = st.session_state.leads

# Ensure all expected columns exist in df
for col in CSV_COLUMNS:
    if col not in df.columns:
        df[col] = ""
st.session_state.leads = df

# Calculate summary metrics
total_leads = len(df)
hot_leads = int(df["lead_type"].str.startswith("HOT").sum()) if not df.empty else 0
with_phone = int((df["phone"].fillna("").astype(str).str.strip() != "").sum()) if not df.empty else 0
with_email = int((df["email"].fillna("").astype(str).str.strip() != "").sum()) if not df.empty else 0
contacted_count = int((df["status"] == "Contacted").sum()) if not df.empty else 0

# Top metrics bar
render_metrics(
    total=total_leads,
    hot=hot_leads,
    with_phone=with_phone,
    with_email=with_email,
    contacted=contacted_count,
)

st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

# Main Application Tabs
tab_find, tab_audit, tab_whatsapp, tab_email, tab_export = st.tabs(
    ["Find Leads", "Audit & Emails", "WhatsApp", "Email", "Export"]
)

# -------------------- TAB 1: FIND LEADS --------------------
with tab_find:
    st.subheader("1. Search Google Places")
    c1, c2, c3 = st.columns([3, 3, 1])
    category_in = c1.text_input("Business Category", placeholder="e.g. coaching center, dental clinic, restaurant")
    city_in = c2.text_input("City or Neighborhood", placeholder="e.g. Lucknow Gomti Nagar, Noida Sector 62")
    pages_in = c3.selectbox("Pages", [1, 2, 3], index=2, help="Each page fetches up to 20 business results")

    if st.button("Search Leads", type="primary", use_container_width=False):
        current_calls, _ = get_monthly_usage()
        if current_calls >= hard_limit:
            st.error(
                f"Search blocked: Monthly Places API calls ({current_calls}) have reached or exceeded "
                f"your safety limit of {hard_limit}. Adjust the limit in the sidebar if you wish to proceed."
            )
        elif not api_key:
            st.error("Google Places API Key is missing. Please configure it in the sidebar settings.")
        elif not category_in.strip() or not city_in.strip():
            st.error("Please provide both a business category and a target city/area.")
        else:
            with st.spinner("Searching Google Places API for verified listings..."):
                query_str = f"{category_in.strip()} in {city_in.strip()}"
                places = search(query_str, api_key, pages_in, usage_limit=hard_limit)

            if places:
                new_rows = []
                for p in places:
                    site = p.get("websiteUri", "")
                    new_rows.append(
                        {
                            "name": p.get("displayName", {}).get("text", ""),
                            "category": category_in.strip(),
                            "phone": p.get("nationalPhoneNumber", ""),
                            "website": site,
                            "lead_type": lead_type(site),
                            "rating": p.get("rating", ""),
                            "reviews": p.get("userRatingCount", ""),
                            "address": p.get("formattedAddress", ""),
                            "maps_link": p.get("googleMapsUri", ""),
                            "place_id": p.get("id", ""),
                            "email": "",
                            "problem": "",
                            "problem_en": "",
                            "problem_hi": "",
                            "contacted_date": "",
                            "status": "New",
                            "psi_mobile": "",
                            "lcp_sec": "",
                            "cls": "",
                            "ssl_days_left": "",
                            "broken_links": 0,
                            "audit_score": 0,
                            "audit_time": "",
                            "instagram": "",
                            "facebook": "",
                            "linkedin": "",
                            "email_source": "",
                            "email_confidence": "",
                            "email_guess": "",
                            "outreach_log": "",
                        }
                    )
                new_df = pd.DataFrame(new_rows)
                combined = pd.concat([df, new_df], ignore_index=True) if not df.empty else new_df
                deduped = combined.drop_duplicates(subset=["place_id"], keep="first").reset_index(drop=True)
                st.session_state.leads = deduped
                st.success(f"Retrieved {len(new_rows)} leads. Total unique leads in session: {len(deduped)}.")
                st.rerun()
            else:
                st.warning("No listings found. Please verify your query, location, and Google API billing setup.")

    st.markdown("---")
    st.subheader("Current Leads")
    if df.empty:
        st.info("No leads found yet. Enter a business type and area above to start searching.")
    else:
        st.dataframe(
            df[["name", "category", "phone", "website", "lead_type", "rating", "reviews", "status"]],
            use_container_width=True,
            hide_index=True,
        )

# -------------------- TAB 2: AUDIT & EMAILS --------------------
with tab_audit:
    st.subheader("2. Deep Website Audit & Email Extraction")
    if df.empty:
        st.info("No leads yet. Search a business type and area to begin.")
    else:
        st.write(
            "Performs deep audit checks: SSL certificate expiry, mobile viewport, internal broken links (up to 15), "
            "page speed, HTML basics, and email extraction (plain-text, obfuscated, and JSON-LD schema)."
        )

        col_audit_btn, col_cache_info = st.columns([2, 3])
        cached_count = len(st.session_state.audit_cache)
        if col_audit_btn.button("Audit Websites & Find Emails", type="primary"):
            progress_bar = st.progress(0.0, text="Starting deep audit with thread pool...")

            def on_progress(done: int, total: int, current_name: str) -> None:
                progress_bar.progress(done / total, text=f"Audited {done}/{total}: {current_name}")

            leads_list = df.to_dict(orient="records")
            audited_records = audit_batch(
                leads=leads_list,
                use_psi=bool(psi_key),
                psi_key=psi_key,
                max_workers=4,
                progress_callback=on_progress,
                cache=st.session_state.audit_cache,
            )

            updated_df = pd.DataFrame(audited_records)
            st.session_state.leads = updated_df
            progress_bar.empty()
            st.toast("Deep website audit completed!")
            st.success("Website audits completed.")
            st.rerun()

        col_cache_info.caption(f"⚡ Domain cache active: {cached_count} domains cached to avoid redundant network calls.")

        st.markdown("---")
        st.subheader("Audited Leads Management")

        # Table filter controls
        f_col1, f_col2, f_col3, f_col4 = st.columns([2, 2, 2, 1.5])
        search_query = f_col1.text_input("Search Leads", placeholder="Filter by name, address, email...")
        lead_type_filter = f_col2.selectbox("Lead Type", ["All", "Hot leads only", "Has website"])
        status_filter = f_col3.selectbox(
            "Status",
            ["All", "New", "Contacted", "Replied", "Meeting", "Closed", "Not interested"],
        )
        has_email_only = f_col4.checkbox("Has Email", value=False)

        # Apply table filters
        filtered_df = df.copy()
        if search_query.strip():
            q = search_query.strip().lower()
            filtered_df = filtered_df[
                filtered_df["name"].astype(str).str.lower().str.contains(q)
                | filtered_df["address"].astype(str).str.lower().str.contains(q)
                | filtered_df["email"].astype(str).str.lower().str.contains(q)
            ]
        if lead_type_filter == "Hot leads only":
            filtered_df = filtered_df[filtered_df["lead_type"].str.startswith("HOT")]
        elif lead_type_filter == "Has website":
            filtered_df = filtered_df[~filtered_df["lead_type"].str.startswith("HOT")]
        if status_filter != "All":
            filtered_df = filtered_df[filtered_df["status"] == status_filter]
        if has_email_only:
            filtered_df = filtered_df[filtered_df["email"].fillna("").astype(str).str.strip() != ""]

        status_options = ["New", "Contacted", "Replied", "Meeting", "Closed", "Not interested"]

        edited_data = st.data_editor(
            filtered_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "website": st.column_config.LinkColumn("Website"),
                "maps_link": st.column_config.LinkColumn("Google Maps"),
                "instagram": st.column_config.LinkColumn("Instagram"),
                "facebook": st.column_config.LinkColumn("Facebook"),
                "linkedin": st.column_config.LinkColumn("LinkedIn"),
                "status": st.column_config.SelectboxColumn("Status", options=status_options),
                "audit_score": st.column_config.NumberColumn("Score (0-100)"),
                "ssl_days_left": st.column_config.TextColumn("SSL Days"),
                "broken_links": st.column_config.NumberColumn("Broken Links"),
                "email_confidence": st.column_config.TextColumn("Email Confidence"),
            },
            disabled=["place_id", "lead_type", "maps_link", "audit_score", "broken_links", "audit_time"],
        )

        # Synchronize inline edits back to session state
        if edited_data is not None and not edited_data.empty:
            today_str = datetime.date.today().isoformat()
            for row_id in edited_data.index:
                new_status = edited_data.at[row_id, "status"]
                if new_status == "Contacted" and not df.at[row_id, "contacted_date"]:
                    df.at[row_id, "contacted_date"] = today_str
            df.loc[edited_data.index, edited_data.columns] = edited_data
            st.session_state.leads = df

        # Quick diagnostic inspector
        if not df.empty:
            st.markdown("#### Diagnostic Findings Inspector")
            insp_names = [n for n in df["name"].tolist() if n]
            selected_insp = st.selectbox("Select Lead to Inspect Findings", insp_names, key="inspect_lead_sel")
            inspect_row = df[df["name"] == selected_insp].iloc[0]

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

# -------------------- TAB 3: WHATSAPP OUTREACH --------------------
with tab_whatsapp:
    st.subheader("3. WhatsApp Outreach Generator")

    # Safety Net: Sender details quick edit expander for mobile / collapsed sidebar
    render_sender_expander("wa_exp")

    if df.empty:
        st.info("No leads yet. Search a business type and area to begin.")
    else:
        # Daily outreach counter
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

        lead_names = [n for n in df["name"].tolist() if n]
        selected_lead_name = st.selectbox("Select Target Lead", lead_names, key="wa_lead_sel")
        lead_row = df[df["name"] == selected_lead_name].iloc[0]

        # 3-Day Duplicate check
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

        # Determine auto-selected template
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
            key=f"tpl_select_{lead_row['place_id']}",
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
        message_body = st.text_area(
            "Message Content",
            value=initial_message,
            height=200,
            key=f"msg_area_{lead_row['place_id']}_{selected_template}_{selected_lang}_{full_sender}",
        )

        # Live character and word count
        char_count = len(message_body)
        word_count = len(message_body.split())
        st.markdown(
            f"<div class='meta-hint'>Length: <strong>{char_count}</strong> characters | "
            f"<strong>{word_count}</strong> words</div>",
            unsafe_allow_html=True,
        )

        st.markdown("#### Message Preview & Quick Copy")
        st.code(message_body, language=None)

        # Phone extraction and WhatsApp link
        phone_raw = str(lead_row.get("phone", ""))
        digits = "".join(ch for ch in phone_raw if ch.isdigit())

        btn_col1, btn_col2 = st.columns([1, 1])
        if len(digits) >= 10:
            formatted_phone = "91" + digits[-10:]
            wa_link = f"https://wa.me/{formatted_phone}?text={quote(message_body)}"
            btn_col1.link_button("📲 Open WhatsApp", wa_link, type="primary")
        else:
            btn_col1.info("No valid phone number available for this business.")

        # Mark as Contacted
        btn_label = "Mark Contacted Anyway" if is_recently_contacted else "Mark as Contacted Today"
        if btn_col2.button(btn_label, key=f"btn_contact_{lead_row['place_id']}"):
            lead_idx = df[df["place_id"] == lead_row["place_id"]].index[0]
            df.at[lead_idx, "status"] = "Contacted"
            df.at[lead_idx, "contacted_date"] = today_iso
            current_log = str(df.at[lead_idx, "outreach_log"] or "")
            entry = f"[{today_iso} | WhatsApp | {selected_template}]"
            df.at[lead_idx, "outreach_log"] = f"{current_log}; {entry}" if current_log else entry
            st.session_state.leads = df
            st.toast(f"Marked '{lead_row['name']}' as Contacted via WhatsApp!")
            st.rerun()

# -------------------- TAB 4: EMAIL OUTREACH --------------------
with tab_email:
    st.subheader("4. Email Outreach Generator")

    # Safety Net: Sender details quick edit expander for mobile / collapsed sidebar
    render_sender_expander("email_exp")

    st.warning(
        "⚠️ Best Practice: Send manually from your verified business domain, keep volume to 20-30 emails/day max, "
        "and ensure SPF, DKIM, and DMARC are properly configured on your domain to prevent spam placement."
    )

    if df.empty:
        st.info("No leads yet. Search a business type and area to begin.")
    else:
        lead_names = [n for n in df["name"].tolist() if n]
        selected_email_lead = st.selectbox("Select Target Lead for Email", lead_names, key="email_lead_sel")
        lead_row = df[df["name"] == selected_email_lead].iloc[0]

        # 3-Day Duplicate check
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

        # Determine auto-selected template
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
            key=f"email_tpl_sel_{lead_row['place_id']}",
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

        subject_val = st.text_input(
            "Subject Line",
            value=initial_sub,
            key=f"email_sub_{lead_row['place_id']}_{selected_email_tpl}_{selected_email_lang}_{full_sender}",
        )

        body_val = st.text_area(
            "Email Body",
            value=initial_body,
            height=260,
            key=f"email_body_{lead_row['place_id']}_{selected_email_tpl}_{selected_email_lang}_{full_sender}_{website_link}",
        )

        # Word count calculation (target 80-120 words)
        words_count = len(body_val.split())
        chars_count = len(body_val)
        word_hint = (
            "✅ Optimal length"
            if 70 <= words_count <= 130
            else ("⚠️ Too long (>130 words)" if words_count > 130 else "ℹ️ Short (<70 words)")
        )
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

        # Mark as Contacted via Email
        btn_email_label = "Mark Contacted Anyway" if is_recently_contacted else "Mark as Contacted via Email"
        if col_act3.button(btn_email_label, key=f"btn_contact_email_{lead_row['place_id']}"):
            lead_idx = df[df["place_id"] == lead_row["place_id"]].index[0]
            df.at[lead_idx, "status"] = "Contacted"
            df.at[lead_idx, "contacted_date"] = datetime.date.today().isoformat()
            current_log = str(df.at[lead_idx, "outreach_log"] or "")
            entry = f"[{datetime.date.today().isoformat()} | Email | {selected_email_tpl}]"
            df.at[lead_idx, "outreach_log"] = f"{current_log}; {entry}" if current_log else entry
            st.session_state.leads = df
            st.toast(f"Marked '{lead_row['name']}' as Contacted via Email!")
            st.rerun()

# -------------------- TAB 5: EXPORT --------------------
with tab_export:
    st.subheader("5. Export Leads & Reports")
    if df.empty:
        st.info("No leads available to export. Search or import leads to download data.")
    else:
        st.write(
            f"Export all **{len(df)}** discovered leads with deep audit scores, contact emails, "
            f"social links, and outreach records."
        )

        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Download Leads CSV",
            data=csv_bytes,
            file_name=f"appnix_leads_{datetime.date.today().isoformat()}.csv",
            mime="text/csv",
            type="primary",
        )

        st.markdown("---")
        st.subheader("Dataset Preview")
        st.dataframe(df, use_container_width=True, hide_index=True)