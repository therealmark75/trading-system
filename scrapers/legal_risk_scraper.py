import requests
import sqlite3
import time
import re
import logging
from datetime import datetime, timedelta
from config.constants import DATABASE_PATH as DB_PATH

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
    RISK_NONE:               0,
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


def _classify(text, ticker=None):
    t = text.lower()

    # ── Patent / USPTO / IP proceedings exclusion ────────────────────────────
    # Patent disputes are routine business, not legal risk in the SEC/fraud sense.
    # If the text is dominated by IP/patent language, skip ALL enforcement checks.
    patent_signals = [
        "inter partes review", "post-grant review", "patent trial and appeal board",
        "ptab", "uspto", "u.s. patent and trademark", "patent infringement",
        "alleged infringement", "infringement claims", "infringes our patents",
        "our intellectual property", "intellectual property rights",
        "trade secret misappropriation", "patent invalidation", "invalidating our patents",
        "claims of patent infringement", "claimed infringement", "patent litigation",
        "patent dispute", "license agreement", "licensing dispute",
        "claims that our products infringe", "products infringe", "infringe upon",
    ]
    patent_score = sum(1 for p in patent_signals if p in t)
    is_patent_doc = patent_score >= 3

    # ── Policy/compliance document detection ─────────────────────────────────
    # Insider trading compliance policies and codes of conduct legitimately contain
    # penalty language in an educational/prescriptive context — not enforcement.
    # If 2+ policy signals are present, skip the enforcement checks entirely.
    policy_signals = [
        "insider trading policy", "insider trading compliance policy",
        "insider trading compliance program", "trading compliance policy",
        "securities trading policy", "blackout period", "pre-clearance",
        "trading window", "quiet period", "covered persons are prohibited",
        "employees are prohibited from trading", "our trading policy",
        "under our policy", "compliance with insider trading",
        "our insider trading", "this policy prohibits", "policy prohibits trading",
        "covered persons must", "covered employees must", "pre-approval",
        "company personnel are prohibited", "executive officers and directors are prohibited",
        "company has adopted", "adopted a policy", "our policies prohibit",
        "code of ethics", "code of conduct", "compliance program",
        "trading restrictions", "restricted trading",
    ]
    policy_score = sum(1 for p in policy_signals if p in t)
    is_policy_doc = policy_score >= 2

    # ── Hypothetical/forward-looking language ────────────────────────────────
    hypothetical = [
        # Forward-looking / conditional language
        "may be subject to", "could be subject to", "might be subject to",
        "may in the future be", "may from time to time become", "may become subject",
        "could face", "may face", "risk of", "risks include", "could result in",
        "there can be no assurance", "we cannot predict", "if we are subject",
        "if the commission", "if a court", "if we are found", "if such",
        "it can issue", "it could issue", "may impose", "could impose",
        "potential liability", "could be required", "may be required",
        "an outcome could", "such an outcome", "could materially",
        "civil penalties of up to", "disgorgement of profits", "could issue a cease",
        "might result in", "may result in", "could be ordered to pay",
        "in the event that", "if the sec", "if regulators", "were to impose",
        "failure to comply", "noncompliance could", "subject to sanctions",
        "exposure to penalties", "potential civil penalty", "potential disgorgement",
        "penalties can include", "penalties may include", "penalties include",
        "consequences include", "subject to civil penalties", "including civil penalties",
        "subject to disgorgement", "including disgorgement",
        "violations may result", "violations could result", "violations can result",
        "may be involved in", "could be involved in", "from time to time",
        "could subject us to", "may subject us to", "could expose us to",
        # Explicit negation — company states absence of litigation
        "there is no pending", "no pending litigation", "no material pending",
        "not a party to any pending", "no legal proceedings", "no material legal proceedings",
        "no current legal proceedings", "are not currently a party",
        "are not a party to any", "we are not aware of any pending",
        # Accounting/accrual context — "settled for" in reserve/estimate language
        "reserved item", "accrual", "accrued liabilities", "previously accrued",
        "reserve for", "amount exceeding the previous estim",
        # Routine/ordinary-course language — not indicative of misconduct
        "ordinary course of business", "in the ordinary course",
        "various legal proceedings", "ordinary business",
        "may become a party to", "may become parties to",
        "routine legal proceedings", "normal course of business",
        # Pharma/biotech Paragraph IV — routine generic drug challenge process
        "paragraph iv certification", "paragraph iv certif",
        "paragraph iv",           # catches "paragraph iv challenges" etc.
        "hatch-waxman", "anda filer", "anda filing",
        "paragraph iv patent", "inter partes review",
        "post-grant review",
        # Industry-level descriptions (boilerplate section headers)
        "in the biotechnology and pharmaceutical industries",
        "in the pharmaceutical industry",
        "involving patent and other intellectual property rights",
        "patent infringement lawsuits, interferences",
        "including patent infringement lawsuits",
        # Risk-factor list language — "litigation, settlement costs" as category listing
        "settlement costs, regulatory scrutiny",
        "regulatory scrutiny and government enforcement",
        "a variety of matters, including employment",
        # Past/completed settlements — risk is resolved, not ongoing
        "we settled our",         # "we settled our disputes/patent/claims with X"
        # Business agreements that contain "party to" but are not litigation
        "joint venture",          # "we are party to [JV]" = normal business partnership
        "supply agreement",       # "we are party to a [supply agreement]"
        "license agreement",      # "we are party to a [license agreement]"
        # EPA environmental cleanup — not SEC/fraud type legal risk
        "superfund",              # "named as responsible party at superfund sites"
        "national priorities list",
        "hazardous material releases",
        "environmental cleanup",
    ]

    def is_hypothetical(snippet):
        return any(h in snippet for h in hypothetical)

    # ── Criminal ─────────────────────────────────────────────────────────────
    for kw in ["grand jury", "indictment", "doj has filed", "department of justice has",
               "criminal charges filed", "criminal complaint filed", "pleaded guilty",
               "criminal conviction"]:
        if kw in t:
            idx = t.find(kw)
            ctx = t[max(0, idx-100):idx+100]
            if not is_hypothetical(ctx):
                return RISK_CRIMINAL

    # ── SEC Enforcement ──────────────────────────────────────────────────────
    # Never classify a policy/compliance document as enforcement.
    # Tier 1: single match sufficient — language that only appears in actual enforcement orders
    # Tier 2: moderate signals — require 2+ non-hypothetical hits to reduce false positives
    if not is_policy_doc and not is_patent_doc:
        # Tier 1 — extremely specific to actual enforcement; one match is conclusive
        enforcement_definite = [
            "without admitting or denying",           # SEC settlement boilerplate
            "accepted and consented to the entry of", # SEC administrative order
            "consent order was entered",
            "consent decree was entered",
            "administrative law judge ruled",
            "civil penalty of $",         # actual dollar amount (not "of up to $X")
            "paid a civil penalty",
            "paid civil penalties of",
            "ordered to pay disgorgement",
            "agreed to pay disgorgement of $",
            "we settled charges with the sec",
            "settled sec charges",
            "settled with the sec for $",
            "judgment was entered against us",
            "judgment was entered against the company",
            "cease and desist order was issued to us",
            "was issued a cease and desist order",
            "we were charged by the sec",
            "company was charged by the sec",
            "the commission charged us",
            "sec charged us",
        ]
        if ticker:
            enforcement_definite.append(f"sec v. {ticker.lower()}")

        for kw in enforcement_definite:
            if kw in t:
                idx = t.find(kw)
                ctx = t[max(0, idx-400):idx+400]
                if not is_hypothetical(ctx):
                    return RISK_SEC_ENFORCEMENT

        # Tier 2 — weaker signals; only classify if 2+ non-hypothetical hits co-occur
        enforcement_moderate = [
            "sec has filed",
            "commission has filed",
            "cease and desist order",
            "administrative proceeding has been",
            "disgorgement of",
            "civil penalty of",
            "sec obtained a judgment",
            "sec obtained",
        ]
        moderate_hits = 0
        for kw in enforcement_moderate:
            if kw in t:
                idx = t.find(kw)
                ctx = t[max(0, idx-600):idx+600]
                if not is_hypothetical(ctx):
                    moderate_hits += 1
        if moderate_hits >= 2:
            return RISK_SEC_ENFORCEMENT

    # ── SEC Investigation ────────────────────────────────────────────────────
    for kw in ["wells notice", "formal order of investigation",
               "we received a subpoena from the sec",
               "sec staff has informed", "sec has opened",
               "under formal investigation"]:
        if kw in t:
            return RISK_SEC_INVESTIGATION

    # ── Class action ─────────────────────────────────────────────────────────
    # Require present-tense active litigation. Past settlements/dismissals are not
    # active risk. Skip if patent context dominates.
    class_action_kws = [
        "class action lawsuit", "class action complaint", "putative class action",
        "securities class action", "class action has been filed", "class action was filed",
        "class action captioned",
    ]
    if ticker:
        class_action_kws.append(f"v. {ticker.lower()}")
    # Signals that indicate the action is resolved/dismissed (not active)
    resolved_signals = [
        "class action was dismissed", "class action has been dismissed",
        "dismissal of the class action", "settled the class action",
        "class action settled", "settlement of the class action",
        "class was decertified", "motion to dismiss was granted",
        "complaint was dismissed with prejudice",
    ]
    for kw in class_action_kws:
        if kw in t and not is_patent_doc:
            idx = t.find(kw)
            ctx = t[max(0, idx-300):idx+300]
            if not is_hypothetical(ctx) and not any(r in ctx for r in resolved_signals):
                return RISK_CLASS_ACTION

    # ── Minor ────────────────────────────────────────────────────────────────
    # Require present-tense active language — skip if context is forward-looking/hypothetical.
    for kw in ["we are party to", "we are a defendant", "plaintiff alleges",
               "complaint alleges", "we have been named", "pending litigation",
               "settled for", "we settled"]:
        if kw in t:
            idx = t.find(kw)
            ctx = t[max(0, idx-400):idx+400]
            if not is_hypothetical(ctx):
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

        # Strip HTML tags so is_hypothetical() sees clean prose context rather than
        # CSS/markup noise, which pollutes the ±400-char window around keyword matches
        clean_text = re.sub(r"<[^>]+>", " ", doc_r.text)
        clean_text = re.sub(r"&#[0-9]+;", " ", clean_text)
        clean_text = re.sub(r"\s+", " ", clean_text)

        # Classify full document text for high-confidence signals
        full_risk = _classify(clean_text, ticker)
        logger.info(f"[LegalRisk] {ticker} 10-K full doc risk: {RISK_LABELS[full_risk]}")

        if full_risk != RISK_NONE:
            highest_risk = _escalate(highest_risk, full_risk)
            snippet = _best_legal_block(doc_r.text)
            snippet_clean = re.sub(r"\s+", " ", snippet).strip()[:300]
            findings.append({
                "type":        RISK_LABELS[full_risk],
                "entity":      ticker,
                "date":        filing["date"],
                "description": f"10-K Legal Proceedings: {snippet_clean}..." if snippet_clean else "See 10-K filing",
                "source_url":  doc_url,
                "risk_level":  full_risk,
                "confidence":  "HIGH",
                "filing_type": "10-K",
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
        risk = _classify(index_text, ticker)
        if risk not in (RISK_NONE, RISK_MINOR):
            highest_risk = _escalate(highest_risk, risk)
            findings.append({
                "type":        RISK_LABELS[risk],
                "entity":      ticker,
                "date":        filing["date"],
                "description": f"8-K Material Legal Event (Items: {filing.get('items','N/A')})",
                "source_url":  f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/index.json",
                "risk_level":  risk,
                "confidence":  "MEDIUM",
                "filing_type": "8-K",
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
    # Derive primary filing type from the most severe finding
    filing_type = None
    for f in findings:
        if f.get("risk_level") == risk_level:
            filing_type = f.get("filing_type")
            break
    return {
        "ticker":      ticker,
        "risk_level":  risk_level,
        "risk_label":  RISK_LABELS[risk_level],
        "risk_color":  RISK_COLORS[risk_level],
        "penalty":     RISK_PENALTIES[risk_level],
        "findings":    findings,
        "filing_type": filing_type,
        "scraped_at":  datetime.now().isoformat(),
    }


def save_legal_risk(ticker, result):
    import json
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Ensure filing_type column exists (idempotent)
    try:
        c.execute("ALTER TABLE legal_risk ADD COLUMN filing_type TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    c.execute("""
        INSERT OR REPLACE INTO legal_risk
            (ticker, risk_level, risk_label, risk_color, penalty, findings_json, filing_type, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker, result["risk_level"], result["risk_label"], result["risk_color"],
          result["penalty"], json.dumps(result["findings"]),
          result.get("filing_type"), result["scraped_at"]))
    conn.commit()
    conn.close()


def get_legal_risk(ticker):
    import json
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM legal_risk WHERE ticker = ?", (ticker,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["findings"] = json.loads(d.get("findings_json") or "[]")
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
