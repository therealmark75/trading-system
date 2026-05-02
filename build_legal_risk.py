#!/usr/bin/env python3
"""
SignalIntel - SEC EDGAR Legal Risk System
Part 8 build script - run once from ~/Documents/trading-system
"""

import os, sys

BASE = os.path.expanduser("~/Documents/trading-system")

# ── 1. SCRAPER ────────────────────────────────────────────────────────────────
scraper = '''import requests
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from config.settings import DB_PATH

logger = logging.getLogger(__name__)

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index?q={query}&dateRange=custom&startdt={start}&enddt={end}&forms=10-K,8-K,DEF14A"
EDGAR_FULL_TEXT  = "https://efts.sec.gov/LATEST/search-index?q=%22legal+proceedings%22+%22{ticker}%22&forms=10-K&dateRange=custom&startdt={start}&enddt={end}"
EDGAR_LITRELEASES = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=LITRLS"
EDGAR_ENTITY_URL  = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_CIK_LOOKUP  = "https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={ticker}&type=&dateb=&owner=include&count=10&search_text=&action=getcompany&output=atom"

HEADERS = {
    "User-Agent": "SignalIntel marknorthall@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

# Risk level definitions - granular 6-tier system
RISK_NONE       = "NONE"
RISK_MINOR      = "MINOR"
RISK_CLASS_ACTION = "CLASS_ACTION"
RISK_SEC_INVESTIGATION = "SEC_INVESTIGATION"
RISK_SEC_ENFORCEMENT   = "SEC_ENFORCEMENT"
RISK_CRIMINAL   = "CRIMINAL"

RISK_PENALTIES = {
    RISK_NONE:             0,
    RISK_MINOR:           -5,
    RISK_CLASS_ACTION:   -15,
    RISK_SEC_INVESTIGATION: -25,
    RISK_SEC_ENFORCEMENT:  -40,
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

def _get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                time.sleep(2 ** attempt)
        except Exception as e:
            logger.warning(f"SEC EDGAR request failed (attempt {attempt+1}): {e}")
            time.sleep(1)
    return None

def _escalate_risk(current, candidate):
    """Return whichever risk level is higher."""
    ci = RISK_ORDER.index(current) if current in RISK_ORDER else 0
    ca = RISK_ORDER.index(candidate) if candidate in RISK_ORDER else 0
    return RISK_ORDER[max(ci, ca)]

def _classify_filing(filing_text):
    """Classify a filing description into a risk level."""
    text = (filing_text or "").lower()
    if any(k in text for k in ["criminal", "doj", "department of justice", "grand jury", "indictment"]):
        return RISK_CRIMINAL
    if any(k in text for k in ["sec enforcement", "cease and desist", "administrative proceeding", "civil penalty", "sec order"]):
        return RISK_SEC_ENFORCEMENT
    if any(k in text for k in ["sec investigation", "sec inquiry", "wells notice", "formal order of investigation"]):
        return RISK_SEC_INVESTIGATION
    if any(k in text for k in ["class action", "securities fraud lawsuit", "shareholder lawsuit", "putative class"]):
        return RISK_CLASS_ACTION
    if any(k in text for k in ["lawsuit", "litigation", "legal proceedings", "settlement", "complaint filed", "arbitration"]):
        return RISK_MINOR
    return RISK_NONE

def fetch_legal_risk(ticker):
    """
    Query SEC EDGAR for legal/litigation risk for a ticker.
    Returns dict: {risk_level, penalty, findings, scraped_at}
    """
    start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")  # 2 years
    end   = datetime.now().strftime("%Y-%m-%d")
    findings = []
    highest_risk = RISK_NONE

    # ── Search 1: Litigation releases mentioning ticker ──────────────────────
    lit_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=LITRLS&dateRange=custom&startdt={start}&enddt={end}"
    data = _get(lit_url)
    time.sleep(0.15)  # respect rate limit

    if data and data.get("hits", {}).get("hits"):
        for hit in data["hits"]["hits"][:5]:
            src = hit.get("_source", {})
            desc = src.get("file_description", "") or src.get("period_of_report", "")
            entity = src.get("entity_name", ticker)
            date_filed = src.get("file_date", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{src.get('entity_id','')}/{src.get('file_name','')}"
            risk = _classify_filing(f"sec enforcement {desc}")  # lit releases = enforcement by definition
            risk = _escalate_risk(RISK_SEC_ENFORCEMENT, risk)
            highest_risk = _escalate_risk(highest_risk, risk)
            findings.append({
                "type": RISK_LABELS[risk],
                "entity": entity,
                "date": date_filed,
                "description": desc or "SEC Litigation Release",
                "source_url": f"https://www.sec.gov/litigation/litreleases.htm",
                "risk_level": risk,
            })

    # ── Search 2: 8-K filings (material legal events) ────────────────────────
    ek_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22+%22legal+proceedings%22&forms=8-K&dateRange=custom&startdt={start}&enddt={end}"
    data = _get(ek_url)
    time.sleep(0.15)

    if data and data.get("hits", {}).get("hits"):
        for hit in data["hits"]["hits"][:5]:
            src = hit.get("_source", {})
            desc = src.get("file_description", "") or ""
            entity = src.get("entity_name", ticker)
            date_filed = src.get("file_date", "")
            risk = _classify_filing(desc)
            if risk != RISK_NONE:
                highest_risk = _escalate_risk(highest_risk, risk)
                findings.append({
                    "type": RISK_LABELS[risk],
                    "entity": entity,
                    "date": date_filed,
                    "description": desc or "Material Legal Event (8-K)",
                    "source_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K&dateb=&owner=include&count=10",
                    "risk_level": risk,
                })

    # ── Search 3: Full-text search for enforcement keywords ──────────────────
    enforce_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22+%22SEC+investigation%22&dateRange=custom&startdt={start}&enddt={end}"
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
                })

    # De-dupe findings by description
    seen = set()
    unique_findings = []
    for f in findings:
        key = (f["type"], f["date"])
        if key not in seen:
            seen.add(key)
            unique_findings.append(f)

    unique_findings.sort(key=lambda x: x.get("date", ""), reverse=True)

    penalty = RISK_PENALTIES.get(highest_risk, 0)
    # Clean record slight positive
    if highest_risk == RISK_NONE:
        penalty = 2

    return {
        "ticker": ticker,
        "risk_level": highest_risk,
        "risk_label": RISK_LABELS[highest_risk],
        "risk_color": RISK_COLORS[highest_risk],
        "penalty": penalty,
        "findings": unique_findings[:8],  # cap at 8 entries
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
    """Fetch cached legal risk from DB. Returns None if not yet scraped."""
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

    # Watchlist tickers
    try:
        c.execute("SELECT DISTINCT ticker FROM watchlists")
        tickers.update(r[0] for r in c.fetchall())
    except:
        pass

    # Top signal tickers (top 50 by score)
    try:
        c.execute("""
            SELECT ticker FROM signal_scores
            ORDER BY composite_score DESC
            LIMIT 50
        """)
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
            time.sleep(0.5)  # be polite to SEC
        except Exception as e:
            logger.error(f"[LegalRisk] {ticker} failed: {e}")

    logger.info(f"[LegalRisk] Done. {scraped}/{len(tickers)} tickers scraped.")
    return scraped
'''

