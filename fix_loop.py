with open('scrapers/legal_risk_scraper.py', 'r') as f:
    lines = f.readlines()

# Find and replace the entire tenk loop section
content = ''.join(lines)

old = '''    # Step 3: Check 10-K legal proceedings section (most reliable)
    tenk_filings = [f for f in filings if f["form"] == "10-K"][:2]  # last 2 annual reports
    for filing in tenk_filings:
        acc = filing["accession_raw"]
        acc_nodash = acc.replace("-", "")
        cik_int = int(cik)

        # Use JSON index to find main document reliably
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
        r = _get(index_url)
        time.sleep(0.15)

        if not r:
            continue

        try:
            idx_data = r.json()
            items = idx_data.get("directory", {}).get("item", [])
            # Find main htm file - named like ticker-date.htm, not an exhibit
            doc_name = None
            for item in items:
                name = item.get("name", "")
                if (name.endswith(".htm") and
                    "exhibit" not in name.lower() and
                    "index" not in name.lower() and
                    not name.startswith("0")):
                    doc_name = name
                    break
            if not doc_name:
                continue
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc_name}"
        except Exception as e:
            logger.warning(f"[LegalRisk] Failed to parse index.json: {e}")
            continue
        doc_r = _get(doc_url)
        time.sleep(0.2)

        if not doc_r:
            continue'''

new = '''    # Step 3: Check 10-K legal proceedings section (most reliable)
    tenk_filings = [f for f in filings if f["form"] == "10-K"][:2]
    for filing in tenk_filings:
        try:
            acc = filing["accession_raw"]
            acc_nodash = acc.replace("-", "")
            cik_int = int(cik)

            logger.info(f"[LegalRisk] Fetching 10-K index for {ticker} ({filing['date']})")

            # Use JSON index to find main document
            index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
            r = _get(index_url)
            time.sleep(0.15)
            if not r:
                logger.warning(f"[LegalRisk] Failed to get index.json for {acc}")
                continue

            idx_data = r.json()
            items = idx_data.get("directory", {}).get("item", [])

            # Find main htm - named like ticker-date.htm, not an exhibit or R-file
            doc_name = None
            for item in items:
                name = item.get("name", "")
                if (name.endswith(".htm") and
                    "exhibit" not in name.lower() and
                    "index" not in name.lower() and
                    not name.startswith("0") and
                    not name.startswith("R")):
                    doc_name = name
                    break

            if not doc_name:
                logger.warning(f"[LegalRisk] No main doc found in index for {acc}")
                continue

            doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc_name}"
            logger.info(f"[LegalRisk] Fetching {doc_url}")

            doc_r = _get(doc_url)
            time.sleep(0.2)
            if not doc_r:
                logger.warning(f"[LegalRisk] Failed to fetch {doc_url}")
                continue'''

if old in content:
    content = content.replace(old, new)
    with open('scrapers/legal_risk_scraper.py', 'w') as f:
        f.write(content)
    print('✅ Loop rewritten cleanly')
else:
    print('❌ Pattern not found')
