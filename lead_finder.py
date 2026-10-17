"""
Appnix Local Lead Finder
------------------------
Extracts local business listings via Google Places API (New) and saves them to CSV.
Persists leads, search history and request accounting in PostgreSQL.
Legacy JSON helpers remain for backward compatibility, but do not govern new searches.

Setup:
    pip install requests
    export GOOGLE_API_KEY="your_api_key"    (Windows: set GOOGLE_API_KEY=your_api_key)

Usage:
    python lead_finder.py "coaching center" "Lucknow"
    python lead_finder.py "clinic" "Noida Sector 62" --out clinics.csv
    python lead_finder.py "restaurant" "Lucknow Gomti Nagar" --max-pages 3
"""

import argparse
import csv
import datetime
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

from configuration import config_value, configured_store
from storage import QuotaReached, StorageError

URL = "https://places.googleapis.com/v1/places:searchText"
USAGE_FILE = "usage.json"
_IN_MEMORY_USAGE: Dict[str, int] = {}

# Contact, website, and rating fields fall under Google Places Enterprise SKU
FIELDS = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.googleMapsUri",
        "places.businessStatus",
        "nextPageToken",
    ]
)

# Domains considered social media or directories rather than official websites
SOCIAL_HOSTS = (
    "facebook.com",
    "instagram.com",
    "justdial.com",
    "linktr.ee",
    "wa.me",
    "whatsapp.com",
    "youtube.com",
    "twitter.com",
    "x.com",
    "sulekha.com",
    "indiamart.com",
    "business.site",  # Legacy Google business mini-sites
)


def _current_month_key() -> str:
    """Return the current month formatted as YYYY-MM."""
    return datetime.date.today().strftime("%Y-%m")


def get_monthly_usage(month: Optional[str] = None) -> Tuple[int, bool]:
    """
    Get the number of Google Places API calls made for the given month.
    Returns (calls_count, is_persistent_storage).
    Legacy history only: new searches use Store.usage() instead.
    """
    m_key = month or _current_month_key()
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return int(data.get(m_key, 0)), True
        except Exception:
            pass

    # Fallback to in-memory store
    return int(_IN_MEMORY_USAGE.get(m_key, 0)), False


def record_api_call(count: int = 1, month: Optional[str] = None) -> Tuple[int, bool]:
    """
    Increment monthly API call counter and persist to usage.json if writable.
    Returns (new_count, is_persistent).
    Legacy compatibility only: never called by the tracked search path.
    """
    m_key = month or _current_month_key()
    current_calls, is_file = get_monthly_usage(m_key)
    new_count = current_calls + count

    # Try saving to usage.json
    try:
        data: Dict[str, Any] = {}
        if os.path.exists(USAGE_FILE):
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[m_key] = new_count
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return new_count, True
    except Exception:
        # File write failed (read-only filesystem or permissions)
        _IN_MEMORY_USAGE[m_key] = new_count
        return new_count, False


def lead_type(website: str) -> str:
    """Classify lead priority based on website presence."""
    if not website:
        return "HOT - no website"
    if any(h in website.lower() for h in SOCIAL_HOSTS):
        return "HOT - only social/directory page"
    return "Check website"


