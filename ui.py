"""
UI styling and helper components for Appnix Lead Finder.
Provides custom CSS, card layouts, metric displays, and clean header styling.
"""

import streamlit as st
from html import escape
from storage import DATABASE_DIAGNOSTICS

CUSTOM_CSS = """
<style>
/* Appnix Premium Theme: Deep Navy & Energetic Orange */
:root {
    --appnix-navy: #0B192C;
    --appnix-navy-light: #1E3E62;
    --appnix-orange: #FF6500;
    --appnix-orange-hover: #E05500;
    --appnix-bg-soft: #F8FAFC;
    --appnix-border: #E2E8F0;
    --appnix-text-main: #0B192C;
    --appnix-text-muted: #64748B;
}

.app-header {margin: 0 0 1.5rem; padding: 0 0 1.25rem; border-bottom: 2px solid #E2E8F0;}
.app-eyebrow {font-size: .75rem; font-weight: 750; letter-spacing: .14em; color: #FF6500; margin-bottom: .45rem; text-transform: uppercase;}
.app-title {font-size: clamp(1.6rem, 3vw, 2.3rem); letter-spacing: -.04em; font-weight: 800; color: #0B192C; margin: 0;}
.app-subtitle {font-size: .98rem; line-height: 1.6; color: #64748B; margin: .5rem 0 0; max-width: 50rem;}

.metric-grid {display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: .85rem; margin-bottom: 1.5rem;}
.metric-card {background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px; padding: 1.1rem 1.15rem; box-shadow: 0 2px 8px rgba(11, 25, 44, 0.04); border-top: 3px solid #E2E8F0; transition: transform 0.15s ease, box-shadow 0.15s ease;}
.metric-card:hover {transform: translateY(-2px); box-shadow: 0 4px 12px rgba(11, 25, 44, 0.08);}
.metric-label {font-size: .78rem; font-weight: 600; color: #64748B; margin-bottom: .6rem; text-transform: uppercase; letter-spacing: 0.05em;}
.metric-value {font-size: 1.95rem; font-weight: 800; line-height: 1.1; color: #0B192C; letter-spacing: -.03em;}

.metric-card:first-child {border-top: 3px solid #0B192C;}
.metric-card.card-hot {border-top: 3px solid #FF6500;}
.metric-card.card-due {border-top: 3px solid #E11D48;}

.meta-hint {font-size: .82rem; color: #64748B; margin-top: .25rem;}
.sidebar-brand {display: flex; align-items: center; gap: .75rem; padding: .25rem 0 .75rem; border-bottom: 1px solid #E2E8F0; margin-bottom: 1rem;}
.sidebar-brand-mark {display: grid; place-items: center; width: 2.4rem; height: 2.4rem; flex-shrink: 0; background: linear-gradient(135deg, #FF6500 0%, #FA812F 100%); color: white; border-radius: 10px; font-weight: 800; font-size: 1.1rem; box-shadow: 0 2px 6px rgba(255, 101, 0, 0.35);}
.sidebar-brand-name {font-size: 1.15rem; font-weight: 800; letter-spacing: -.03em; color: #0B192C;}
.sidebar-brand-caption {font-size: .75rem; color: #64748B; margin-top: .1rem;}

/* Badges */
.badge-hot {background: linear-gradient(135deg, #FF6500 0%, #FA812F 100%); color: #FFFFFF; font-size: 0.72rem; font-weight: 750; padding: 2px 8px; border-radius: 6px; letter-spacing: 0.05em; display: inline-block; text-transform: uppercase;}
.badge-status-new {background: #F1F5F9; color: #334155; font-size: 0.72rem; font-weight: 650; padding: 2px 8px; border-radius: 6px; display: inline-block;}
.badge-status-contacted {background: #EFF6FF; color: #1D4ED8; font-size: 0.72rem; font-weight: 650; padding: 2px 8px; border-radius: 6px; display: inline-block;}
.badge-status-meeting {background: #FEF3C7; color: #92400E; font-size: 0.72rem; font-weight: 650; padding: 2px 8px; border-radius: 6px; display: inline-block;}
.badge-status-closed {background: #F0FDF4; color: #15803D; font-size: 0.72rem; font-weight: 650; padding: 2px 8px; border-radius: 6px; display: inline-block;}
.badge-status-not-interested {background: #FFF1F2; color: #BE123C; font-size: 0.72rem; font-weight: 650; padding: 2px 8px; border-radius: 6px; display: inline-block;}

/* System Health Card */
.health-card {background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 0.85rem 1rem; margin-bottom: 0.75rem;}
.health-row {display: flex; justify-content: space-between; align-items: center; padding: 0.35rem 0; font-size: 0.84rem; border-bottom: 1px dashed #E2E8F0;}
.health-row:last-child {border-bottom: none;}
.health-label {color: #475569; font-weight: 600;}
.health-val {font-family: monospace; font-size: 0.82rem;}
.health-ok {color: #16A34A; font-weight: 700;}
.health-bad {color: #DC2626; font-weight: 700;}

@media (max-width: 800px) {.metric-grid {grid-template-columns: repeat(3, minmax(0, 1fr));}}
@media (max-width: 480px) {.metric-grid {grid-template-columns: repeat(2, minmax(0, 1fr));} .metric-card {padding: .85rem;}}
</style>
"""


