"""Streamlit dashboard to review leads, edit pitches, approve & send."""
from __future__ import annotations

import os
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from config import BRAND, CATEGORIES, KERALA_CITIES

load_dotenv()
DATA_DIR = Path(__file__).parent / "data"
LEADS_CSV = DATA_DIR / "leads.csv"
PITCHES_CSV = DATA_DIR / "pitches.csv"
OUTREACH_CSV = DATA_DIR / "outreach.csv"

st.set_page_config(page_title="Rosh Creatives — Lead Agent", layout="wide")
st.title("🎬 Rosh Creatives — Lead Agent")
st.caption(f"{BRAND['founder']} · {BRAND['phone']} · {BRAND['email']} · {BRAND['website']}")

tab1, tab2, tab3 = st.tabs(["📋 Leads", "✍️ Pitches", "✉️ Outreach"])


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