class PlacesSearchError(RuntimeError):
    """A failed search, with any leads already retrieved from earlier pages."""

    def __init__(self, message: str, partial_results: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.partial_results = list(partial_results or [])
        self.pending_completion = None
        self.search_id = None


def place_to_lead(place, category="", city=""):
    """Map provider results to the existing lead schema without dropping audit fields."""
    from lead_model import CSV_COLUMNS
    lead = dict.fromkeys(CSV_COLUMNS, "")
    site = place.get("websiteUri", "")
    lead.update(name=place.get("displayName", {}).get("text", ""), category=category,
                city=city, phone=place.get("nationalPhoneNumber", ""), website=site,
                lead_type=lead_type(site), rating=place.get("rating", ""),
                reviews=place.get("userRatingCount", ""), address=place.get("formattedAddress", ""),
                maps_link=place.get("googleMapsUri", ""), place_id=place.get("id", ""),
                status="New", broken_links=0, audit_score=0)
    return lead


def _api_error_message(response: requests.Response, api_key: str) -> str:
    """Expose Google's useful error details without exposing the API key."""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    message = str(error.get("message") or "Google returned an unsuccessful response.")
    status = str(error.get("status") or "UNKNOWN")
    details = error.get("details", [])
    reasons = " ".join(
        str(item.get("reason", "")) for item in details if isinstance(item, dict)
    ) if isinstance(details, list) else ""
    diagnostic = f"{status} {reasons} {message}".lower()

    if "billing" in diagnostic:
        hint = "Enable billing on the Google Cloud project that owns this API key."
    elif "service_disabled" in diagnostic or "has not been used" in diagnostic:
        hint = "Enable Places API (New) in the Google Cloud project that owns this API key."
    elif "api_key_invalid" in diagnostic or "api key not valid" in diagnostic:
        hint = "Google rejected the API key: Replace GOOGLE_API_KEY in your app settings with a valid Google Cloud API key."
    elif any(word in diagnostic for word in ("blocked", "referer", "referrer", "restriction")):
        hint = (
            "Check this key's restrictions: allow Places API (New) and the app server's public IP. "
            "Website referrer restrictions do not work for this server-side search."
        )
    elif response.status_code == 429 or "resource_exhausted" in diagnostic:
        hint = "Check your Places API quota in Google Cloud Console and retry after the rate limit resets."
    elif response.status_code in (401, 403):
        hint = "Check the API key, Places API (New) enablement, billing, and key restrictions in its Google Cloud project."
    elif response.status_code >= 500:
        hint = "Google Places is temporarily unavailable. Please try again shortly."
    else:
        hint = "Review the Google error above and check your search settings."
    return f"Google Places API error (HTTP {response.status_code}, {status}): {message} {hint}".replace(
        api_key, "[REDACTED]"
    )


def search(
    query: str,
    api_key: str,
    max_pages: int,
    usage_limit: Optional[int] = None,
    *,
    store=None,
    category: str = "",
    city: str = "",
    search_id: Optional[str] = None,
    progress_callback=None,
) -> List[Dict[str, Any]]:
    """
    Search Google Places API for businesses matching the query.
    Respects optional monthly usage limits and records all calls.
    Raises PlacesSearchError on failure; only successful empty searches return [].
    """
    api_key = api_key.strip().strip("'\"")
    query = query.strip()
    if not api_key:
        raise PlacesSearchError("Google Places API key is missing. Configure GOOGLE_API_KEY or enter it in the sidebar.")
    if not query or not 1 <= max_pages <= 3:
        raise PlacesSearchError("Provide a search query and request one to three pages.")
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELDS,
    }
    body: Dict[str, Any] = {"textQuery": query, "pageSize": 20, "regionCode": "IN"}
    results: List[Dict[str, Any]] = []

    search_id = search_id or str(uuid.uuid4())
    pending = None
    error_category = "provider"
    started = False
    try:
        store = store or configured_store()
        started = store.start_search(search_id, category or query, city, max_pages)
        if not started:
            raise PlacesSearchError("This search submission was already recorded. Refresh saved leads before starting another search.")
        for page in range(max_pages):
            request_id = store.reserve_request("google_places", "text_search", search_id, monthly_cap=usage_limit)
            pending = {"request_id":request_id, "outcome":"network_error"}
            try:
                r = requests.post(URL, json=body, headers=headers, timeout=30, allow_redirects=False)
            except requests.Timeout:
                pending["outcome"] = "timeout"
                store.complete_request(**pending)
                pending = None
                error_category = "network"
                raise PlacesSearchError("Business search timed out. Please try again shortly.", results) from None
            except requests.RequestException:
                store.complete_request(**pending)
                pending = None
                error_category = "network"
                raise PlacesSearchError("Could not connect to the business search provider. Check the server connection and try again.", results) from None
            pending.update(http_status=r.status_code, outcome="http_error")
            if r.status_code != 200:
                store.complete_request(**pending)
                pending = None
                raise PlacesSearchError(_api_error_message(r, api_key), results)
            pending["outcome"] = "invalid_response"
            try:
                data = r.json()
                if not isinstance(data, dict) or not isinstance(data.get("places", []), list) or any(not isinstance(p, dict) for p in data.get("places", [])):
                    raise ValueError("Invalid response shape")
            except ValueError:
                store.complete_request(**pending)
                pending = None
                error_category = "invalid_response"
                raise PlacesSearchError("The search provider returned an invalid response. Please try again shortly.", results) from None
            results.extend(data.get("places", []))
            pending.update(outcome="success", leads=[place_to_lead(p, category, city) for p in data.get("places", [])])
            # Each page's leads, counters and request outcome commit together, before fetching another page.
            store.complete_request(**pending)
            pending = None
            if progress_callback:
                progress_callback(page + 1, max_pages, len(results))
            token = data.get("nextPageToken")
            if not token:
                break
            body["pageToken"] = token
            if page + 1 < max_pages:
                time.sleep(2)
        store.finish_search(search_id, "success")
    except (StorageError, PlacesSearchError) as cause:
        if isinstance(cause, QuotaReached):
            error_category = "quota"
        elif isinstance(cause, StorageError):
            error_category = "database"
        exc = cause if isinstance(cause, PlacesSearchError) else PlacesSearchError(str(cause), results)
        exc.pending_completion = pending
        exc.search_id = search_id
        if started:
            try:
                store.finish_search(search_id, "partial" if results else "blocked" if error_category == "quota" else "failed", error_category)
            except StorageError:
                exc.args = (str(exc) + " Search history could not be finalized; its recorded status may remain running.",)
        raise exc from None
    return results


