"""Rosh Creatives autonomous reply agent (Level B).

For every UNREAD inbox email that's a reply to a lead we contacted:
  1. Classify intent via Gemini (interested / wants_info / wants_price /
     not_interested / unsubscribe / out_of_office / complex / complaint / angry / legal).
  2. If intent is in AGENT['auto_send_intents']: generate a warm Kerala-creator
     reply and AUTO-SEND it via Gmail SMTP.
  3. If intent is in AGENT['always_draft_intents']: save a DRAFT in Gmail for you.
  4. Unsubscribes -> data/do_not_contact.csv (future runs skip them).
  5. Logs everything to data/agent_log.csv.

Usage:
    python replier.py
    python replier.py --days 14
    python replier.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import email
import imaplib
import os
import smtplib
import ssl
import time
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, parseaddr
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

from config import AGENT, BRAND

load_dotenv()
DATA_DIR = Path(__file__).parent / "data"
OUTREACH_CSV = DATA_DIR / "outreach.csv"
REPLIES_CSV = DATA_DIR / "replies.csv"
DNC_CSV = DATA_DIR / "do_not_contact.csv"
AGENT_LOG = DATA_DIR / "agent_log.csv"

VALID_INTENTS = {
    "interested", "wants_info", "wants_price", "not_interested",
    "unsubscribe", "out_of_office", "complex", "complaint", "angry", "legal",
}

CLASSIFIER_PROMPT = """You are an intent classifier for {company}, a Kerala creative studio.
Read the inbound reply below and output ONE word from this list, nothing else:

interested        - they want to talk / move forward
wants_info        - they're curious and want portfolio / examples / sample work
wants_price       - they're asking for a quote / rate / package
not_interested    - polite no, not now
unsubscribe       - explicitly asks to be removed / stop emailing
out_of_office     - auto-reply / vacation responder
complex           - multi-question, unclear, needs human judgement
complaint         - they're unhappy or feel spammed
angry             - hostile / abusive language
legal             - mentions lawyer / legal action / court

OUTPUT EXACTLY ONE WORD from the list. No punctuation, no explanation.

=== REPLY ===
From: {sender}
Subject: {subject}

{body}
"""

REPLY_PROMPT = """You are {founder}, founder of {company}, a Kerala-based creative
studio producing video ads, radio jingles, social media reels and brand sound design.
Past work: {past_work}.

You're replying to a lead's response. Their classified intent is: {intent}.

Tone: warm, confident, conversational, slightly Malayali. 50-100 words.
NOT corporate. NO "I hope this email finds you well". Plain prose, no bullets/asterisks.

GUIDELINES BY INTENT:
- interested: thank them, propose 2 concrete time slots in next 4 weekdays (e.g. "Thursday 11:00 IST or Friday 16:00 IST"). Add WhatsApp ({phone}) as quick alternative. {booking_line}
- wants_info: briefly describe ONE past project that matches their category, offer to share the actual film/jingle link if they want a quick look.
- wants_price: give an honest starting range: video ads from INR 25,000, radio jingles from INR 15,000, reels from INR 8,000 - and offer to scope properly on a 10-min call.
- not_interested: thank them sincerely, say "happy to stay in touch", no pressure.
- unsubscribe: confirm immediate removal, apologise briefly.
- out_of_office: very short - acknowledge, say you'll follow up when they're back.

Sign off:
Warm regards,
{founder}
{company} - {tagline}
{phone} - {email_addr}
{website}

=== ORIGINAL PITCH WE SENT ===
Subject: {orig_subject}

{orig_body}

=== THEIR REPLY ===
From: {sender_name} <{sender_email}>
Subject: {orig_reply_subject}

{reply_body}

