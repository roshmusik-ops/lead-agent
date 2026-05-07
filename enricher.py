"""Enrich leads with emails by visiting their websites.

For each lead with a website but no email, it fetches the homepage + likely contact pages
(/contact, /about, /reach-us, etc.) and extracts any email addresses found.

Usage:
    python enricher.py                # enrich all leads missing emails
    python enricher.py --limit 20
"""
from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import urllib.request
import urllib.error

DATA_DIR = Path(__file__).parent / "data"
LEADS_CSV = DATA_DIR / "leads.csv"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CONTACT_PATHS = [
    "", "contact", "contact-us", "contactus", "about", "about-us",
    "reach-us", "reach", "get-in-touch", "support",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}
SKIP_DOMAINS = {"sentry.io", "wixpress.com", "googleusercontent.com",
                "example.com", "domain.com", "email.com"}


def fetch(url: str, timeout: int = 8) -> str:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return ""
            data = r.read(800_000)  # cap at 800KB
            try:
                return data.decode("utf-8", errors="ignore")
            except Exception:
                return ""
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError, OSError):
        return ""
    except Exception:
        return ""


def extract_emails(html: str) -> list[str]:
    emails = set()
    for m in EMAIL_RE.findall(html or ""):
        e = m.lower().strip(".,;:")
        domain = e.split("@", 1)[1] if "@" in e else ""
        if any(domain.endswith(d) for d in SKIP_DOMAINS):
            continue
        if e.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        emails.add(e)
    return sorted(emails)


def find_email_for_site(website: str) -> str:
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = "https://" + website
    parsed = urlparse(website)
    base = f"{parsed.scheme}://{parsed.netloc}"

    found: list[str] = []
    for path in CONTACT_PATHS:
        url = urljoin(base + "/", path)
        html = fetch(url)
        if not html:
            continue
        emails = extract_emails(html)
        if emails:
            found.extend(emails)
            if path in ("contact", "contact-us"):
                break  # high-quality match, stop early
        time.sleep(0.4)

    if not found:
        return ""

    # Prefer emails on the same domain as the website
    netloc = parsed.netloc.lower().lstrip("www.")
    same = [e for e in found if e.split("@", 1)[1].endswith(netloc)]
    return (same or found)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not LEADS_CSV.exists():
        raise SystemExit("No leads.csv found.")

    rows = list(csv.DictReader(LEADS_CSV.open(encoding="utf-8")))
    fieldnames = list(rows[0].keys()) if rows else []
    if "email" not in fieldnames:
        fieldnames.append("email")
        for r in rows:
            r["email"] = ""

    todo = [r for r in rows if not r.get("email") and r.get("website")]
    if args.limit:
        todo = todo[: args.limit]

    print(f"Enriching {len(todo)} leads...")
    found = 0
    for i, r in enumerate(todo, 1):
        try:
            email = find_email_for_site(r["website"])
            if email:
                r["email"] = email
                found += 1
                print(f"  [{i}/{len(todo)}] {r['name'][:40]:40s} → {email}")
            else:
                print(f"  [{i}/{len(todo)}] {r['name'][:40]:40s} → -")
        except Exception as e:
            print(f"  [{i}] error for {r['name']}: {e}")

    with LEADS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"\n✓ Found {found} new emails. Updated {LEADS_CSV}")


if __name__ == "__main__":
    main()