scraper_path = os.path.join(BASE, "scrapers", "legal_risk_scraper.py")
with open(scraper_path, "w") as f:
    f.write(scraper)
print(f"✅ Created: {scraper_path}")

# ── 2. DB MIGRATION ───────────────────────────────────────────────────────────
migration = '''import sqlite3, sys, os
sys.path.insert(0, os.path.expanduser("~/Documents/trading-system"))
from config.settings import DB_PATH

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.execute("""
    CREATE TABLE IF NOT EXISTS legal_risk (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker       TEXT UNIQUE NOT NULL,
        risk_level   TEXT NOT NULL DEFAULT 'NONE',
        risk_label   TEXT NOT NULL DEFAULT 'None',
        risk_color   TEXT NOT NULL DEFAULT '#22c55e',
        penalty      INTEGER NOT NULL DEFAULT 0,
        findings_json TEXT,
        scraped_at   TEXT
    )
""")
conn.commit()
conn.close()
print("✅ legal_risk table created (or already exists)")
'''

migration_path = os.path.join(BASE, "migrate_legal_risk.py")
with open(migration_path, "w") as f:
    f.write(migration)
print(f"✅ Created: {migration_path}")

# ── 3. SCHEDULER PATCH ────────────────────────────────────────────────────────
scheduler_patch = '''import re, os

main_path = os.path.expanduser("~/Documents/trading-system/main.py")
with open(main_path, "r") as f:
    content = f.read()

# Add import
if "legal_risk_scraper" not in content:
    content = content.replace(
        "from scrapers.screener_scraper import",
        "from scrapers.legal_risk_scraper import scrape_priority_tickers\\nfrom scrapers.screener_scraper import",
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
'''