def apply_custom_css() -> None:
    """Inject minimal, clean CSS into the Streamlit application."""
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


DATABASE_STATUS_MESSAGES = {
    "connected": ("Connected", "Saved leads and usage are available."),
    "not_configured": ("Not configured", "Set SUPABASE_DB_URL in .env or deployment secrets (not .env.example). Configure SUPABASE_SSL_ROOT_CERT, then refresh the connection."),
    "connection_failed": ("Connection failed", "Ask an administrator to check database availability, credentials, TLS and access permissions, then refresh."),
    "migration_required": ("Migration required", "Ask an administrator to apply the reviewed database migration, then refresh the connection."),
}


def render_sidebar_brand() -> None:
    st.markdown(
        '<div class="sidebar-brand"><div class="sidebar-brand-mark" aria-hidden="true">◈</div>'
        '<div><div class="sidebar-brand-name">Appnix</div>'
        '<div class="sidebar-brand-caption">Lead Finder · Workspace</div></div></div>',
        unsafe_allow_html=True,
    )


def render_database_status(state: str, diagnostic: str = "") -> None:
    """Render fixed copy only. Never pass exceptions or UI return values to st.write."""
    label, guidance = DATABASE_STATUS_MESSAGES.get(state, DATABASE_STATUS_MESSAGES["connection_failed"])
    if state == "connected":
        st.success(label)
    elif state == "not_configured":
        st.info(label)
    else:
        st.warning(label)
    st.caption(DATABASE_DIAGNOSTICS.get(diagnostic, guidance))


def render_header(
    title: str = "Appnix Lead Finder",
    subtitle: str = "Find local businesses, audit websites, find emails, and perform personalized manual outreach.",
) -> None:
    """Render a clean, professional application header."""
    st.markdown(
        f"""
        <div class="app-header">
            <div class="app-eyebrow">Appnix Growth & Business Development</div>
            <h1 class="app-title">{escape(title)}</h1>
            <p class="app-subtitle">{escape(subtitle)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metrics(
    total: int = 0,
    hot: int = 0,
    with_phone: int = 0,
    with_email: int = 0,
    contacted: int = 0,
    followups_due: int = 0,
) -> None:
    """Render the standard top metrics banner."""
    values = [
        ("Total Leads", total, ""),
        ("Hot Leads", hot, "card-hot"),
        ("With Phone", with_phone, ""),
        ("With Email", with_email, ""),
        ("Contacted", contacted, ""),
    ]
    if followups_due > 0:
        values.append(("Follow-ups Due", followups_due, "card-due"))
    cards = "".join(f'<div class="metric-card {css}"><div class="metric-label">{label}</div>'
                    f'<div class="metric-value">{int(value):,}</div></div>' for label, value, css in values)
    st.markdown(f'<div class="metric-grid" role="group" aria-label="Saved lead metrics">{cards}</div>', unsafe_allow_html=True)