def main() -> None:
    """CLI entry point for searching leads and writing to CSV."""
    ap = argparse.ArgumentParser(description="Find local business leads via Google Maps Places API")
    ap.add_argument("category", help='Business type, e.g., "coaching center", "clinic", "restaurant"')
    ap.add_argument("city", help='City or neighborhood, e.g., "Lucknow" or "Noida Sector 62"')
    ap.add_argument("--max-pages", type=int, default=3, help="Pages to fetch (1-3, each page has up to 20 results)")
    ap.add_argument("--out", default="leads.csv", help="Output CSV filepath")
    args = ap.parse_args()

    api_key = config_value("GOOGLE_API_KEY")
    if not api_key:
        sys.exit("Error: GOOGLE_API_KEY environment variable is not set. Please configure your key.")

    query = f"{args.category} in {args.city}"
    print(f"Searching: {query}")
    try:
        places = search(query, api_key, min(args.max_pages, 3), category=args.category, city=args.city)
    except PlacesSearchError as exc:
        if not exc.partial_results:
            sys.exit(str(exc))
        places = exc.partial_results
        print(f"Warning: {exc} Saving {len(places)} leads from completed pages.", file=sys.stderr)
    print(f"Found {len(places)} businesses.")

    seen = set()
    file_exists = os.path.exists(args.out)
    if file_exists:
        with open(args.out, newline="", encoding="utf-8") as f:
            seen = {row.get("place_id", "") for row in csv.DictReader(f)}

    cols = [
        "place_id",
        "name",
        "category_searched",
        "city_searched",
        "phone",
        "website",
        "lead_type",
        "rating",
        "reviews",
        "address",
        "maps_link",
        "email",
        "status",
    ]
    new_count = 0
    with open(args.out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if not file_exists:
            w.writeheader()
        for p in places:
            pid = p.get("id", "")
            if pid in seen or p.get("businessStatus") not in (None, "OPERATIONAL"):
                continue
            site = p.get("websiteUri", "")
            w.writerow(
                {
                    "place_id": pid,
                    "name": p.get("displayName", {}).get("text", ""),
                    "category_searched": args.category,
                    "city_searched": args.city,
                    "phone": p.get("nationalPhoneNumber", ""),
                    "website": site,
                    "lead_type": lead_type(site),
                    "rating": p.get("rating", ""),
                    "reviews": p.get("userRatingCount", ""),
                    "address": p.get("formattedAddress", ""),
                    "maps_link": p.get("googleMapsUri", ""),
                    "email": "",
                    "status": "New",
                }
            )
            new_count += 1

    print(f"Saved {new_count} new leads to {args.out}.")
    hot = sum(1 for p in places if lead_type(p.get("websiteUri", "")).startswith("HOT"))
    print(f"{hot} leads classified as 'HOT' (no website or social-only link).")
    print("Search requests and retrieved leads are recorded in the configured database.")


if __name__ == "__main__":
    main()


# team search
