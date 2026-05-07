"""Streamlit dashboard to review leads, edit pitches, approve & send."""
from __future__ import annotations

import os
import re
import smtplib
import ssl
import time
from urllib.parse import quote
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from config import BRAND, CATEGORIES, KERALA_CITIES

load_dotenv()

# Streamlit Cloud: pull secrets from st.secrets into env vars
try:
    for _k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "GEMINI_API_KEY"):
        if _k in st.secrets and not os.getenv(_k):
            os.environ[_k] = st.secrets[_k]
except Exception:
    pass

DATA_DIR = Path(__file__).parent / "data"
LEADS_CSV = DATA_DIR / "leads.csv"
PITCHES_CSV = DATA_DIR / "pitches.csv"
OUTREACH_CSV = DATA_DIR / "outreach.csv"

st.set_page_config(page_title="Rosh Creatives — Lead Agent", layout="wide")
st.title("🎬 Rosh Creatives — Lead Agent")
st.caption(f"{BRAND['founder']} · {BRAND['phone']} · {BRAND['email']} · {BRAND['website']}")

tab1, tab2, tab3, tab4 = st.tabs(["📋 Leads", "✍️ Pitches", "✉️ Email Outreach", "💬 WhatsApp"])


def clean_phone(phone: str) -> str:
    """Convert messy phone (e.g. '0484 403 3350', '+91 ...') to wa.me-friendly format."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if not digits:
        return ""
    if digits.startswith("91") and len(digits) >= 12:
        return digits[:12]
    if digits.startswith("0") and len(digits) == 11:
        return "91" + digits[1:]
    if len(digits) == 10:
        return "91" + digits
    if digits.startswith("91"):
        return digits
    return digits


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


with tab1:
    df = load_csv(LEADS_CSV)
    st.subheader(f"Scraped leads: {len(df)}")
    if df.empty:
        st.info("No leads yet. Run: `python scraper.py --category \"bridal lounge\" --city \"Kochi\"`")
    else:
        cats = ["(all)"] + sorted(df["category"].dropna().unique().tolist())
        cities = ["(all)"] + sorted(df["city"].dropna().unique().tolist())
        c1, c2, c3 = st.columns(3)
        f_cat = c1.selectbox("Category", cats)
        f_city = c2.selectbox("City", cities)
        q = c3.text_input("Search name/address")
        view = df.copy()
        if f_cat != "(all)": view = view[view["category"] == f_cat]
        if f_city != "(all)": view = view[view["city"] == f_city]
        if q:
            mask = view["name"].str.contains(q, case=False, na=False) | view["address"].str.contains(q, case=False, na=False)
            view = view[mask]
        st.dataframe(view, use_container_width=True, hide_index=True)

with tab2:
    df = load_csv(PITCHES_CSV)
    st.subheader(f"Generated pitches: {len(df)}")
    if df.empty:
        st.info("No pitches yet. Run: `python pitcher.py`")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

with tab3:
    st.markdown("### ✉️ Email outreach")
    if not OUTREACH_CSV.exists():
        st.info("Build outreach list first: `python outreach.py --build`")
    else:
        df = load_csv(OUTREACH_CSV)
        st.subheader(f"Outreach queue: {len(df)}")
        st.markdown("Edit the **email** column and set **approved=yes** for rows you want to send.")

        edited = st.data_editor(
            df,
            use_container_width=True,
            num_rows="fixed",
            column_config={
                "approved": st.column_config.SelectboxColumn(options=["no", "yes"]),
                "email_body": st.column_config.TextColumn(width="large"),
            },
            hide_index=True,
        )
        if st.button("💾 Save changes"):
            edited.to_csv(OUTREACH_CSV, index=False)
            st.success("Saved.")

        st.divider()
        st.markdown("### Send approved emails")
        approved = edited[(edited["approved"].astype(str).str.lower() == "yes")
                         & (edited["email"].astype(str).str.len() > 3)
                         & (edited["sent_at"].astype(str).str.len() == 0)]
        st.write(f"Ready to send: **{len(approved)}**")

        dry = st.checkbox("Dry run (don't actually send)", value=True)
        if st.button("🚀 Send now", disabled=len(approved) == 0):
            user = os.getenv("GMAIL_USER")
            pwd = os.getenv("GMAIL_APP_PASSWORD")
            if not user or not pwd:
                st.error("GMAIL_USER / GMAIL_APP_PASSWORD missing in .env")
            else:
                smtp = None
                if not dry:
                    ctx = ssl.create_default_context()
                    smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx)
                    smtp.login(user, pwd)

                progress = st.progress(0.0)
                for i, (_, r) in enumerate(approved.iterrows(), 1):
                    try:
                        if not dry:
                            msg = MIMEMultipart()
                            msg["From"] = f"{BRAND['founder']} <{user}>"
                            msg["To"] = r["email"]
                            msg["Subject"] = r["email_subject"]
                            msg.attach(MIMEText(r["email_body"], "plain", "utf-8"))
                            smtp.sendmail(user, [r["email"]], msg.as_string())
                        edited.loc[r.name, "sent_at"] = time.strftime("%Y-%m-%d %H:%M")
                        edited.loc[r.name, "status"] = "sent" if not dry else "dry-run"
                        time.sleep(3)
                    except Exception as e:
                        edited.loc[r.name, "status"] = f"error: {e}"
                    progress.progress(i / max(len(approved), 1))

                if smtp:
                    smtp.quit()
                edited.to_csv(OUTREACH_CSV, index=False)
                st.success("Done. Refresh to see updated statuses.")

with tab4:
    st.markdown("### 💬 WhatsApp click-to-chat")
    st.caption("Tap a link to open WhatsApp with a personalized opener pre-filled. Sends from YOUR number, fully manual — no spam risk.")
    leads_df = load_csv(LEADS_CSV)
    pitches_df = load_csv(PITCHES_CSV)
    if leads_df.empty:
        st.info("No leads yet.")
    else:
        # join pitches by name+city
        pmap = {}
        if not pitches_df.empty:
            for _, p in pitches_df.iterrows():
                pmap[(p["name"], p["city"])] = p

        c1, c2 = st.columns(2)
        cats = ["(all)"] + sorted(leads_df["category"].dropna().unique().tolist())
        cities = ["(all)"] + sorted(leads_df["city"].dropna().unique().tolist())
        f_cat = c1.selectbox("Category ", cats, key="wa_cat")
        f_city = c2.selectbox("City ", cities, key="wa_city")

        view = leads_df.copy()
        if f_cat != "(all)": view = view[view["category"] == f_cat]
        if f_city != "(all)": view = view[view["city"] == f_city]
        view = view[view["phone"].astype(str).str.len() > 3]
        st.write(f"**{len(view)}** leads with phone numbers")

        for _, r in view.head(50).iterrows():
            wa = clean_phone(r.get("phone", ""))
            if not wa:
                continue
            p = pmap.get((r["name"], r["city"]))
            if p is not None:
                opener = (
                    f"Hi! I'm {BRAND['founder']} from {BRAND['company']}. "
                    f"Loved your work at {r['name']}. We've made ads for "
                    f"{', '.join(BRAND['past_work'])} and have a fresh idea for your brand. "
                    f"Free for a 10-min chat this week?"
                )
            else:
                opener = (
                    f"Hi! I'm {BRAND['founder']} from {BRAND['company']} — we make video ads & jingles "
                    f"(past work: {', '.join(BRAND['past_work'])}). "
                    f"Have a creative idea for {r['name']}. Free for a quick chat?"
                )
            link = f"https://wa.me/{wa}?text={quote(opener)}"
            with st.container(border=True):
                cc1, cc2 = st.columns([3, 1])
                cc1.markdown(f"**{r['name']}** · _{r['category']}, {r['city']}_")
                cc1.caption(r.get("address", ""))
                cc1.caption(f"☎️ {r.get('phone','')}  ⭐ {r.get('rating','')} ({r.get('reviews','')} reviews)")
                cc2.link_button("💬 WhatsApp", link, use_container_width=True)
