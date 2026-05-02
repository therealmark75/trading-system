import requests
import sqlite3
import time
import re
import logging
from datetime import datetime, timedelta
from config.settings import DATABASE_PATH as DB_PATH

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "SignalIntel marknorthall@gmail.com"}

RISK_NONE              = "NONE"
RISK_MINOR             = "MINOR"
RISK_CLASS_ACTION      = "CLASS_ACTION"
RISK_SEC_INVESTIGATION = "SEC_INVESTIGATION"
RISK_SEC_ENFORCEMENT   = "SEC_ENFORCEMENT"
RISK_CRIMINAL          = "CRIMINAL"

RISK_ORDER = [RISK_NONE, RISK_MINOR, RISK_CLASS_ACTION,
              RISK_SEC_INVESTIGATION, RISK_SEC_ENFORCEMENT, RISK_CRIMINAL]

RISK_LABELS = {
    RISK_NONE:              "None",
    RISK_MINOR:             "Minor",
    RISK_CLASS_ACTION:      "Class Action",
    RISK_SEC_INVESTIGATION: "SEC Investigation",
    RISK_SEC_ENFORCEMENT:   "SEC Enforcement",
    RISK_CRIMINAL:          "Criminal / DOJ",
}

RISK_COLORS = {
    RISK_NONE:              "#22c55e",
    RISK_MINOR:             "#84cc16",
    RISK_CLASS_ACTION:      "#f59e0b",
    RISK_SEC_INVESTIGATION: "#f97316",
    RISK_SEC_ENFORCEMENT:   "#ef4444",
    RISK_CRIMINAL:          "#7c2d12",
}

RISK_PENALTIES = {
    RISK_NONE:               2,
    RISK_MINOR:             -5,
    RISK_CLASS_ACTION:     -15,
    RISK_SEC_INVESTIGATION: -25,
    RISK_SEC_ENFORCEMENT:  -40,
    RISK_CRIMINAL:         -60,
}

_CIK_CACHE = {}


def _get(url, timeout=90):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            elif r.status_code == 429:
                logger.warning("[LegalRisk] Rate limited, sleeping 5s")
                time.sleep(5)
            else:
                logger.warning(f"[LegalRisk] HTTP {r.status_code}: {url[-80:]}")
                return None
        except Exception as e:
            logger.warning(f"[LegalRisk] Request attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return None


def _escalate(current, candidate):
    ci = RISK_ORDER.index(current) if current in RISK_ORDER else 0
    ca = RISK_ORDER.index(candidate) if candidate in RISK_ORDER else 0
    return RISK_ORDER[max(ci, ca)]


def _classify(text):
    t = text.lower()

    # Skip hypothetical/forward-looking risk language
    hypothetical = ["may be subject to", "could be subject to", "might be subject to",
                    "could face", "may face", "risk of", "risks include", "could result in",
                    "there can be no assurance", "we cannot predict", "if we are subject",
                    "if the commission", "if a court", "if we are found", "if such",
                    "it can issue", "it could issue", "may impose", "could impose",
                    "potential liability", "could be required", "may be required",
                    "an outcome could", "such an outcome", "could materially"]

    def is_hypothetical(snippet):
        return any(h in snippet for h in hypothetical)

    # Criminal - only if active/concrete, not hypothetical
    for kw in ["grand jury", "indictment", "doj has filed", "department of justice has",
               "criminal charges filed", "criminal complaint filed", "pleaded guilty",
               "criminal conviction"]:
        if kw in t:
            idx = t.find(kw)
            ctx = t[max(0,idx-100):idx+100]
            if not is_hypothetical(ctx):
                return RISK_CRIMINAL

    # SEC Enforcement - active actions only, not hypothetical risk language
    for kw in ["sec has filed", "commission has filed", "cease and desist order",
               "administrative proceeding has", "civil penalty of", "disgorgement of",
               "administrative law judge ruled", "sec obtained"]:
        if kw in t:
            idx = t.find(kw)
            # Widen context window - hypothetical markers can be sentences before/after
            ctx = t[max(0,idx-400):idx+400]
            if not is_hypothetical(ctx):
                return RISK_SEC_ENFORCEMENT

    # SEC Investigation - active
    for kw in ["wells notice", "formal order of investigation", "we received a subpoena from the sec",
               "sec staff has informed", "sec has opened", "under formal investigation"]:
        if kw in t:
            return RISK_SEC_INVESTIGATION

    # Class action - filed cases
    for kw in ["class action lawsuit", "class action complaint", "putative class action",
               "securities class action", "class action has been filed", "class action was filed",
               "class action captioned", "v. " + t[:10].strip()]:
        if kw in t:
            return RISK_CLASS_ACTION

    # Minor - general litigation language in legal proceedings section
    for kw in ["we are party to", "we are a defendant", "plaintiff alleges",
               "complaint alleges", "we have been named", "pending litigation",
               "settled for", "we settled"]:
        if kw in t:
            return RISK_MINOR

    return RISK_NONE


def _resolve_cik(ticker):
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]
    r = _get("https://www.sec.gov/files/company_tickers.json")
    if not r:
        return None
    for val in r.json().values():
        if val.get("ticker", "").upper() == ticker.upper():
            cik = str(val["cik_str"]).zfill(10)
            _CIK_CACHE[ticker] = cik
            logger.info(f"[LegalRisk] {ticker} -> CIK {cik} ({val.get('title','')})")
            return cik
    logger.warning(f"[LegalRisk] CIK not found for {ticker}")
    return None