sched_path = os.path.join(BASE, "patch_scheduler_legal.py")
with open(sched_path, "w") as f:
    f.write(scheduler_patch)
print(f"✅ Created: {sched_path}")

# ── 4. FLASK ROUTE PATCH ──────────────────────────────────────────────────────
route_patch = '''import os

app_path = os.path.expanduser("~/Documents/trading-system/web/app.py")
with open(app_path, "r") as f:
    content = f.read()

if "legal_risk_scraper" not in content:
    content = content.replace(
        "from scrapers.screener_scraper import",
        "from scrapers.legal_risk_scraper import get_legal_risk, fetch_legal_risk, save_legal_risk\\nfrom scrapers.screener_scraper import",
        1
    )
    print("✅ Added legal_risk import to app.py")
else:
    print("⚠️  Import already present")

# Inject legal_risk data into the ticker route
# Find the return render_template for ticker page and inject before it
old_render = "return render_template(\\'ticker.html\\'"
if "legal_risk" not in content:
    inject = """
    # Legal risk - serve from cache, trigger on-demand scrape if missing
    legal_risk_data = get_legal_risk(ticker)
    if legal_risk_data is None:
        try:
            result = fetch_legal_risk(ticker)
            save_legal_risk(ticker, result)
            legal_risk_data = result
        except Exception as e:
            legal_risk_data = {
                "risk_level": "NONE", "risk_label": "Unavailable",
                "risk_color": "#6b7280", "penalty": 0, "findings": [],
                "scraped_at": None,
            }

"""
    # Insert before the render_template call in the ticker route
    content = content.replace(
        "    return render_template(\\'ticker.html\\'",
        inject + "    return render_template(\\'ticker.html\\'",
        1
    )
    # Add legal_risk_data to template kwargs
    content = content.replace(
        "return render_template(\\'ticker.html\\'",
        "return render_template(\\'ticker.html\\', legal_risk=legal_risk_data",
        1
    )
    print("✅ Legal risk injected into ticker route")
else:
    print("⚠️  Legal risk already in ticker route")

with open(app_path, "w") as f:
    f.write(content)
print("✅ app.py patched")
'''

# Note: route patch needs careful handling - output as instructions instead
# since we don't know exact app.py structure
route_patch_path = os.path.join(BASE, "patch_route_legal.py")

