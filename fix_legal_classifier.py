with open('scrapers/legal_risk_scraper.py', 'r') as f:
    content = f.read()

# Tighten the full-text search - require entity name match in findings
old = '''    enforce_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22+%22SEC+investigation%22&dateRange=custom&startdt={start}&enddt={end}"
    data = _get(enforce_url)
    time.sleep(0.15)

    if data and data.get("hits", {}).get("hits"):
        for hit in data["hits"]["hits"][:3]:
            src = hit.get("_source", {})
            desc = src.get("file_description", "")
            entity = src.get("entity_name", ticker)
            date_filed = src.get("file_date", "")
            risk = _classify_filing(f"sec investigation {desc}")
            if risk not in (RISK_NONE, RISK_MINOR):
                highest_risk = _escalate_risk(highest_risk, risk)
                findings.append({
                    "type": RISK_LABELS[risk],
                    "entity": entity,
                    "date": date_filed,
                    "description": desc or "SEC Investigation Reference",
                    "source_url": f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22+%22SEC+investigation%22",
                    "risk_level": risk,
                })'''

new = '''    enforce_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22+%22SEC+investigation%22&dateRange=custom&startdt={start}&enddt={end}"
    data = _get(enforce_url)
    time.sleep(0.15)

    if data and data.get("hits", {}).get("hits"):
        for hit in data["hits"]["hits"][:3]:
            src = hit.get("_source", {})
            desc = src.get("file_description", "")
            entity = src.get("entity_name", ticker)
            date_filed = src.get("file_date", "")
            # Only escalate if the filing entity closely matches the ticker
            # Avoids false positives from fund filings mentioning ticker as a holding
            entity_match = (
                ticker.upper() in entity.upper() or
                entity.upper() in ticker.upper()
            )
            risk = _classify_filing(f"sec investigation {desc}")
            if risk not in (RISK_NONE, RISK_MINOR) and entity_match:
                highest_risk = _escalate_risk(highest_risk, risk)
                findings.append({
                    "type": RISK_LABELS[risk],
                    "entity": entity,
                    "date": date_filed,
                    "description": desc or "SEC Investigation Reference",
                    "source_url": f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22+%22SEC+investigation%22",
                    "risk_level": risk,
                })'''

if old in content:
    content = content.replace(old, new)
    with open('scrapers/legal_risk_scraper.py', 'w') as f:
        f.write(content)
    print('✅ Classifier tightened - entity match required for SEC Investigation')
else:
    print('❌ Pattern not found')
