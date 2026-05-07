"""Google Maps scraper for local business leads.

Usage:
    python scraper.py --category "bridal lounge" --city "Kochi" --limit 25
    python scraper.py --all                       # scrape all categories x cities
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

from config import CATEGORIES, ALL_CITIES

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
LEADS_CSV = DATA_DIR / "leads.csv"

FIELDS = [
    "name", "category", "city", "address", "phone",
    "website", "rating", "reviews", "maps_url", "scraped_at",
]


def ensure_csv():
    if not LEADS_CSV.exists():
        with LEADS_CSV.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()


def existing_keys() -> set[str]:
    if not LEADS_CSV.exists():
        return set()
    keys = set()
    with LEADS_CSV.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keys.add((row.get("name", "") + "|" + row.get("city", "")).lower())
    return keys


def append_leads(rows: list[dict]):
    ensure_csv()
    seen = existing_keys()
    new_rows = []
    for r in rows:
        key = (r.get("name", "") + "|" + r.get("city", "")).lower()
        if key and key not in seen:
            seen.add(key)
            new_rows.append(r)
    with LEADS_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        for r in new_rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return len(new_rows)


def scrape(category: str, city: str, limit: int = 25, headless: bool = True) -> list[dict]:
    state = "Tamil Nadu" if city in {
        "Chennai","Coimbatore","Madurai","Tiruchirappalli","Salem","Tirunelveli","Tiruppur",
        "Vellore","Erode","Thoothukudi","Dindigul","Thanjavur","Ranipet","Sivaganga","Karur",
        "Namakkal","Kanchipuram","Tiruvannamalai","Pudukkottai","Nagapattinam","Cuddalore",
        "Villupuram","Krishnagiri","Dharmapuri","Theni","Virudhunagar","Ramanathapuram",
        "Kanyakumari","Nilgiris","Ariyalur","Perambalur","Tenkasi","Chengalpattu","Tirupathur",
        "Mayiladuthurai","Kallakurichi","Tiruvallur","Tiruvarur"
    } else "Kerala"
    query = f"{category} in {city}, {state}"
    url = f"https://www.google.com/maps/search/{quote_plus(query)}"
    results: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(locale="en-IN")
        page = ctx.new_page()
        page.goto(url, timeout=60000)
        page.wait_for_timeout(3500)

        # Scroll the results panel to load more
        try:
            panel = page.locator('div[role="feed"]').first
            for _ in range(12):
                panel.evaluate("el => el.scrollBy(0, 1500)")
                page.wait_for_timeout(900)
                cards = page.locator('a.hfpxzc')
                if cards.count() >= limit:
                    break
        except Exception:
            pass

        cards = page.locator('a.hfpxzc')
        count = min(cards.count(), limit)
        print(f"  found {cards.count()} results, scraping {count}")

        for i in range(count):
            try:
                card = cards.nth(i)
                card.click()
                page.wait_for_timeout(1800)

                name = page.locator('h1.DUwDvf').first.inner_text(timeout=4000) if page.locator('h1.DUwDvf').count() else ""

                # Address, phone, website via aria-labels
                address = ""
                phone = ""
                website = ""
                try:
                    btns = page.locator('button[data-item-id]')
                    n = btns.count()
                    for j in range(n):
                        b = btns.nth(j)
                        did = b.get_attribute("data-item-id") or ""
                        aria = b.get_attribute("aria-label") or ""
                        if did.startswith("address"):
                            address = aria.replace("Address: ", "").strip()
                        elif did.startswith("phone"):
                            phone = aria.replace("Phone: ", "").strip()
                except Exception:
                    pass

                try:
                    a_site = page.locator('a[data-item-id="authority"]').first
                    if a_site.count():
                        website = a_site.get_attribute("href") or ""
                except Exception:
                    pass

                rating = ""
                reviews = ""
                try:
                    rspan = page.locator('div.F7nice span[aria-hidden="true"]').first
                    if rspan.count():
                        rating = rspan.inner_text().strip()
                    rcount = page.locator('div.F7nice span[aria-label*="review"]').first
                    if rcount.count():
                        m = re.search(r"([\d,]+)", rcount.inner_text())
                        reviews = m.group(1) if m else ""
                except Exception:
                    pass

                maps_url = page.url

                if name:
                    results.append({
                        "name": name.strip(),
                        "category": category,
                        "city": city,
                        "address": address,
                        "phone": phone,
                        "website": website,
                        "rating": rating,
                        "reviews": reviews,
                        "maps_url": maps_url,
                        "scraped_at": time.strftime("%Y-%m-%d %H:%M"),
                    })
            except Exception as e:
                print(f"  card {i} error: {e}")
                continue

        browser.close()

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", help="e.g. 'bridal lounge'")
    ap.add_argument("--city", help="e.g. 'Kochi'")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--all", action="store_true", help="Scrape all categories x cities")
    ap.add_argument("--show", action="store_true", help="Show browser (non-headless)")
    args = ap.parse_args()

    pairs = []
    if args.all:
        pairs = [(c, city) for c in CATEGORIES for city in ALL_CITIES]
    elif args.category and args.city:
        pairs = [(args.category, args.city)]
    else:
        ap.error("Provide --category and --city, or --all")

    total_new = 0
    for cat, city in pairs:
        print(f"\n→ {cat} in {city}")
        try:
            rows = scrape(cat, city, limit=args.limit, headless=not args.show)
            added = append_leads(rows)
            total_new += added
            print(f"  +{added} new leads (total scraped: {len(rows)})")
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\n✓ Done. {total_new} new leads added to {LEADS_CSV}")


if __name__ == "__main__":
    main()
