"""Generate personalized email pitches for leads using Gemini.

Usage:
    python pitcher.py                    # pitch all leads missing a pitch
    python pitcher.py --limit 10         # pitch only 10
    python pitcher.py --regenerate       # regenerate pitches for all
"""
from __future__ import annotations

import argparse
import csv
import os
import time
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

from config import BRAND

load_dotenv()
DATA_DIR = Path(__file__).parent / "data"
LEADS_CSV = DATA_DIR / "leads.csv"
PITCHES_CSV = DATA_DIR / "pitches.csv"

PITCH_FIELDS = ["name", "city", "category", "email_subject", "email_body", "generated_at"]

SYSTEM_PROMPT = f"""You are a copywriter for {BRAND['company']}, an advertising studio
founded by {BRAND['founder']} that produces video ads, radio jingles, and social media reels
for Kerala-based businesses. Past clients include {', '.join(BRAND['past_work'])}.

Write a SHORT, warm, professional cold email (max 120 words) to a local business owner.
Personalize it to their business name, category, and city. Mention one concrete idea
(e.g., a 30s reel, a Malayalam jingle, a festive launch ad). Avoid hype words like
"revolutionary" or "best-in-class". End with a soft call-to-action (a 10-min call).

Output STRICTLY in this format:
SUBJECT: <subject line>
BODY:
<email body, plain text, with line breaks>

Signature must be:
{BRAND['founder']}
{BRAND['company']} — {BRAND['tagline']}
{BRAND['phone']} · {BRAND['email']}
{BRAND['website']}
"""


def configure():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY missing in .env")
    genai.configure(api_key=key)
    return genai.GenerativeModel("gemini-flash-latest")


def load_leads() -> list[dict]:
    if not LEADS_CSV.exists():
        raise SystemExit("No leads.csv found. Run scraper.py first.")
    with LEADS_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_existing_pitches() -> dict:
    if not PITCHES_CSV.exists():
        return {}
    with PITCHES_CSV.open(encoding="utf-8") as f:
        return {(r["name"] + "|" + r["city"]).lower(): r for r in csv.DictReader(f)}


def save_pitches(rows: list[dict]):
    PITCHES_CSV.parent.mkdir(exist_ok=True)
    with PITCHES_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PITCH_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in PITCH_FIELDS})


def parse_output(text: str) -> tuple[str, str]:
    subject = ""
    body = ""
    if "SUBJECT:" in text:
        rest = text.split("SUBJECT:", 1)[1]
        if "BODY:" in rest:
            subject_part, body_part = rest.split("BODY:", 1)
            subject = subject_part.strip().splitlines()[0].strip()
            body = body_part.strip()
    if not subject or not body:
        # fallback: first line subject, rest body
        lines = text.strip().splitlines()
        subject = lines[0][:90] if lines else "A quick idea for your brand"
        body = "\n".join(lines[1:]).strip() or text.strip()
    return subject, body


def build_user_prompt(lead: dict) -> str:
    return (
        f"Business: {lead.get('name','')}\n"
        f"Category: {lead.get('category','')}\n"
        f"City: {lead.get('city','')}, Kerala\n"
        f"Address: {lead.get('address','')}\n"
        f"Rating: {lead.get('rating','')} ({lead.get('reviews','')} reviews)\n"
        f"Website: {lead.get('website','')}\n\n"
        "Write the personalized cold email now."
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--regenerate", action="store_true")
    args = ap.parse_args()

    model = configure()
    leads = load_leads()
    existing = {} if args.regenerate else load_existing_pitches()

    out_rows = list(existing.values())
    todo = [l for l in leads if (l["name"] + "|" + l["city"]).lower() not in existing]
    if args.limit:
        todo = todo[: args.limit]

    print(f"Generating pitches for {len(todo)} leads...")
    for i, lead in enumerate(todo, 1):
        try:
            resp = model.generate_content([SYSTEM_PROMPT, build_user_prompt(lead)])
            text = resp.text or ""
            subject, body = parse_output(text)
            out_rows.append({
                "name": lead["name"],
                "city": lead["city"],
                "category": lead["category"],
                "email_subject": subject,
                "email_body": body,
                "generated_at": time.strftime("%Y-%m-%d %H:%M"),
            })
            print(f"  [{i}/{len(todo)}] {lead['name']} → {subject[:60]}")
            time.sleep(1.2)  # gentle rate-limit for free tier
        except Exception as e:
            print(f"  [{i}] ERROR for {lead['name']}: {e}")

    save_pitches(out_rows)
    print(f"\n✓ Saved {len(out_rows)} pitches to {PITCHES_CSV}")


if __name__ == "__main__":
    main()
