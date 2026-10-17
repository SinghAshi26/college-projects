"""Existing CSV fields plus additive source location and complete audit data."""
import pandas as pd

CSV_COLUMNS = [
    "name", "category", "phone", "website", "lead_type", "rating", "reviews", "address",
    "maps_link", "place_id", "email", "problem", "problem_en", "problem_hi", "contacted_date",
    "status", "psi_mobile", "lcp_sec", "cls", "ssl_days_left", "broken_links", "audit_score",
    "audit_time", "instagram", "facebook", "linkedin", "email_source", "email_confidence",
    "email_guess", "outreach_log", "call_status", "call_notes", "followup_date",
]


def lead_frame(records):
    frame = pd.DataFrame(records)
    for column in CSV_COLUMNS:
        if column not in frame:
            frame[column] = ""
    # Mixed numeric/string diagnostic fields intentionally retain existing values.
    return frame[CSV_COLUMNS + [column for column in frame if column not in CSV_COLUMNS]].fillna("")


def dashboard_metrics(frame):
    return dict(
        total=len(frame),
        hot=int(frame["lead_type"].astype(str).str.startswith("HOT").sum()),
        with_phone=int(frame["phone"].astype(str).str.strip().ne("").sum()),
        with_email=int(frame["email"].astype(str).str.strip().ne("").sum()),
        contacted=int(frame["status"].eq("Contacted").sum()),
    )


def followups_due(frame, target_date=None):
    if target_date is None:
        import datetime
        target_date = datetime.date.today().isoformat()
    if "followup_date" not in frame.columns or frame.empty:
        return frame.iloc[0:0]
    return frame[frame["followup_date"].astype(str).str.strip() == str(target_date).strip()]


def csv_export(frame):
    return frame.drop(columns=[c for c in frame if c.startswith("_")]).to_csv(index=False).encode("utf-8")
