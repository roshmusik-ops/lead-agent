# Rosh Creatives — Lead Agent

Automated lead-generation agent that finds local Kerala businesses (bridal, jewellery, textiles, ayurveda, etc.), generates personalized pitches, and emails them on your behalf.

## Features
- 🔍 Scrapes Google Maps for businesses by category + city (no paid API)
- 🧠 Uses Gemini to write personalized pitches in English (Malayalam optional)
- 📊 Saves leads to a CSV / Google Sheet
- ✉️ Sends emails via Gmail SMTP (with your approval)
- 🖥️ Streamlit dashboard to review leads + track replies

## Setup

### 1. Install Python 3.10+
Download: https://www.python.org/downloads/

### 2. Install dependencies
```powershell
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure secrets
Copy `.env.example` to `.env` and fill in:
- `GEMINI_API_KEY` — from https://aistudio.google.com/apikey
- `GMAIL_USER` — rosh.musik@gmail.com
- `GMAIL_APP_PASSWORD` — 16-char app password from https://myaccount.google.com/apppasswords

### 4. Run the scraper
```powershell
python scraper.py --category "bridal lounge" --city "Kochi" --limit 25
```

### 5. Generate pitches
```powershell
python pitcher.py
```

### 6. Review + send via dashboard
```powershell
streamlit run dashboard.py
```

## Project structure
```
lead-agent/
├── .env                 # secrets (NOT committed)
├── .env.example
├── requirements.txt
├── config.py            # brand info + categories + cities
├── scraper.py           # Google Maps scraper
├── pitcher.py           # Gemini pitch generator
├── outreach.py          # Gmail sender
├── dashboard.py         # Streamlit UI
└── data/
    └── leads.csv        # output
```

## Brand
- **Rosh Creatives** by Roshith R Menon
- 📞 +91 94473 36560 · ✉️ rosh.musik@gmail.com · 🌐 roshmusik.com