OUTPUT STRICTLY:
SUBJECT: <Re: ...>
BODY:
<reply body>
"""


def configure_gemini():
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
    if not OUTREACH_CSV.exists():
        return {}
    idx = {}
    with OUTREACH_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("email"):
                idx[r["email"].lower()] = r
    return idx


def load_dnc() -> set:
    if not DNC_CSV.exists():
        return set()
    out = set()
    with DNC_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            e = r.get("email", "").lower().strip()
            if e:
                out.add(e)
    return out


def add_to_dnc(email_addr: str, reason: str):
    new = not DNC_CSV.exists()
    with DNC_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["email", "reason", "added_at"])
        if new:
            w.writeheader()
        w.writerow({"email": email_addr.lower(), "reason": reason,
                    "added_at": time.strftime("%Y-%m-%d %H:%M")})


def log_action(row: dict):
    new = not AGENT_LOG.exists()
    fields = ["timestamp", "msg_id", "from", "name", "intent", "action", "subject"]
    with AGENT_LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def classify_intent(model, sender: str, subject: str, body: str) -> str:
    prompt = CLASSIFIER_PROMPT.format(
        company=BRAND["company"],
        sender=sender,
        subject=subject,
        body=body[:2500],
    )
    try:
        resp = model.generate_content(prompt)
        word = (resp.text or "").strip().lower().split()[0]
        word = "".join(ch for ch in word if ch.isalpha() or ch == "_")
        if word in VALID_INTENTS:
            return word
    except Exception as e:
        print(f"  classifier error: {e}")
    return "complex"


def parse_output(text: str) -> tuple[str, str]:
    subj, body = "", ""
    lines = text.strip().splitlines()
    for i, line in enumerate(lines):
        if line.upper().startswith("SUBJECT:"):
            subj = line.split(":", 1)[1].strip()
        elif line.upper().startswith("BODY:"):
            body = "\n".join(lines[i + 1:]).strip()
            break
    return subj or "Re: your reply", body or text


def draft_reply(model, intent: str, original: dict, sender_email: str,
                sender_name: str, reply_subject: str, reply_body: str) -> tuple[str, str]:
    booking_line = ""
    if BRAND.get("booking_link"):
        booking_line = f"Or pick a slot directly here: {BRAND['booking_link']}."
    prompt = REPLY_PROMPT.format(
        company=BRAND["company"],
        founder=BRAND["founder"],
        past_work=", ".join(BRAND["past_work"]),
        intent=intent,
        phone=BRAND["phone"],
        email_addr=BRAND["email"],
        website=BRAND["website"],
        tagline=BRAND.get("tagline", ""),
        booking_line=booking_line,
        orig_subject=original.get("email_subject", ""),
        orig_body=original.get("email_body", ""),
        sender_name=sender_name or original.get("name", ""),
        sender_email=sender_email,
        orig_reply_subject=reply_subject,
        reply_body=reply_body[:2500],
    )
    resp = model.generate_content(prompt)
    return parse_output(resp.text or "")


def send_email(smtp, sender_user: str, to_addr: str, subject: str,
               body: str, in_reply_to: str = "", references: str = ""):
    msg = MIMEMultipart()
    msg["From"] = f"{BRAND['founder']} <{sender_user}>"
    msg["To"] = to_addr
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg["Date"] = formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    smtp.sendmail(sender_user, [to_addr], msg.as_string())


def save_draft(imap, user: str, to_addr: str, subject: str, body: str,
               in_reply_to: str, references: str):
    draft = MIMEMultipart()
    draft["From"] = f"{BRAND['founder']} <{user}>"
    draft["To"] = to_addr
    draft["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    draft["Date"] = formatdate(localtime=True)
    if in_reply_to:
        draft["In-Reply-To"] = in_reply_to
        draft["References"] = references or in_reply_to
    draft.attach(MIMEText(body, "plain", "utf-8"))
    imap.append('"[Gmail]/Drafts"', r"(\Draft)",
                imaplib.Time2Internaldate(time.time()), draft.as_bytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=AGENT["max_auto_replies_per_day"])
    args = ap.parse_args()

    user = os.getenv("GMAIL_USER")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        raise SystemExit("GMAIL_USER / GMAIL_APP_PASSWORD missing")

    outreach = load_outreach_index()
    if not outreach:
        print("No outreach.csv - nothing to match replies against.")
        return

    dnc = load_dnc()
    model = configure_gemini()

    print(f"[Rosh Agent] Checking last {args.days} days of inbox (dry_run={args.dry_run})")
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    imap.login(user, pwd)
    imap.select("INBOX")

    since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - args.days * 86400))
    typ, data = imap.search(None, f'(UNSEEN SINCE {since})')
    ids = data[0].split() if data and data[0] else []
    print(f"  Found {len(ids)} unread")

    seen_msg_ids = set()
    if REPLIES_CSV.exists():
        with REPLIES_CSV.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                seen_msg_ids.add(r.get("msg_id", ""))

    smtp = None
    if not args.dry_run:
        ctx = ssl.create_default_context()
        smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx)
        smtp.login(user, pwd)

    sent_count = 0
    drafted_count = 0
    processed = []

    for mid in ids:
        if sent_count + drafted_count >= args.max:
            print(f"  Hit daily cap ({args.max}). Stopping.")
            break
        try:
            typ, msg_data = imap.fetch(mid, "(RFC822)")
            if typ != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            msg_id = msg.get("Message-ID", "").strip()
            if not msg_id or msg_id in seen_msg_ids:
                continue

            from_addr = parseaddr(msg.get("From", ""))[1].lower()
            if not from_addr:
                continue
            original = outreach.get(from_addr)
            if not original:
                continue

            subject = decode_str(msg.get("Subject", ""))
            body = get_body(msg)[:5000]
            references = msg.get("References", msg_id)

            intent = classify_intent(model, from_addr, subject, body)
            print(f"  -> {original['name']} <{from_addr}> :: {intent}")

            log_row = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "msg_id": msg_id,
                "from": from_addr,
                "name": original["name"],
                "intent": intent,
                "subject": subject,
            }

            if args.dry_run:
                log_row["action"] = "dry_run"
                log_action(log_row)
                processed.append(log_row)
                continue

            if from_addr in dnc:
                log_row["action"] = "skipped_dnc"
                log_action(log_row)
                continue

            if intent == "unsubscribe":
                add_to_dnc(from_addr, "unsubscribe_request")
                dnc.add(from_addr)

            if intent in AGENT["always_draft_intents"]:
                action = "draft"
            elif intent in AGENT["auto_send_intents"]:
                action = "send"
            else:
                action = "draft"

            try:
                re_subj, re_body = draft_reply(
                    model, intent, original, from_addr,
                    original["name"], subject, body
                )
            except Exception as e:
                print(f"    gen error: {e}")
                continue

            if action == "send":
                try:
                    send_email(smtp, user, from_addr, re_subj, re_body,
                               in_reply_to=msg_id, references=references)
                    log_row["action"] = "sent"
                    sent_count += 1
                    time.sleep(AGENT["seconds_between_sends"])
                except Exception as e:
                    print(f"    send failed: {e}")
                    log_row["action"] = f"send_error: {e}"
            else:
                try:
                    save_draft(imap, user, from_addr, re_subj, re_body,
                               in_reply_to=msg_id, references=references)
                    log_row["action"] = "drafted"
                    drafted_count += 1
                except Exception as e:
                    print(f"    draft failed: {e}")
                    log_row["action"] = f"draft_error: {e}"

            log_action(log_row)
            processed.append(log_row)

        except Exception as e:
            print(f"  message error: {e}")
            continue

    if smtp:
        smtp.quit()
    imap.logout()

    if processed:
        existed = REPLIES_CSV.exists()
        with REPLIES_CSV.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["msg_id", "from", "name", "intent", "action", "drafted_at"])
            if not existed:
                w.writeheader()
            for r in processed:
                w.writerow({
                    "msg_id": r["msg_id"],
                    "from": r["from"],
                    "name": r["name"],
                    "intent": r["intent"],
                    "action": r.get("action", ""),
                    "drafted_at": r["timestamp"],
                })

    print(f"\n[Rosh Agent] Sent: {sent_count} | Drafted: {drafted_count}")


if __name__ == "__main__":
    main()
