"""One-off batch scrape: top 5 ad-buying categories x all 14 Kerala districts."""
import subprocess, sys
from config import KERALA_CITIES

CATS = ["bridal lounge", "jewellery shop", "textile showroom",
        "ayurveda clinic", "restaurant"]

total = len(CATS) * len(KERALA_CITIES)
i = 0
for cat in CATS:
    for city in KERALA_CITIES:
        i += 1
        print(f"\n[{i}/{total}] {cat} in {city}")
        try:
            subprocess.run(
                [sys.executable, "scraper.py", "--category", cat, "--city", city, "--limit", "20"],
                check=False,
            )
        except KeyboardInterrupt:
            print("\nStopped by user.")
            sys.exit(0)
print("\nALL DONE")
