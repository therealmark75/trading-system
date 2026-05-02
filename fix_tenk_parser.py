with open('scrapers/legal_risk_scraper.py', 'r') as f:
    content = f.read()

old = '''        legal_section = _extract_legal_section(doc_r.text[:80000])'''

new = '''        # 10-Ks are large - search full document for legal content
        full_text = doc_r.text
        legal_section = _extract_legal_section(full_text)
        # If no section found via headers, scan full doc for legal keywords
        if not legal_section:
            legal_section = _scan_full_doc(full_text)'''

content = content.replace(old, new)

# Add the _scan_full_doc helper before fetch_legal_risk
old2 = '''def fetch_legal_risk(ticker):'''

new2 = '''def _scan_full_doc(text):
    """Fallback: find richest legal content block in document."""
    indices = [m.start() for m in re.finditer('legal proceedings', text, re.IGNORECASE)]
    best = ""
    for idx in indices:
        chunk = text[idx:idx+5000]
        clean = re.sub(r'<[^>]+>', ' ', chunk)
        clean = re.sub(r'\s+', ' ', clean).strip()
        # Pick longest substantive block (skip TOC entries)
        if len(clean) > len(best) and len(clean) > 300:
            best = clean
    return best[:5000]

def fetch_legal_risk(ticker):'''

content = content.replace(old2, new2)

# Also fix the document URL - use JSON index to find main doc
old3 = '''        # Try to get filing index
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}-index.htm"
        r = _get(index_url)
        time.sleep(0.15)

        if not r:
            continue

        # Find main document
        htm_match = re.search(
            r\'href="(/Archives/edgar/data/[^"]+?(?:10k|10-k|annual)[^"]*?\\.htm)"\',
            r.text, re.IGNORECASE
        )
        if not htm_match:
            # Fallback: grab first .htm that isn\'t an exhibit
            htm_match = re.search(
                r\'<td scope="row"><a href="(/Archives/edgar/data/\' + str(cik_int) + r\'/[^"]+\\.htm)"\',
                r.text, re.IGNORECASE
            )

        if not htm_match:
            continue

        doc_url = "https://www.sec.gov" + htm_match.group(1)'''

new3 = '''        # Use JSON index to find main document reliably
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
            continue'''

content = content.replace(old3, new3)

with open('scrapers/legal_risk_scraper.py', 'w') as f:
    f.write(content)
print('✅ Parser updated - JSON index + full doc scan')
