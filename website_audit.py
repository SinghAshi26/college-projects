"""
Appnix Website Audit and Advanced Email Finder
----------------------------------------------
Performs deep technical audits on lead websites and extracts verified business emails:
  - HTTPS security and SSL certificate expiry check (socket + ssl)
  - Mobile responsiveness (viewport meta tag)
  - Broken internal link detection (checks up to 15 internal links)
  - On-page basics: <title>, meta description, <h1>, outdated copyright year (>2 yrs)
  - Presence of contact forms and WhatsApp buttons
  - Google PageSpeed Insights (mobile score, LCP, CLS)
  - Advanced email discovery: mailto, plain-text, obfuscated (name [at] domain [dot] com),
    JSON-LD schema, and meta tags
  - Social media link extraction (Instagram, Facebook, LinkedIn - links only)
  - Junk email filtering and confidence ranking (same-domain vs role vs third-party)
  - Multi-threaded batch auditing with progress callback and domain caching
"""

import argparse
import csv
import datetime
import json
import os
import re
import socket
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AppnixAudit/1.0; +https://appnix.org)"}
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
OBFUSCATED_RE = re.compile(
    r"\b([a-zA-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\s+at\s+)\s*([a-zA-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\s+dot\s+|\.)\s*([a-zA-Z]{2,})\b",
    re.IGNORECASE,
)
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright|\(c\))\s*(?:20\d{2}\s*[-–—/]\s*)?(20\d{2})", re.IGNORECASE)

JUNK_EMAIL = (
    "sentry",
    "example.com",
    "domain.com",
    "wixpress",
    "noreply",
    "no-reply",
    "email.com",
    "yourname",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".svg",
    ".gif",
)

ROLE_PREFIXES = ("info@", "contact@", "hello@", "support@", "sales@", "admin@", "office@")
CONTACT_HINTS = ("contact", "about", "reach", "support", "privacy")


def get(url: str, timeout: int = 10, retries: int = 1) -> Tuple[requests.Response, float]:
    """Fetch URL with timeout and limited retries, measuring response duration in seconds."""
    last_err: Optional[Exception] = None
    for _ in range(retries + 1):
        try:
            start = time.time()
            r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            return r, round(time.time() - start, 2)
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise last_err or Exception(f"Failed to fetch {url}")


