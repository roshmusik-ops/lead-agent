"""Auto-draft replies to incoming Gmail responses.

Reads unread emails in your inbox that match leads in outreach.csv,
uses Gemini to draft a contextual response, and saves to Gmail Drafts.
You review + click Send manually.

Usage:
    python replier.py            # process last 7 days of replies
    python replier.py --days 14
"""
from __future__ import annotations

import argparse
import csv
import email
import imaplib
import os
import time
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, parseaddr
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

from config import BRAND

load_dotenv()
DATA_DIR = Path(__file__).parent / "data"
OUTREACH_CSV = DATA_DIR / "outreach.csv"
REPLIES_CSV = DATA_DIR / "replies.csv"

REPLY_PROMPT = f"""You are {BRAND['founder']}, founder of {BRAND['company']}, a Kerala-based
ad/jingle/reel studio. Past work: {', '.join(BRAND['past_work'])}.

A potential client just replied to your cold email. Below is:
1. The pitch you originally sent them
2. Their reply

Write a SHORT, warm, human reply (50-90 words). Be specific to what they said.
- If they're interested: propose 2 concrete time slots for a 15-min call (next 3 weekdays, 11am or 4pm IST).
- If they want more info: send 1-2 specific portfolio links or describe one past project briefly.
- If they politely decline: thank them, say "happy to stay in touch" warmly, no pressure.
- If they ask price: give a small range ("video ads from 25k, jingles from 15k") and offer to scope it on a call.
- Sign off: "Warm regards,\\n{BRAND['founder']}"
- No corporate jargon. No "I hope this email finds you well". Sound like a Kerala creator.

OUTPUT STRICTLY:
SUBJECT: <Re: their original subject>
BODY:
<plain text reply>
"""


def configure():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY missing")
    genai.configure(api_key=key)
    return genai.GenerativeModel("gemini-flash-latest")


def decode_str(s) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for txt, enc in parts:
        if isinstance(txt, bytes):
            try:
                out.append(txt.decode(enc or "utf-8", errors="ignore"))
            except Exception:
                out.append(txt.decode("utf-8", errors="ignore"))
        else:
            out.append(txt)
    return "".join(out)


def get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="ignore")
                except Exception:
                    pass
        return ""
    try:
        return msg.get_payload(decode=True).decode(errors="ignore")
    except Exception:
        return ""


def load_outreach_index() -> dict:
    """Index outreach.csv by sender email to find original pitches."""
    if not OUTREACH_CSV.exists():
        return {}
    idx = {}
    with OUTREACH_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("email"):
                idx[r["email"].lower()] = r
    return idx


def parse_gemini_output(text: str) -> tuple[str, str]:
    subj, body = "", ""
    lines = text.strip().splitlines()
    for i, line in enumerate(lines):
        if line.upper().startswith("SUBJECT:"):
            subj = line.split(":", 1)[1].strip()
        elif line.upper().startswith("BODY:"):
            body = "\n".join(lines[i + 1:]).strip()
            break
    return subj or "Re: your reply", body or text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    user = os.getenv("GMAIL_USER")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        raise SystemExit("GMAIL credentials missing")

    outreach = load_outreach_index()
    if not outreach:
        print("No outreach.csv yet — nothing to match replies against.")
        return

    model = configure()

    print(f"Connecting to Gmail IMAP... checking last {args.days} days")
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    imap.login(user, pwd)
    imap.select("INBOX")

    since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - args.days * 86400))
    typ, data = imap.search(None, f'(UNSEEN SINCE {since})')
    ids = data[0].split() if data and data[0] else []
    print(f"Found {len(ids)} unread emails")

    # Track which we've already drafted replies to
    replied = set()
    if REPLIES_CSV.exists():
        with REPLIES_CSV.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                replied.add(r["msg_id"])

    drafted = 0
    new_log = []
    for mid in ids:
        typ, msg_data = imap.fetch(mid, "(RFC822)")
        if typ != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        msg_id = msg.get("Message-ID", "").strip()
        if not msg_id or msg_id in replied:
            continue

        from_addr = parseaddr(msg.get("From", ""))[1].lower()
        original = outreach.get(from_addr)
        if not original:
            continue  # not from a lead we contacted

        subject = decode_str(msg.get("Subject", ""))
        body = get_body(msg)[:3000]

        prompt = (
            f"{REPLY_PROMPT}\n\n"
            f"=== ORIGINAL PITCH SENT ===\n"
            f"Subject: {original['email_subject']}\n\n{original['email_body']}\n\n"
            f"=== THEIR REPLY ===\n"
            f"From: {original['name']} <{from_addr}>\n"
            f"Subject: {subject}\n\n{body}\n"
        )
        try:
            resp = model.generate_content(prompt)
            re_subj, re_body = parse_gemini_output(resp.text)
        except Exception as e:
            print(f"  Gemini error: {e}")
            continue

        # Save as draft via IMAP append to [Gmail]/Drafts
        draft = MIMEMultipart()
        draft["From"] = f"{BRAND['founder']} <{user}>"
        draft["To"] = from_addr
        draft["Subject"] = re_subj if re_subj.lower().startswith("re:") else f"Re: {subject}"
        draft["In-Reply-To"] = msg_id
        draft["References"] = msg_id
        draft["Date"] = formatdate(localtime=True)
        draft.attach(MIMEText(re_body, "plain", "utf-8"))

        try:
            imap.append('"[Gmail]/Drafts"', r"(\Draft)",
                        imaplib.Time2Internaldate(time.time()),
                        draft.as_bytes())
            drafted += 1
            new_log.append({"msg_id": msg_id, "from": from_addr,
                            "name": original["name"], "drafted_at": time.strftime("%Y-%m-%d %H:%M")})
            print(f"  ✓ Drafted reply to {original['name']} <{from_addr}>")
        except Exception as e:
            print(f"  ✗ Draft error: {e}")

    imap.logout()

    if new_log:
        existed = REPLIES_CSV.exists()
        with REPLIES_CSV.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["msg_id", "from", "name", "drafted_at"])
            if not existed:
                w.writeheader()
            w.writerows(new_log)

    print(f"\n✓ Drafted {drafted} replies. Open Gmail → Drafts to review.")


if __name__ == "__main__":
    main()
