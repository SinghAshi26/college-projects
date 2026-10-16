"""
UI styling and helper components for Appnix Lead Finder.
Provides custom CSS, card layouts, metric displays, and clean header styling.
"""

import streamlit as st

CUSTOM_CSS = """
<style>
    /* Hide Streamlit default hamburger menu and footer only */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    [class*="viewerBadge"] {visibility: hidden; display: none !important;}

    /* Main container spacing */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2.5rem;
        padding-left: 2.5rem;
        padding-right: 2.5rem;
        max-width: 1280px;
    }

    /* Custom Header */
    .app-header {
        margin-bottom: 1.25rem;
        padding-bottom: 0.75rem;
        border-bottom: 1px solid #e2e8f0;
    }
    .app-title {
        font-size: 1.85rem;
        font-weight: 700;
        color: #0f172a;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .app-subtitle {
        font-size: 0.95rem;
        color: #64748b;
        margin-top: 0.25rem;
        margin-bottom: 0;
    }

    /* Metric Cards */
    div[data-testid="stMetric"] {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.03);
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.8rem;
        font-weight: 600;
        color: #475569;
        text-transform: uppercase;
        letter-spacing: 0.025em;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.5rem;
        font-weight: 700;
        color: #1e293b;
    }

    /* Card Containers */
    .content-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1.25rem;
        margin-bottom: 1rem;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 1px solid #e2e8f0;
        margin-bottom: 1.25rem;
    }
    .stTabs [data-baseweb="tab"] {
        height: 42px;
        font-weight: 500;
        font-size: 0.95rem;
        padding: 0 16px;
        border-radius: 6px 6px 0 0;
    }

    /* Outreach character counter and hints */
    .meta-hint {
        font-size: 0.82rem;
        color: #64748b;
        margin-top: 0.2rem;
    }

    /* Button enhancements */
    div.stButton > button {
        border-radius: 6px;
        font-weight: 500;
        transition: all 0.15s ease-in-out;
    }

    /* Badges */
    .score-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .score-high { background-color: #dcfce7; color: #166534; }
    .score-mid { background-color: #fef9c3; color: #854d0e; }
    .score-low { background-color: #fee2e2; color: #991b1b; }
</style>
"""


def apply_custom_css() -> None:
    """Inject minimal, clean CSS into the Streamlit application."""
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def render_header(
    title: str = "Appnix Lead Finder",
    subtitle: str = "Find local businesses, audit websites, find emails, and perform personalized manual outreach.",
) -> None:
    """Render a clean, professional application header."""
    st.markdown(
        f"""
        <div class="app-header">
            <h1 class="app-title">{title}</h1>
            <p class="app-subtitle">{subtitle}</p>
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
) -> None:
    """Render the standard top metrics banner."""
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Leads", total)
    c2.metric("Hot Leads", hot)
    c3.metric("With Phone", with_phone)
    c4.metric("With Email", with_email)
    c5.metric("Contacted", contacted)
