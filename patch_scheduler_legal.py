import re, os

main_path = os.path.expanduser("~/Documents/trading-system/main.py")
with open(main_path, "r") as f:
    content = f.read()

# Add import
if "legal_risk_scraper" not in content:
    content = content.replace(
        "from scrapers.screener_scraper import",
        "from scrapers.legal_risk_scraper import scrape_priority_tickers\nfrom scrapers.screener_scraper import",
        1
    )
    print("✅ Added legal_risk_scraper import")
else:
    print("⚠️  Import already present, skipping")

# Add scheduled job - runs at 06:00 daily (before market open)
job_code = """
    # Legal risk scraper - SEC EDGAR (daily, pre-market)
    scheduler.add_job(
        scrape_priority_tickers,
        CronTrigger(hour=6, minute=0),
        id="legal_risk_scraper",
        name="SEC EDGAR Legal Risk Scraper",
        replace_existing=True,
    )
"""

if "legal_risk_scraper" not in content or "add_job" not in content.split("legal_risk_scraper")[1][:200]:
    # Find a good insertion point - after the last add_job block before scheduler.start()
    insert_before = "    scheduler.start()"
    content = content.replace(insert_before, job_code + "    " + insert_before.strip(), 1)
    print("✅ Added scheduled job (daily 06:00)")
else:
    print("⚠️  Job already present, skipping")

with open(main_path, "w") as f:
    f.write(content)
print("✅ main.py patched")