def _get_filings(cik, max_age_days=730):
    r = _get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if not r:
        return [], []
    data = r.json()
    recent = data.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    dates   = recent.get("filingDate", [])
    accnums = recent.get("accessionNumber", [])
    items   = recent.get("items", [])
    cutoff  = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")

    tenk, eightk = [], []
    for i, form in enumerate(forms):
        date = dates[i] if i < len(dates) else ""
        if date < cutoff:
            continue
        acc = accnums[i] if i < len(accnums) else ""
        item = items[i] if i < len(items) else ""
        entry = {"date": date, "accession_raw": acc, "items": item}
        if form == "10-K":
            tenk.append(entry)
        elif form == "8-K":
            eightk.append(entry)
    return tenk[:2], eightk[:10]


def _get_main_doc(cik_int, acc_raw):
    acc_nodash = acc_raw.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
    r = _get(index_url)
    time.sleep(0.15)
    if not r:
        return None, None
    try:
        items = r.json().get("directory", {}).get("item", [])
        for item in items:
            name = item.get("name", "")
            if (name.endswith(".htm") and
                    "exhibit" not in name.lower() and
                    "index" not in name.lower() and
                    not name.startswith("0") and
                    not name.startswith("R")):
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{name}"
                return name, doc_url
    except Exception as e:
        logger.warning(f"[LegalRisk] index.json parse error: {e}")
    return None, None


def _best_legal_block(text):
    """Find the richest legal content block in a document."""
    high_value = ["class action", "sec", "enforcement", "lawsuit", "settlement",
                  "subpoena", "investigation", "plaintiff", "complaint", "et al",
                  "securities fraud", "doj", "criminal"]
    indices = [m.start() for m in re.finditer(
        r"legal proceedings|class action|sec enforcement|sec investigation|subpoena|litigation",
        text, re.IGNORECASE
    )]
    best, best_score = "", 0
    for idx in indices:
        chunk = text[idx:idx + 8000]
        clean = re.sub(r"<[^>]+>", " ", chunk)
        clean = re.sub(r"&#[0-9]+;", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) < 200:
            continue
        score = sum(1 for t in high_value if t in clean.lower()) + len(clean) // 500
        if score > best_score:
            best_score = score
            best = clean
    return best[:6000]


