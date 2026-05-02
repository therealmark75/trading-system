#!/usr/bin/env python3
"""
Rewrites scrapers/legal_risk_scraper.py to use CIK-based EDGAR approach.
Run from ~/Documents/trading-system
"""

scraper = '''import requests
import sqlite3
import time
import re
import logging
from datetime import datetime, timedelta
from config.settings import DATABASE_PATH as DB_PATH

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "SignalIntel marknorthall@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

# ── Risk levels ───────────────────────────────────────────────────────────────
RISK_NONE             = "NONE"
RISK_MINOR            = "MINOR"
RISK_CLASS_ACTION     = "CLASS_ACTION"
RISK_SEC_INVESTIGATION= "SEC_INVESTIGATION"
RISK_SEC_ENFORCEMENT  = "SEC_ENFORCEMENT"
RISK_CRIMINAL         = "CRIMINAL"

RISK_PENALTIES = {
    RISK_NONE:              2,   # clean record slight positive
    RISK_MINOR:            -5,
    RISK_CLASS_ACTION:    -15,
    RISK_SEC_INVESTIGATION:-25,
    RISK_SEC_ENFORCEMENT: -40,
    RISK_CRIMINAL:        -60,
}

RISK_ORDER = [
    RISK_NONE, RISK_MINOR, RISK_CLASS_ACTION,
    RISK_SEC_INVESTIGATION, RISK_SEC_ENFORCEMENT, RISK_CRIMINAL
]

RISK_LABELS = {
    RISK_NONE:             "None",
    RISK_MINOR:            "Minor",
    RISK_CLASS_ACTION:     "Class Action",
    RISK_SEC_INVESTIGATION:"SEC Investigation",
    RISK_SEC_ENFORCEMENT:  "SEC Enforcement",
    RISK_CRIMINAL:         "Criminal / DOJ",
}

RISK_COLORS = {
    RISK_NONE:             "#22c55e",
    RISK_MINOR:            "#84cc16",
    RISK_CLASS_ACTION:     "#f59e0b",
    RISK_SEC_INVESTIGATION:"#f97316",
    RISK_SEC_ENFORCEMENT:  "#ef4444",
    RISK_CRIMINAL:         "#7c2d12",
}

# Cache ticker->CIK mapping in memory
_CIK_CACHE = {}

def _get(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
        elif r.status_code == 429:
            time.sleep(3)
    except Exception as e:
        logger.warning(f"Request failed: {url} — {e}")
    return None

def _escalate_risk(current, candidate):
    ci = RISK_ORDER.index(current) if current in RISK_ORDER else 0
    ca = RISK_ORDER.index(candidate) if candidate in RISK_ORDER else 0
    return RISK_ORDER[max(ci, ca)]

def _resolve_cik(ticker):
    """Resolve ticker to zero-padded CIK using EDGAR company_tickers.json"""
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]

    r = _get("https://www.sec.gov/files/company_tickers.json")
    if not r:
        return None

    data = r.json()
    for val in data.values():
        if val.get("ticker", "").upper() == ticker.upper():
            cik = str(val["cik_str"]).zfill(10)
            _CIK_CACHE[ticker] = cik
            logger.info(f"[LegalRisk] {ticker} -> CIK {cik} ({val.get('title','')})")
            return cik

    logger.warning(f"[LegalRisk] CIK not found for {ticker}")
    return None

def _get_filings(cik, forms=("10-K", "8-K"), max_age_days=730):
    """Pull recent filings from EDGAR submissions API."""
    r = _get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if not r:
        return []

    data = r.json()
    recent = data.get("filings", {}).get("recent", {})
    all_forms    = recent.get("form", [])
    all_dates    = recent.get("filingDate", [])
    all_accnums  = recent.get("accessionNumber", [])
    all_descs    = recent.get("items", [])  # 8-K item numbers

    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")

    results = []
    for i, form in enumerate(all_forms):
        if form not in forms:
            continue
        date = all_dates[i] if i < len(all_dates) else ""
        if date < cutoff:
            continue
        accnum = all_accnums[i] if i < len(all_accnums) else ""
        desc   = all_descs[i] if i < len(all_descs) else ""
        results.append({
            "form": form,
            "date": date,
            "accession": accnum.replace("-", ""),
            "accession_raw": accnum,
            "items": desc,
        })

    return results

def _fetch_filing_text(cik, accession, max_chars=50000):
    """Fetch the text of a filing document."""
    # Build index URL
    acc_fmt = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_fmt}/{accession}-index.htm"
    r = _get(index_url)
    if not r:
        # Try without dashes
        index_url2 = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K&dateb=&owner=include&count=1"
        return ""

    # Find the main document link
    htm_match = re.search(r'href="(/Archives/edgar/data/[^"]+\.htm)"', r.text, re.IGNORECASE)
    if not htm_match:
        return ""

    doc_url = "https://www.sec.gov" + htm_match.group(1)
    doc_r = _get(doc_url)
    if not doc_r:
        return ""

    return doc_r.text[:max_chars]

def _classify_text(text):
    """Classify filing text into risk level."""
    t = text.lower()

    if any(k in t for k in ["criminal", "department of justice", "doj", "grand jury", "indictment", "criminal charges"]):
        return RISK_CRIMINAL

    if any(k in t for k in ["sec enforcement action", "cease and desist", "administrative proceeding",
                              "civil money penalty", "sec has filed", "commission has filed",
                              "disgorgement", "administrative law judge"]):
        return RISK_SEC_ENFORCEMENT

    if any(k in t for k in ["sec investigation", "sec inquiry", "wells notice",
                              "formal order of investigation", "under investigation by the sec",
                              "sec is investigating", "commission is investigating"]):
        return RISK_SEC_INVESTIGATION

    if any(k in t for k in ["class action", "putative class", "securities class action",
                              "shareholder class action", "securities fraud class"]):
        return RISK_CLASS_ACTION

    if any(k in t for k in ["lawsuit", "litigation", "legal proceedings", "complaint",
                              "arbitration", "settlement", "alleged", "plaintiff"]):
        return RISK_MINOR

    return RISK_NONE

def _extract_legal_section(text):
    """Extract the Legal Proceedings section from a 10-K."""
    # Common patterns for legal proceedings section headers
    patterns = [
        r"LEGAL PROCEEDINGS(.{200,8000}?)(?:ITEM\s+\d|RISK FACTORS|UNRESOLVED|$)",
        r"Legal Proceedings(.{200,8000}?)(?:Item\s+\d|Risk Factors|Unresolved|$)",
        r"Item\s+3\.?\s+Legal Proceedings(.{100,6000}?)(?:Item\s+4|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)[:3000]
    return ""

def fetch_legal_risk(ticker):
    """Main entry point - fetch legal risk for a ticker from SEC EDGAR."""
    findings = []
    highest_risk = RISK_NONE

    # Step 1: Resolve CIK
    cik = _resolve_cik(ticker)
    if not cik:
        return _build_result(ticker, RISK_NONE, [], note="Ticker not found in EDGAR")

    time.sleep(0.2)

    # Step 2: Get recent filings
    filings = _get_filings(cik, forms=("10-K", "8-K"), max_age_days=730)
    logger.info(f"[LegalRisk] {ticker}: {len(filings)} filings found (10-K + 8-K)")

    # Step 3: Check 10-K legal proceedings section (most reliable)
    tenk_filings = [f for f in filings if f["form"] == "10-K"][:2]  # last 2 annual reports
    for filing in tenk_filings:
        acc = filing["accession_raw"]
        acc_nodash = acc.replace("-", "")
        cik_int = int(cik)

        # Try to get filing index
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}-index.htm"
        r = _get(index_url)
        time.sleep(0.15)

        if not r:
            continue

        # Find main document
        htm_match = re.search(
            r'href="(/Archives/edgar/data/[^"]+?(?:10k|10-k|annual)[^"]*?\.htm)"',
            r.text, re.IGNORECASE
        )
        if not htm_match:
            # Fallback: grab first .htm that isn't an exhibit
            htm_match = re.search(
                r'<td scope="row"><a href="(/Archives/edgar/data/' + str(cik_int) + r'/[^"]+\.htm)"',
                r.text, re.IGNORECASE
            )

        if not htm_match:
            continue

        doc_url = "https://www.sec.gov" + htm_match.group(1)
        doc_r = _get(doc_url)
        time.sleep(0.2)

        if not doc_r:
            continue

        # Extract legal proceedings section
        legal_section = _extract_legal_section(doc_r.text[:80000])
        if legal_section:
            risk = _classify_text(legal_section)
            if risk != RISK_NONE:
                highest_risk = _escalate_risk(highest_risk, risk)
                # Extract a meaningful snippet
                snippet = re.sub(r'<[^>]+>', ' ', legal_section)
                snippet = re.sub(r'\s+', ' ', snippet).strip()[:300]
                findings.append({
                    "type": RISK_LABELS[risk],
                    "entity": ticker,
                    "date": filing["date"],
                    "description": f"10-K Legal Proceedings: {snippet}...",
                    "source_url": doc_url,
                    "risk_level": risk,
                })
                logger.info(f"[LegalRisk] {ticker} 10-K ({filing['date']}): {RISK_LABELS[risk]}")
        else:
            logger.info(f"[LegalRisk] {ticker} 10-K ({filing['date']}): no legal section found")

    # Step 4: Check 8-K filings for item 1.01 (legal/regulatory material events)
    # Item 8.01 = other events, Item 1.01 = material agreements
    # We look for 8-Ks filed around known enforcement keywords
    eightk_filings = [f for f in filings if f["form"] == "8-K"][:10]
    legal_8k_keywords = ["sec", "enforcement", "investigation", "class action", "lawsuit",
                          "litigation", "settlement", "doj", "criminal", "subpoena"]

    for filing in eightk_filings:
        items = (filing.get("items") or "").lower()
        # Only fetch 8-Ks that mention legal-adjacent item numbers (9.01 = exhibits, skip)
        # Item 8.01 = other events often includes legal notices
        if not items or "8.01" in items or "1.01" in items or not items:
            acc = filing["accession_raw"]
            acc_nodash = acc.replace("-", "")
            cik_int = int(cik)

            index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}-index.htm"
            r = _get(index_url)
            time.sleep(0.1)

            if not r:
                continue

            # Check if any legal keywords appear in the index page itself
            index_text = r.text.lower()
            if not any(k in index_text for k in legal_8k_keywords):
                continue

            # If keywords found, classify the index text
            risk = _classify_text(index_text)
            if risk not in (RISK_NONE, RISK_MINOR):
                highest_risk = _escalate_risk(highest_risk, risk)
                findings.append({
                    "type": RISK_LABELS[risk],
                    "entity": ticker,
                    "date": filing["date"],
                    "description": f"8-K Material Event (Items: {filing.get('items','N/A')})",
                    "source_url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}-index.htm",
                    "risk_level": risk,
                })
                logger.info(f"[LegalRisk] {ticker} 8-K ({filing['date']}): {RISK_LABELS[risk]}")

    # De-dupe
    seen = set()
    unique = []
    for f in findings:
        key = (f["type"], f["date"][:7])  # month-level dedup
        if key not in seen:
            seen.add(key)
            unique.append(f)

    unique.sort(key=lambda x: x.get("date",""), reverse=True)
    return _build_result(ticker, highest_risk, unique[:6])

def _build_result(ticker, risk_level, findings, note=None):
    return {
        "ticker": ticker,
        "risk_level": risk_level,
        "risk_label": RISK_LABELS[risk_level],
        "risk_color": RISK_COLORS[risk_level],
        "penalty": RISK_PENALTIES[risk_level],
        "findings": findings,
        "note": note,
        "scraped_at": datetime.now().isoformat(),
    }

def save_legal_risk(ticker, result):
    import json
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO legal_risk
            (ticker, risk_level, risk_label, risk_color, penalty, findings_json, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker,
        result["risk_level"],
        result["risk_label"],
        result["risk_color"],
        result["penalty"],
        json.dumps(result["findings"]),
        result["scraped_at"],
    ))
    conn.commit()
    conn.close()

def get_legal_risk(ticker):
    """Fetch cached legal risk from DB."""
    import json
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM legal_risk WHERE ticker = ?", (ticker,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["id","ticker","risk_level","risk_label","risk_color","penalty","findings_json","scraped_at"]
    d = dict(zip(cols, row))
    d["findings"] = json.loads(d["findings_json"] or "[]")
    return d

def scrape_priority_tickers():
    """Scrape legal risk for watchlist + top signal tickers."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    tickers = set()
    try:
        c.execute("SELECT DISTINCT ticker FROM watchlists")
        tickers.update(r[0] for r in c.fetchall())
    except:
        pass
    try:
        c.execute("SELECT ticker FROM signal_scores ORDER BY composite_score DESC LIMIT 50")
        tickers.update(r[0] for r in c.fetchall())
    except:
        pass
    conn.close()

    logger.info(f"[LegalRisk] Scraping {len(tickers)} priority tickers")
    scraped = 0
    for ticker in sorted(tickers):
        try:
            result = fetch_legal_risk(ticker)
            save_legal_risk(ticker, result)
            scraped += 1
            logger.info(f"[LegalRisk] {ticker}: {result['risk_label']} (penalty: {result['penalty']})")
            time.sleep(1)  # be polite to SEC servers
        except Exception as e:
            logger.error(f"[LegalRisk] {ticker} failed: {e}")

    logger.info(f"[LegalRisk] Done. {scraped}/{len(tickers)} scraped.")
    return scraped
'''

import os
path = os.path.expanduser("~/Documents/trading-system/scrapers/legal_risk_scraper.py")
with open(path, "w") as f:
    f.write(scraper)
print(f"✅ Rewrote: {path}")
print()
print("Now test with:")
print('python -c "')
print('import logging; logging.basicConfig(level=logging.INFO)')
print('from scrapers.legal_risk_scraper import fetch_legal_risk, save_legal_risk')
print('r = fetch_legal_risk(\'COIN\')')
print('save_legal_risk(\'COIN\', r)')
print('print(r[\'risk_label\'], \'|\', r[\'penalty\'], \'|\', len(r[\'findings\']), \'findings\')')
print('for f in r[\'findings\']: print(\' -\', f[\'type\'], \'|\', f[\'date\'], \'|\', f[\'description\'][:80])')
print('"')