# Simpler, safer version
route_patch_safe = '''import os

app_path = os.path.expanduser("~/Documents/trading-system/web/app.py")
with open(app_path, "r") as f:
    content = f.read()

# 1. Add import
if "legal_risk_scraper" not in content:
    old = "from scrapers.screener_scraper import"
    new = "from scrapers.legal_risk_scraper import get_legal_risk, fetch_legal_risk, save_legal_risk\\nfrom scrapers.screener_scraper import"
    content = content.replace(old, new, 1)
    print("✅ Import added")
else:
    print("⚠️  Import already present")

with open(app_path, "w") as f:
    f.write(content)

print("✅ app.py import patched")
print()
print(">>> MANUAL STEP REQUIRED <<<")
print("In your ticker route (def ticker / @app.route('/ticker/<ticker>') etc),")
print("add this block BEFORE the return render_template line:")
print()
print("""    legal_risk_data = get_legal_risk(ticker)
    if legal_risk_data is None:
        try:
            result = fetch_legal_risk(ticker)
            save_legal_risk(ticker, result)
            legal_risk_data = result
        except:
            legal_risk_data = {
                'risk_level': 'NONE', 'risk_label': 'Unavailable',
                'risk_color': '#6b7280', 'penalty': 0, 'findings': [],
                'scraped_at': None,
            }
""")
print("Then add  legal_risk=legal_risk_data  to the render_template kwargs.")
'''

with open(route_patch_path, "w") as f:
    f.write(route_patch_safe)
print(f"✅ Created: {route_patch_path}")

