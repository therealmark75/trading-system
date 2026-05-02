with open('scrapers/legal_risk_scraper.py', 'r') as f:
    content = f.read()

# Fix 1: Increase timeout for large document fetches
old = '''def _get(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
        elif r.status_code == 429:
            time.sleep(3)
    except Exception as e:
        logger.warning(f"Request failed: {url} — {e}")
    return None'''

new = '''def _get(url, timeout=60):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
        elif r.status_code == 429:
            time.sleep(3)
        else:
            logger.warning(f"Request returned {r.status_code}: {url}")
    except Exception as e:
        logger.warning(f"Request failed: {url} — {e}")
    return None'''

content = content.replace(old, new)

# Fix 2: Better _scan_full_doc - find richest block with actual case names
old2 = '''def _scan_full_doc(text):
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
    return best[:5000]'''

new2 = '''def _scan_full_doc(text):
    """Find the richest legal content block - prioritise blocks with case names/enforcement."""
    indices = [m.start() for m in re.finditer(r'legal proceedings|class action|sec enforcement|sec investigation|subpoena|litigation', text, re.IGNORECASE)]
    best = ""
    best_score = 0
    high_value_terms = ['class action', 'sec', 'enforcement', 'lawsuit', 'settlement',
                        'subpoena', 'investigation', 'plaintiff', 'complaint', 'v\\.', 'et al']
    for idx in indices:
        chunk = text[idx:idx+8000]
        clean = re.sub(r'<[^>]+>', ' ', chunk)
        clean = re.sub(r'&#[0-9]+;', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if len(clean) < 200:
            continue
        # Score by number of high-value legal terms
        score = sum(1 for t in high_value_terms if t in clean.lower())
        score += len(clean) // 500  # longer blocks score higher
        if score > best_score:
            best_score = score
            best = clean
    return best[:6000]'''

content = content.replace(old2, new2)

with open('scrapers/legal_risk_scraper.py', 'w') as f:
    f.write(content)
print('✅ Fixed timeout + improved doc scanner')
