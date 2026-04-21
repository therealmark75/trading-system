import time, random, logging
from datetime import datetime, timedelta
from collections import defaultdict
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
BASE_URL = "https://finviz.com/insidertrading.ashx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://finviz.com/",
}
TYPE_MAP = {"Buy":"Buy","Sale":"Sale","Sale+OE":"Sale","Option Exercise":"Option Exercise","OE":"Option Exercise","Gift":"Gift"}

def _to_int(val):
    try: return int(str(val).replace(",","").strip())
    except: return None

def _to_float(val):
    try: return float(str(val).replace(",","").replace("$","").strip())
    except: return None

def _parse_date(val):
    if not val or val=="-": return None
    for fmt in ("%b %d %y","%b %d, %Y","%Y-%m-%d","%m/%d/%Y","%b %d '%y"):
        try: return datetime.strptime(str(val).strip(),fmt).strftime("%Y-%m-%d")
        except: continue
    return str(val).strip()

def _parse_insider_table(soup):
    rows=[]
    table=soup.find("table",{"class":"styled-table-new"})
    if not table:
        for t in soup.find_all("table"):
            hdrs=[td.get_text(strip=True) for td in t.find_all("td",limit=10)]
            if "Ticker" in hdrs or "Owner" in hdrs:
                table=t; break
    if not table: return []
    trows=table.find_all("tr")
    if len(trows)<2: return []
    headers=[td.get_text(strip=True) for td in trows[0].find_all("td")]
    if not headers: headers=[th.get_text(strip=True) for th in trows[0].find_all("th")]
    for tr in trows[1:]:
        cells=tr.find_all("td")
        if len(cells)<8: continue
        rd=dict(zip(headers,[td.get_text(strip=True) for td in cells]))
        ticker=None
        for td in cells:
            a=td.find("a")
            if a and "quote.ashx" in a.get("href",""):
                ticker=a.get_text(strip=True); break
        if not ticker: ticker=rd.get("Ticker","").strip()
        if not ticker: continue
        tx_type=TYPE_MAP.get(rd.get("Transaction",rd.get("Type","")).strip(), rd.get("Transaction",""))
        rows.append({"ticker":ticker,"company":rd.get("Company",rd.get("Owner","")),"insider_name":rd.get("Owner",rd.get("Insider","")),"insider_title":rd.get("Relationship",rd.get("Title","")),"transaction_date":_parse_date(rd.get("Date","")),"transaction_type":tx_type,"shares":_to_int(rd.get("Shares","")),"price":_to_float(rd.get("Cost",rd.get("Price",""))),"value":_to_float(rd.get("Value #",rd.get("Value",""))),"shares_total":_to_int(rd.get("Shares Total","")),"sec_form":rd.get("SEC Form 4","")})
    return rows

def scrape_insider_trades(transaction_type="ALL", pages=3):
    logger.info(f"Scraping insider trades (type={transaction_type}, pages={pages})")
    type_param={"ALL":"","Buy":"0","Sale":"1","Option Exercise":"2"}.get(transaction_type,"")
    all_rows=[]
    for page in range(pages):
        params={"or":page*20}
        if type_param!="": params["tc"]=type_param
        try:
            resp=requests.get(BASE_URL,headers=HEADERS,params=params,timeout=20)
            resp.raise_for_status()
            page_rows=_parse_insider_table(BeautifulSoup(resp.text,"lxml"))
            if not page_rows: break
            all_rows.extend(page_rows)
            logger.info(f"  Page {page+1}: {len(page_rows)} rows")
            time.sleep(2.5+random.uniform(0,1))
        except Exception as e:
            logger.error(f"Error page {page+1}: {e}"); break
    logger.info(f"Total insider rows: {len(all_rows)}")
    return all_rows

def scrape_all_insider_types(delay=2.5):
    all_rows=[]; seen=set()
    for tx_type in ("Buy","Sale","Option Exercise"):
        for row in scrape_insider_trades(transaction_type=tx_type,pages=5):
            key=(row.get("ticker"),row.get("insider_name"),row.get("transaction_date"),row.get("transaction_type"),row.get("shares"))
            if key not in seen:
                seen.add(key); all_rows.append(row)
        time.sleep(delay+random.uniform(0,1))
    logger.info(f"Total unique insider trades: {len(all_rows)}")
    return all_rows

def detect_cluster_signals(trades, window_days=10, min_insiders=3, signal_type="Buy"):
    relevant=[t for t in trades if t.get("transaction_type")==signal_type and t.get("transaction_date")]
    by_ticker=defaultdict(list)
    for trade in relevant: by_ticker[trade["ticker"]].append(trade)
    cutoff=datetime.utcnow()-timedelta(days=window_days)
    signals=[]
    for ticker,tt in by_ticker.items():
        recent=[t for t in tt if datetime.strptime(t["transaction_date"],"%Y-%m-%d")>=cutoff]
        unique={t["insider_name"] for t in recent if t.get("insider_name")}
        if len(unique)>=min_insiders:
            total=sum(t.get("value") or 0 for t in recent)
            signals.append({"detected_at":datetime.utcnow().isoformat(),"ticker":ticker,"signal_type":f"CLUSTER_{signal_type.upper()}","insider_count":len(unique),"total_value":total,"window_days":window_days,"notes":f"Insiders: {', '.join(sorted(unique))}"})
            logger.info(f"CLUSTER SIGNAL: {ticker} | {len(unique)} {signal_type}s | ${total:,.0f}")
    return signals