# ── 5. HTML CARD TEMPLATE SNIPPET ─────────────────────────────────────────────
html_card = '''<!-- ═══════════════════════════════════════════════════════
     LEGAL & REGULATORY RISK CARD  (insert in ticker.html)
     ═══════════════════════════════════════════════════════ -->
<div class="widget-card" id="legal-risk-card">
  <div class="widget-header">
    <span class="widget-icon">⚖️</span>
    <h3>Legal &amp; Regulatory Risk</h3>
    <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={{ ticker }}&type=&dateb=&owner=include&count=10"
       target="_blank" class="edgar-link" title="View on SEC EDGAR">
      SEC EDGAR ↗
    </a>
  </div>

  {% set lr = legal_risk %}
  {% if lr %}
    <!-- Risk Badge -->
    <div class="legal-risk-badge" style="background: {{ lr.risk_color }}22; border: 1.5px solid {{ lr.risk_color }}; color: {{ lr.risk_color }};">
      <span class="risk-dot" style="background: {{ lr.risk_color }};"></span>
      {{ lr.risk_label }}
      {% if lr.penalty != 0 %}
        <span class="risk-penalty" title="Impact on composite score">
          {% if lr.penalty > 0 %}+{% endif %}{{ lr.penalty }} pts
        </span>
      {% endif %}
    </div>

    <!-- Score Impact Bar -->
    <div class="legal-score-impact">
      <span class="impact-label">Score Impact</span>
      <div class="impact-bar">
        {% set pct = ((lr.penalty | abs) / 60 * 100) | int %}
        {% if lr.penalty < 0 %}
          <div class="impact-fill negative" style="width: {{ pct }}%;"></div>
        {% elif lr.penalty > 0 %}
          <div class="impact-fill positive" style="width: {{ [pct, 10] | max }}%;"></div>
        {% else %}
          <div class="impact-fill neutral" style="width: 2%;"></div>
        {% endif %}
      </div>
      <span class="impact-value {% if lr.penalty < 0 %}red{% elif lr.penalty > 0 %}green{% endif %}">
        {% if lr.penalty > 0 %}+{% endif %}{{ lr.penalty }}
      </span>
    </div>

    <!-- Findings -->
    {% if lr.findings %}
      <div class="legal-findings">
        <p class="findings-header">Recent Filings ({{ lr.findings | length }})</p>
        {% for f in lr.findings %}
          <div class="finding-row risk-{{ f.risk_level | lower }}">
            <div class="finding-meta">
              <span class="finding-type" style="color: {{ lr.risk_color }};">{{ f.type }}</span>
              <span class="finding-date">{{ f.date }}</span>
            </div>
            <p class="finding-desc">{{ f.description }}</p>
            {% if f.source_url %}
              <a href="{{ f.source_url }}" target="_blank" class="finding-link">View filing ↗</a>
            {% endif %}
          </div>
        {% endfor %}
      </div>
    {% else %}
      <p class="no-findings">No significant legal filings found in the past 2 years.</p>
    {% endif %}

    {% if lr.scraped_at %}
      <p class="legal-updated">Updated: {{ lr.scraped_at[:10] }}</p>
    {% endif %}

  {% else %}
    <p class="no-findings">Legal risk data not yet available for this ticker.</p>
  {% endif %}
</div>

<style>
/* ── Legal Risk Card Styles ── */
#legal-risk-card { position: relative; }

.edgar-link {
  margin-left: auto;
  font-size: 0.72rem;
  color: #60a5fa;
  text-decoration: none;
  opacity: 0.8;
  transition: opacity 0.2s;
}
.edgar-link:hover { opacity: 1; }

.legal-risk-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 0.85rem;
  font-weight: 600;
  margin-bottom: 14px;
}
.risk-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.risk-penalty {
  font-size: 0.75rem;
  opacity: 0.85;
  margin-left: 4px;
}

.legal-score-impact {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  font-size: 0.78rem;
}
.impact-label { color: #9ca3af; white-space: nowrap; }
.impact-bar {
  flex: 1;
  height: 6px;
  background: #374151;
  border-radius: 3px;
  overflow: hidden;
}
.impact-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s ease;
}
.impact-fill.negative { background: #ef4444; }
.impact-fill.positive { background: #22c55e; }
.impact-fill.neutral  { background: #6b7280; }
.impact-value { font-weight: 600; min-width: 32px; text-align: right; }
.impact-value.red   { color: #ef4444; }
.impact-value.green { color: #22c55e; }

.findings-header {
  font-size: 0.75rem;
  color: #9ca3af;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 10px;
  font-weight: 600;
}

.finding-row {
  border-left: 3px solid #374151;
  padding: 8px 12px;
  margin-bottom: 10px;
  background: #1f2937;
  border-radius: 0 6px 6px 0;
}
.finding-row.risk-criminal         { border-left-color: #7c2d12; }
.finding-row.risk-sec_enforcement  { border-left-color: #ef4444; }
.finding-row.risk-sec_investigation{ border-left-color: #f97316; }
.finding-row.risk-class_action     { border-left-color: #f59e0b; }
.finding-row.risk-minor            { border-left-color: #84cc16; }
.finding-row.risk-none             { border-left-color: #22c55e; }

.finding-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.finding-type { font-size: 0.78rem; font-weight: 600; }
.finding-date { font-size: 0.72rem; color: #9ca3af; }
.finding-desc { font-size: 0.8rem; color: #d1d5db; margin-bottom: 4px; line-height: 1.4; }
.finding-link { font-size: 0.72rem; color: #60a5fa; text-decoration: none; }
.finding-link:hover { text-decoration: underline; }

.no-findings {
  font-size: 0.82rem;
  color: #6b7280;
  text-align: center;
  padding: 12px 0;
}
.legal-updated {
  font-size: 0.7rem;
  color: #4b5563;
  text-align: right;
  margin-top: 10px;
}
</style>
'''

html_path = os.path.join(BASE, "legal_risk_card.html")
with open(html_path, "w") as f:
    f.write(html_card)
print(f"✅ Created: {html_path}")

print()
print("=" * 60)
print("ALL FILES CREATED. Now run:")
print()
print("  cd ~/Documents/trading-system")
print("  source venv/bin/activate")
print()
print("  # Step 1: Create DB table")
print("  python migrate_legal_risk.py")
print()
print("  # Step 2: Patch scheduler")
print("  python patch_scheduler_legal.py")
print()
print("  # Step 3: Add import to app.py")
print("  python patch_route_legal.py")
print()
print("  # Step 4: Test scraper on one ticker")
print("  python -c \"")
print("    from scrapers.legal_risk_scraper import fetch_legal_risk")
print("    import json")
print("    r = fetch_legal_risk('AAPL')")
print("    print(json.dumps(r, indent=2))")
print("  \"")
print()
print("  # Step 5: Run full priority scrape")
print("  python -c \"from scrapers.legal_risk_scraper import scrape_priority_tickers; scrape_priority_tickers()\"")
print("=" * 60)
