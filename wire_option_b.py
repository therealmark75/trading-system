with open('scrapers/screener_scraper.py', 'r') as f:
    content = f.read()

new_fn = '''
def scrape_analyst_recom_priority(db_path):
    """Option B: scrape analyst recom for watchlist + top signal tickers."""
    from finvizfinance.quote import finvizfinance
    from database.db import get_connection
    import time

    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM watchlists")
    watchlist = [r[0] for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT ticker FROM top_signals_of_day 
                   WHERE signal_date = DATE('now') LIMIT 20""")
    top_signals = [r[0] for r in cur.fetchall()]
    conn.close()

    tickers = list(set(watchlist + top_signals))
    if not tickers:
        return

    results = {}
    for ticker in tickers:
        try:
            data = finvizfinance(ticker).ticker_fundament()
            recom = data.get('Recom', None)
            if recom and recom != '-':
                results[ticker] = float(recom)
            time.sleep(0.5)
        except Exception:
            pass

    if results:
        conn = get_connection(db_path)
        cur = conn.cursor()
        for ticker, recom in results.items():
            cur.execute("""UPDATE screener_snapshots SET analyst_recom = ?
                           WHERE ticker = ? AND scraped_at = (
                               SELECT MAX(scraped_at) FROM screener_snapshots WHERE ticker = ?)""",
                        (recom, ticker, ticker))
        conn.commit()
        conn.close()
        print(f"  Option B: updated {len(results)} analyst recoms")

'''

# Add before the last function or at end of file
if 'def scrape_analyst_recom_priority' not in content:
    content += new_fn
    with open('scrapers/screener_scraper.py', 'w') as f:
        f.write(content)
    print("Added scrape_analyst_recom_priority to screener_scraper.py")
else:
    print("Function already exists")