def extract_domain(url: str) -> str:
    """Extract clean domain name without www or port."""
    try:
        netloc = urlparse(url).netloc.split(":")[0].lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def check_ssl_expiry(hostname: str) -> Tuple[Optional[int], str]:
    """
    Check SSL certificate validity and days until expiration.
    Returns (days_left, status_flag).
    """
    if not hostname:
        return None, ""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        with socket.create_connection((hostname, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                if not cert or "notAfter" not in cert:
                    return None, ""
                # Date format: e.g. 'May 15 12:00:00 2027 GMT'
                exp_date = datetime.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                days_left = (exp_date - now).days
                if days_left <= 0:
                    return days_left, "expired"
                if days_left < 30:
                    return days_left, "expiring_soon"
                return days_left, "valid"
    except Exception:
        return None, "error"


def check_broken_links(soup: BeautifulSoup, base_url: str, max_check: int = 15) -> int:
    """
    Sample up to `max_check` internal links on the homepage and count 404/5xx broken links.
    """
    domain = extract_domain(base_url)
    if not domain:
        return 0

    internal_links: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.scheme in ("http", "https") and extract_domain(full_url) == domain:
            internal_links.add(full_url)
            if len(internal_links) >= max_check:
                break

    broken_count = 0
    for link in list(internal_links)[:max_check]:
        try:
            r = requests.head(link, headers=HEADERS, timeout=4, allow_redirects=True)
            if r.status_code == 405:  # Method Not Allowed for HEAD, try GET
                r = requests.get(link, headers=HEADERS, timeout=4, stream=True, allow_redirects=True)
            if r.status_code >= 400:
                broken_count += 1
        except Exception:
            broken_count += 1
    return broken_count


def extract_social_links(soup: BeautifulSoup) -> Dict[str, str]:
    """Find links to official social profiles (Instagram, Facebook, LinkedIn)."""
    socials = {"instagram": "", "facebook": "", "linkedin": ""}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        h_low = href.lower()
        if "instagram.com/" in h_low and not socials["instagram"]:
            if not any(x in h_low for x in ("/share", "/intent", "/p/")):
                socials["instagram"] = href
        elif "facebook.com/" in h_low and not socials["facebook"]:
            if not any(x in h_low for x in ("/sharer", "/share", "/dialog")):
                socials["facebook"] = href
        elif "linkedin.com/" in h_low and not socials["linkedin"]:
            if any(x in h_low for x in ("/company/", "/in/")):
                socials["linkedin"] = href
    return socials


def extract_json_ld_emails(soup: BeautifulSoup) -> Set[str]:
    """Extract email addresses declared in JSON-LD schema markup."""
    emails: Set[str] = set()
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    em = item.get("email")
                    if isinstance(em, str) and "@" in em:
                        emails.add(em.strip().lower())
                    elif isinstance(em, list):
                        for e in em:
                            if isinstance(e, str) and "@" in e:
                                emails.add(e.strip().lower())
        except Exception:
            continue
    return emails


def extract_emails_from_text(html: str) -> Set[str]:
    """Extract standard regex emails and obfuscated (e.g. name [at] domain [dot] com) emails."""
    emails: Set[str] = set(EMAIL_RE.findall(html))

    # Obfuscated matches
    for match in OBFUSCATED_RE.finditer(html):
        user, domain_part, tld = match.groups()
        constructed = f"{user}@{domain_part}.{tld}".lower().strip()
        emails.add(constructed)

    clean: Set[str] = set()
    for e in emails:
        em = e.lower().strip()
        if em and not any(j in em for j in JUNK_EMAIL):
            clean.add(em)
    return clean


def rank_emails(emails: Set[str], domain: str) -> List[Tuple[str, str]]:
    """
    Rank discovered emails: same domain first, personal addresses over role addresses.
    Returns list of (email, confidence) tuples.
    """
    if not emails:
        return []

    same_domain_personal: List[str] = []
    same_domain_role: List[str] = []
    other_personal: List[str] = []
    other_role: List[str] = []

    for em in sorted(emails):
        is_same_domain = domain and domain in em
        is_role = any(em.startswith(prefix) for prefix in ROLE_PREFIXES)

        if is_same_domain:
            if is_role:
                same_domain_role.append(em)
            else:
                same_domain_personal.append(em)
        else:
            if is_role:
                other_role.append(em)
            else:
                other_personal.append(em)

    ranked: List[Tuple[str, str]] = []
    for e in same_domain_personal:
        ranked.append((e, "high"))
    for e in same_domain_role:
        ranked.append((e, "high"))
    for e in other_personal:
        ranked.append((e, "low"))
    for e in other_role:
        ranked.append((e, "low"))

    return ranked


def contact_page_urls(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Find contact, about, reach, or privacy subpage URLs."""
    urls: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = (a.get_text() + href).lower()
        if any(h in text for h in CONTACT_HINTS):
            full = urljoin(base_url, href)
            if extract_domain(full) == extract_domain(base_url) and full not in urls:
                urls.append(full)
                if len(urls) >= 2:
                    break
    return urls


def psi_metrics(url: str, key: str) -> Dict[str, Any]:
    """
    Fetch mobile performance score, LCP, and CLS via Google PageSpeed Insights API.
    Handles quota/rate limit errors (429) gracefully without throwing.
    """
    out: Dict[str, Any] = {"score": "", "lcp_sec": "", "cls": ""}
    api = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    try:
        r = requests.get(api, params={"url": url, "strategy": "mobile", "key": key}, timeout=45)
        if r.status_code == 429:
            return out
        if r.status_code != 200:
            return out
        data = r.json()
        lh = data.get("lighthouseResult", {})
        score = lh.get("categories", {}).get("performance", {}).get("score")
        if score is not None:
            out["score"] = int(score * 100)

        audits = lh.get("audits", {})
        # LCP
        lcp_val = audits.get("largest-contentful-paint", {}).get("numericValue")
        if lcp_val is not None:
            out["lcp_sec"] = f"{round(lcp_val / 1000, 1)}s"
        # CLS
        cls_val = audits.get("cumulative-layout-shift", {}).get("numericValue")
        if cls_val is not None:
            out["cls"] = f"{round(cls_val, 2)}"
    except Exception:
        pass
    return out


def audit(url: str, use_psi: bool = False, psi_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Perform deep technical audit and email extraction on a target URL.
    Returns structured metrics, findings by severity, and bilingual problem summaries.
    """
    audit_timestamp = datetime.datetime.now().isoformat()
    out: Dict[str, Any] = {
        "https": "",
        "mobile_friendly": "",
        "load_sec": "",
        "wordpress": "",
        "psi_mobile": "",
        "lcp_sec": "",
        "cls": "",
        "ssl_days_left": "",
        "broken_links": 0,
        "found_email": "",
        "email_source": "",
        "email_confidence": "",
        "email_guess": "",
        "instagram": "",
        "facebook": "",
        "linkedin": "",
        "findings": [],
        "audit_score": 0,
        "audit_time": audit_timestamp,
        "problem": "",
        "problem_en": "",
        "problem_hi": "",
    }

    if not url:
        out["problem_en"] = "No website found"
        out["problem_hi"] = "Website hi nahi hai"
        out["problem"] = out["problem_en"]
        out["findings"].append(
            {
                "code": "NO_WEBSITE",
                "severity": "high",
                "text_en": "No website found",
                "text_hi": "Website hi nahi hai",
            }
        )
        return out

    if not url.startswith("http"):
        url = "http://" + url

    try:
        r, secs = get(url, timeout=10)
    except Exception:
        out["problem_en"] = "Website unreachable or down"
        out["problem_hi"] = "Website khul nahi rahi / down hai"
        out["problem"] = out["problem_en"]
        out["findings"].append(
            {
                "code": "SITE_DOWN",
                "severity": "high",
                "text_en": "Website unreachable or down",
                "text_hi": "Website khul nahi rahi / down hai",
            }
        )
        return out

    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    final_url = r.url
    domain = extract_domain(final_url)

    # HTTPS check
    has_https = final_url.startswith("https")
    out["https"] = "yes" if has_https else "no"

    # SSL Expiry check
    if has_https and domain:
        days_left, ssl_status = check_ssl_expiry(domain)
        if days_left is not None:
            out["ssl_days_left"] = days_left
            if ssl_status == "expired":
                out["findings"].append(
                    {
                        "code": "SSL_EXPIRED",
                        "severity": "high",
                        "text_en": "SSL security certificate has expired",
                        "text_hi": "SSL certificate expire ho chuka hai",
                    }
                )
            elif ssl_status == "expiring_soon":
                out["findings"].append(
                    {
                        "code": "SSL_EXPIRING",
                        "severity": "medium",
                        "text_en": f"SSL certificate expires soon ({days_left} days left)",
                        "text_hi": f"SSL certificate jaldi expire ho raha hai ({days_left} din baaki)",
                    }
                )

    if not has_https:
        out["findings"].append(
            {
                "code": "NO_HTTPS",
                "severity": "high",
                "text_en": "Website is not secure (no HTTPS), shows 'Not Secure' warning",
                "text_hi": "Website secure (HTTPS) nahi hai, browser 'Not secure' dikhata hai",
            }
        )

    # Mobile friendly check
    has_viewport = bool(soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}))
    out["mobile_friendly"] = "yes" if has_viewport else "no"
    if not has_viewport:
        out["findings"].append(
            {
                "code": "NO_VIEWPORT",
                "severity": "high",
                "text_en": "Website is not mobile-friendly (missing viewport tag)",
                "text_hi": "Mobile par theek se nahi dikhti (viewport tag missing)",
            }
        )

    # Load time
    out["load_sec"] = secs
    if secs > 3.0:
        out["findings"].append(
            {
                "code": "SLOW_LOAD",
                "severity": "medium",
                "text_en": f"Slow page load time ({secs}s)",
                "text_hi": f"Load hone me {secs} sec lagte hain",
            }
        )

    # WordPress / Legacy CMS
    out["wordpress"] = "yes" if ("wp-content" in html or "wp-includes" in html) else "no"

    # Broken internal links
    broken = check_broken_links(soup, final_url, max_check=15)
    out["broken_links"] = broken
    if broken > 0:
        out["findings"].append(
            {
                "code": "BROKEN_LINKS",
                "severity": "medium",
                "text_en": f"Found {broken} broken internal link(s) on homepage",
                "text_hi": f"Homepage par {broken} broken links mile",
            }
        )

    # On-page basics
    # Title
    title_tag = soup.find("title")
    if not title_tag or not (title_tag.string or "").strip():
        out["findings"].append(
            {
                "code": "NO_TITLE",
                "severity": "medium",
                "text_en": "Missing HTML page title",
                "text_hi": "HTML page title missing hai",
            }
        )

    # Meta Description
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if not meta_desc or not meta_desc.get("content", "").strip():
        out["findings"].append(
            {
                "code": "NO_META_DESC",
                "severity": "low",
                "text_en": "Missing meta description for search engines",
                "text_hi": "SEO meta description missing hai",
            }
        )

    # H1 Heading
    h1_tag = soup.find("h1")
    if not h1_tag or not h1_tag.get_text().strip():
        out["findings"].append(
            {
                "code": "NO_H1",
                "severity": "low",
                "text_en": "Missing primary H1 headline",
                "text_hi": "Primary H1 headline missing hai",
            }
        )

    # Outdated Copyright year (> 2 years old)
    current_year = datetime.datetime.now().year
    copy_matches = COPYRIGHT_RE.findall(html)
    if copy_matches:
        try:
            oldest_found = max(int(y) for y in copy_matches if 2000 <= int(y) <= current_year + 1)
            if oldest_found <= current_year - 2:
                out["findings"].append(
                    {
                        "code": "OUTDATED_COPYRIGHT",
                        "severity": "low",
                        "text_en": f"Copyright year appears outdated ({oldest_found})",
                        "text_hi": f"Copyright year purana hai ({oldest_found})",
                    }
                )
        except Exception:
            pass

    # Visible contact / Form
    has_form = bool(soup.find("form"))
    has_tel = bool(soup.find("a", href=re.compile(r"^tel:", re.I)))

    # WhatsApp Button
    has_wa = bool(re.search(r"(whatsapp|wa\.me|api\.whatsapp\.com)", html, re.I))
    if not has_wa:
        out["findings"].append(
            {
                "code": "NO_WHATSAPP",
                "severity": "low",
                "text_en": "No direct WhatsApp chat or inquiry button",
                "text_hi": "WhatsApp chat ya enquiry button nahi hai",
            }
        )

    # PageSpeed Insights
    if use_psi and psi_key:
        psi_res = psi_metrics(final_url, psi_key)
        out["psi_mobile"] = psi_res["score"]
        out["lcp_sec"] = psi_res["lcp_sec"]
        out["cls"] = psi_res["cls"]

        if isinstance(out["psi_mobile"], int) and out["psi_mobile"] < 50:
            out["findings"].append(
                {
                    "code": "LOW_PSI",
                    "severity": "medium",
                    "text_en": f"Google mobile performance score is low ({out['psi_mobile']}/100)",
                    "text_hi": f"Google mobile speed score sirf {out['psi_mobile']}/100 hai",
                }
            )

    # Social links extraction
    social_links = extract_social_links(soup)
    out["instagram"] = social_links["instagram"]
    out["facebook"] = social_links["facebook"]
    out["linkedin"] = social_links["linkedin"]

    # Email extraction
    homepage_emails = extract_emails_from_text(html)
    jsonld_emails = extract_json_ld_emails(soup)

    email_source = ""
    combined_emails: Set[str] = set(homepage_emails)

    if jsonld_emails:
        combined_emails.update(jsonld_emails)
        email_source = "json-ld"
    elif homepage_emails:
        email_source = "website"

    # Check subpages (contact / about / privacy) if emails not found
    if not combined_emails:
        subpages = contact_page_urls(soup, final_url)
        for sub_url in subpages:
            try:
                sub_r, _ = get(sub_url, timeout=7)
                sub_soup = BeautifulSoup(sub_r.text, "html.parser")
                sub_emails = extract_emails_from_text(sub_r.text)
                sub_emails.update(extract_json_ld_emails(sub_soup))
                if sub_emails:
                    combined_emails.update(sub_emails)
                    email_source = "contact page"
                    break
            except Exception:
                pass

    if combined_emails:
        ranked = rank_emails(combined_emails, domain)
        top_emails = [e for e, _ in ranked[:3]]
        out["found_email"] = ", ".join(top_emails)
        out["email_source"] = email_source or "website"
        out["email_confidence"] = ranked[0][1] if ranked else "low"
    else:
        # Generate unverified pattern guesses
        if domain:
            out["email_guess"] = f"info@{domain}, contact@{domain}, hello@{domain} (unverified guess)"
            if not has_form and not has_tel:
                out["findings"].append(
                    {
                        "code": "NO_VISIBLE_CONTACT",
                        "severity": "low",
                        "text_en": "No contact form, email, or direct phone found",
                        "text_hi": "Direct contact form ya email address nahi mila",
                    }
                )

    # Overall Audit Score calculation (0 - 100)
    score = 100
    for f in out["findings"]:
        if f["severity"] == "high":
            score -= 20
        elif f["severity"] == "medium":
            score -= 10
        elif f["severity"] == "low":
            score -= 5
    out["audit_score"] = max(0, min(100, score))

    # Top findings for problem summary (rank high > medium > low)
    sev_order = {"high": 0, "medium": 1, "low": 2}
    sorted_findings = sorted(out["findings"], key=lambda item: sev_order.get(item["severity"], 3))

    if sorted_findings:
        top_en = [f["text_en"] for f in sorted_findings[:2]]
        top_hi = [f["text_hi"] for f in sorted_findings[:2]]
        out["problem_en"] = "; ".join(top_en)
        out["problem_hi"] = "; ".join(top_hi)
    else:
        out["problem_en"] = "Site looks healthy - pitch WhatsApp automation or custom tools"
        out["problem_hi"] = "Site theek hai - WhatsApp/automation angle use karo"

    out["problem"] = out["problem_en"]
    return out


def audit_batch(
    leads: List[Dict[str, Any]],
    use_psi: bool = False,
    psi_key: Optional[str] = None,
    max_workers: int = 4,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Run audits across a list of leads using a thread pool (max 4 workers).
    Caches results by domain so re-runs do not repeat work.
    """
    results: List[Dict[str, Any]] = [dict(lead) for lead in leads]
    total = len(results)
    if total == 0:
        return results

    if cache is None:
        cache = {}

    to_audit_indices = []
    for idx, lead in enumerate(results):
        site = lead.get("website", "")
        domain = extract_domain(site) if site else ""
        if domain and domain in cache:
            # Populate from cache
            cached_res = cache[domain]
            results[idx].update(cached_res)
            if cached_res.get("found_email") and not results[idx].get("email"):
                results[idx]["email"] = cached_res["found_email"].split(",")[0].strip()
        else:
            to_audit_indices.append(idx)

    completed_count = total - len(to_audit_indices)
    if progress_callback and completed_count > 0:
        progress_callback(completed_count, total, "Loaded cached audits...")

    def _worker(idx: int) -> Tuple[int, Dict[str, Any]]:
        lead_data = results[idx]
        site_url = lead_data.get("website", "")
        is_real = site_url and not str(lead_data.get("lead_type", "")).startswith("HOT")
        res = audit(site_url if is_real else "", use_psi, psi_key)
        return idx, res

    workers = min(max_workers, len(to_audit_indices) or 1)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_worker, idx): idx for idx in to_audit_indices}
        for future in as_completed(futures):
            idx, audit_result = future.result()
            results[idx].update(audit_result)
            if audit_result.get("found_email") and not results[idx].get("email"):
                results[idx]["email"] = audit_result["found_email"].split(",")[0].strip()

            site_url = results[idx].get("website", "")
            domain = extract_domain(site_url) if site_url else ""
            if domain:
                cache[domain] = audit_result

            completed_count += 1
            if progress_callback:
                progress_callback(completed_count, total, results[idx].get("name", "")[:35])

    return results


def main() -> None:
    """CLI tool for auditing a CSV list of leads."""
    ap = argparse.ArgumentParser(description="Deep technical audit and email extraction for leads CSV")
    ap.add_argument("csv_in", help="Input CSV filepath")
    ap.add_argument("--out", default="audited.csv", help="Output audited CSV filepath")
    ap.add_argument("--psi", action="store_true", help="Include Google PageSpeed Insights score")
    args = ap.parse_args()

    psi_key = os.environ.get("PSI_API_KEY")
    if args.psi and not psi_key:
        sys.exit("Error: PSI_API_KEY environment variable is required when using --psi flag.")

    with open(args.csv_in, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        sys.exit("Error: Input CSV file is empty.")

    new_cols = [
        "https",
        "mobile_friendly",
        "load_sec",
        "wordpress",
        "psi_mobile",
        "lcp_sec",
        "cls",
        "ssl_days_left",
        "broken_links",
        "found_email",
        "email_source",
        "email_confidence",
        "email_guess",
        "instagram",
        "facebook",
        "linkedin",
        "audit_score",
        "audit_time",
        "problem",
        "problem_en",
        "problem_hi",
    ]
    fieldnames = list(rows[0].keys()) + [c for c in new_cols if c not in rows[0]]

    def progress(done: int, total: int, name: str) -> None:
        print(f"[{done}/{total}] Audited: {name}")

    audited_rows = audit_batch(rows, use_psi=args.psi, psi_key=psi_key, progress_callback=progress)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(audited_rows)

    print(f"\nDeep audit complete. Saved to {args.out}.")


if __name__ == "__main__":
    main()