def fetch_legal_risk(ticker):
    findings = []
    highest_risk = RISK_NONE

    cik = _resolve_cik(ticker)
    if not cik:
        return _build(ticker, RISK_NONE, [])

    time.sleep(0.2)
    tenk_filings, eightk_filings = _get_filings(cik)
    logger.info(f"[LegalRisk] {ticker}: {len(tenk_filings)} 10-K, {len(eightk_filings)} 8-K filings")

    cik_int = int(cik)

    # ── 10-K analysis ────────────────────────────────────────────────────────
    for filing in tenk_filings:
        acc = filing["accession_raw"]
        doc_name, doc_url = _get_main_doc(cik_int, acc)

        if not doc_name:
            logger.warning(f"[LegalRisk] No main doc found for {ticker} 10-K {acc}")
            continue

        logger.info(f"[LegalRisk] Fetching {doc_name} for {ticker}")
        doc_r = _get(doc_url)
        time.sleep(0.3)

        if not doc_r:
            logger.warning(f"[LegalRisk] Failed to fetch {doc_name}")
            continue

        logger.info(f"[LegalRisk] {ticker} 10-K fetched ({len(doc_r.text):,} chars)")

        # Classify full document text for high-confidence signals
        full_risk = _classify(doc_r.text)
        logger.info(f"[LegalRisk] {ticker} 10-K full doc risk: {RISK_LABELS[full_risk]}")

        if full_risk != RISK_NONE:
            highest_risk = _escalate(highest_risk, full_risk)
            # Get best snippet for display
            snippet = _best_legal_block(doc_r.text)
            snippet_clean = re.sub(r"\s+", " ", snippet).strip()[:300]
            findings.append({
                "type": RISK_LABELS[full_risk],
                "entity": ticker,
                "date": filing["date"],
                "description": f"10-K Legal Proceedings: {snippet_clean}..." if snippet_clean else "See 10-K filing",
                "source_url": doc_url,
                "risk_level": full_risk,
            })

    # ── 8-K analysis ─────────────────────────────────────────────────────────
    legal_keywords = ["sec", "enforcement", "investigation", "class action",
                      "lawsuit", "litigation", "settlement", "doj", "criminal", "subpoena"]

    for filing in eightk_filings:
        acc = filing["accession_raw"]
        acc_nodash = acc.replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json"
        r = _get(index_url)
        time.sleep(0.1)
        if not r:
            continue
        try:
            index_text = r.text.lower()
        except Exception:
            continue
        if not any(k in index_text for k in legal_keywords):
            continue
        risk = _classify(index_text)
        if risk not in (RISK_NONE, RISK_MINOR):
            highest_risk = _escalate(highest_risk, risk)
            findings.append({
                "type": RISK_LABELS[risk],
                "entity": ticker,
                "date": filing["date"],
                "description": f"8-K Material Legal Event (Items: {filing.get('items','N/A')})",
                "source_url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json",
                "risk_level": risk,
            })

    # De-dupe by month
    seen, unique = set(), []
    for f in findings:
        key = (f["type"], f["date"][:7])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    unique.sort(key=lambda x: x.get("date", ""), reverse=True)
    return _build(ticker, highest_risk, unique[:6])


def _build(ticker, risk_level, findings):
    return {
        "ticker": ticker,
        "risk_level": risk_level,
        "risk_label": RISK_LABELS[risk_level],
        "risk_color": RISK_COLORS[risk_level],
        "penalty": RISK_PENALTIES[risk_level],
        "findings": findings,
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
    """, (ticker, result["risk_level"], result["risk_label"], result["risk_color"],
          result["penalty"], json.dumps(result["findings"]), result["scraped_at"]))
    conn.commit()
    conn.close()


def get_legal_risk(ticker):
    import json
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM legal_risk WHERE ticker = ?", (ticker,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    cols = ["id", "ticker", "risk_level", "risk_label", "risk_color",
            "penalty", "findings_json", "scraped_at"]
    d = dict(zip(cols, row))
    d["findings"] = json.loads(d["findings_json"] or "[]")
    return d


def scrape_priority_tickers():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    tickers = set()
    try:
        c.execute("SELECT DISTINCT ticker FROM watchlists")
        tickers.update(r[0] for r in c.fetchall())
    except Exception:
        pass
    try:
        c.execute("SELECT ticker FROM signal_scores ORDER BY composite_score DESC LIMIT 50")
        tickers.update(r[0] for r in c.fetchall())
    except Exception:
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
            time.sleep(1)
        except Exception as e:
            logger.error(f"[LegalRisk] {ticker} failed: {e}")

    logger.info(f"[LegalRisk] Done. {scraped}/{len(tickers)} scraped.")
    return scraped
