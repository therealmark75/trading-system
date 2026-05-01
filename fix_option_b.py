from finvizfinance.quote import finvizfinance
from database.db import get_connection
from config.settings import DATABASE_PATH
import time

def scrape_analyst_recom_option_b(tickers):
    """Scrape analyst recom from individual FinViz ticker pages."""
    results = {}
    for ticker in tickers:
        try:
            stock = finvizfinance(ticker)
            data = stock.ticker_fundament()
            recom = data.get('Recom', None)
            if recom:
                results[ticker] = float(recom)
                print(f"  {ticker}: {recom}")
            else:
                print(f"  {ticker}: no recom found")
            time.sleep(0.5)  # be polite to FinViz
        except Exception as e:
            print(f"  {ticker}: ERROR - {e}")
    return results

def get_priority_tickers():
    """Get watchlist + today's top signals."""
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    # Watchlist tickers
    cur.execute('SELECT DISTINCT ticker FROM watchlists')
    watchlist = [r[0] for r in cur.fetchall()]
    # Top signals tickers
    cur.execute('''SELECT DISTINCT ticker FROM top_signals_of_day 
                   WHERE signal_date = DATE('now') LIMIT 20''')
    top_signals = [r[0] for r in cur.fetchall()]
    conn.close()
    combined = list(set(watchlist + top_signals))
    print(f"Priority tickers: {combined}")
    return combined

def update_analyst_recom(results):
    """Write scraped recom values back to screener_snapshots."""
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    for ticker, recom in results.items():
        cur.execute('''UPDATE screener_snapshots SET analyst_recom = ?
                       WHERE ticker = ? AND scraped_at = (
                           SELECT MAX(scraped_at) FROM screener_snapshots WHERE ticker = ?)''',
                    (recom, ticker, ticker))
        print(f"  Updated {ticker} -> {recom}")
    conn.commit()
    conn.close()

if __name__ == '__main__':
    tickers = get_priority_tickers()
    print(f"\nScraping {len(tickers)} tickers...")
    results = scrape_analyst_recom_option_b(tickers)
    print(f"\nUpdating DB with {len(results)} results...")
    update_analyst_recom(results)
    print("\nDone.")
