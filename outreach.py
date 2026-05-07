"""Send approved pitches via Gmail SMTP.

Outreach is OPT-IN per row: only sends to leads with `email` filled and `approved=yes`
in data/outreach.csv (auto-created from pitches.csv).
"""
from __future__ import annotations

import argparse
import csv
import imaplib
import os
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path

from dotenv import load_dotenv

from config import BRAND

load_dotenv()
DATA_DIR = Path(__file__).parent / "data"
LEADS_CSV = DATA_DIR / "leads.csv"
PITCHES_CSV = DATA_DIR / "pitches.csv"
OUTREACH_CSV = DATA_DIR / "outreach.csv"

FIELDS = [
    "name", "city", "category", "email", "email_subject", "email_body",
    "approved", "sent_at", "status",
]


def build_outreach_csv():
    """Merge leads + pitches → outreach.csv (preserves approved/sent rows)."""
    if not PITCHES_CSV.exists():
        raise SystemExit("Run pitcher.py first.")

    leads = {}
    with LEADS_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            leads[(r["name"] + "|" + r["city"]).lower()] = r

    existing = {}
    if OUTREACH_CSV.exists():
        with OUTREACH_CSV.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing[(r["name"] + "|" + r["city"]).lower()] = r

    rows = []
    with PITCHES_CSV.open(encoding="utf-8") as f:
        for p in csv.DictReader(f):
            key = (p["name"] + "|" + p["city"]).lower()
            lead = leads.get(key, {})
            prev = existing.get(key, {})
            email = prev.get("email") or guess_email(lead)
            rows.append({
                "name": p["name"],
                "city": p["city"],
                "category": p["category"],
                "email": email,
                "email_subject": p["email_subject"],
                "email_body": p["email_body"],
                "approved": prev.get("approved", "no"),
                "sent_at": prev.get("sent_at", ""),
                "status": prev.get("status", ""),
            })

    with OUTREACH_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Built {OUTREACH_CSV} with {len(rows)} rows.")


def guess_email(lead: dict) -> str:
    """Best-effort email guess from website domain — leave blank if no website."""
    return ""  # Email enrichment is a separate step; keep blank for manual entry.


def send_email(smtp, to_addr: str, subject: str, body: str):
    msg = MIMEMultipart()
    msg["From"] = f"{BRAND['founder']} <{os.getenv('GMAIL_USER')}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    smtp.sendmail(os.getenv("GMAIL_USER"), [to_addr], msg.as_string())


def send_all(dry_run: bool = False, limit: int = 0):
    user = os.getenv("GMAIL_USER")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        raise SystemExit("GMAIL_USER / GMAIL_APP_PASSWORD missing in .env")

    if not OUTREACH_CSV.exists():
        build_outreach_csv()

    rows = list(csv.DictReader(OUTREACH_CSV.open(encoding="utf-8")))
    todo = [r for r in rows if r["approved"].lower() == "yes" and r["email"] and not r["sent_at"]]
    if limit:
        todo = todo[:limit]

    print(f"Approved & ready: {len(todo)}")
    if not todo:
        return

    smtp = None
    if not dry_run:
        ctx = ssl.create_default_context()
        smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx)
        smtp.login(user, pwd)

    for r in todo:
        try:
            print(f"→ {r['name']} <{r['email']}>")
            if not dry_run:
                send_email(smtp, r["email"], r["email_subject"], r["email_body"])
            r["sent_at"] = time.strftime("%Y-%m-%d %H:%M")
            r["status"] = "sent" if not dry_run else "dry-run"
            time.sleep(3)  # avoid Gmail rate limits
        except Exception as e:
            r["status"] = f"error: {e}"
            print(f"  ERROR: {e}")

    if smtp:
        smtp.quit()

    with OUTREACH_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print("✓ outreach.csv updated.")


def draft_all(limit: int = 0):
    """Save approved emails as Gmail Drafts via IMAP — you review and send manually."""
    user = os.getenv("GMAIL_USER")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        raise SystemExit("GMAIL_USER / GMAIL_APP_PASSWORD missing in .env")

    if not OUTREACH_CSV.exists():
        build_outreach_csv()

    rows = list(csv.DictReader(OUTREACH_CSV.open(encoding="utf-8")))
    todo = [r for r in rows
            if r["approved"].lower() == "yes"
            and r["email"] and not r["sent_at"]
            and r.get("status", "") != "drafted"]
    if limit:
        todo = todo[:limit]
    print(f"Drafting {len(todo)} approved emails to Gmail Drafts...")
    if not todo:
        return

    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    imap.login(user, pwd)
    drafts_box = '"[Gmail]/Drafts"'

    for r in todo:
        try:
            msg = MIMEMultipart()
            msg["From"] = f"{BRAND['founder']} <{user}>"
            msg["To"] = r["email"]
            msg["Subject"] = r["email_subject"]
            msg["Date"] = formatdate(localtime=True)
            msg.attach(MIMEText(r["email_body"], "plain", "utf-8"))
            raw = msg.as_bytes()
            imap.append(drafts_box, r"(\Draft)", imaplib.Time2Internaldate(time.time()), raw)
            r["status"] = "drafted"
            r["sent_at"] = time.strftime("%Y-%m-%d %H:%M") + " (draft)"
            print(f"  ✓ {r['name']} <{r['email']}>")
        except Exception as e:
            r["status"] = f"draft-error: {e}"
            print(f"  ✗ {r['name']}: {e}")

    imap.logout()
    with OUTREACH_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print("✓ Done. Open Gmail → Drafts to review and click Send.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="Rebuild outreach.csv from pitches")
    ap.add_argument("--send", action="store_true", help="Send approved emails directly")
    ap.add_argument("--draft", action="store_true", help="Save approved emails as Gmail drafts")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.build:
        build_outreach_csv()
    if args.send:
        send_all(dry_run=args.dry_run, limit=args.limit)
    if args.draft:
        draft_all(limit=args.limit)
    if not (args.build or args.send or args.draft):
        ap.print_help()


if __name__ == "__main__":
    main()
