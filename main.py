# main.py - Phase 1 + Phase 2
import sys, time, logging, argparse
from datetime import datetime, timezone
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (DATABASE_PATH, SECTORS, SCREENER_SCRAPE_TIMES, NEWS_SCRAPE_TIMES,
    INSIDER_SCRAPE_TIMES, INSIDER_CLUSTER_BUY_COUNT, INSIDER_CLUSTER_DAYS,
    LOG_DIR, LOG_LEVEL, REQUEST_DELAY_SECONDS)
from database.db import (get_connection, initialise_schema, insert_screener_rows, generate_top_signals_of_day, prune_old_snapshots,
    insert_insider_trades, insert_insider_signal, insert_signal_scores, detect_rating_changes, update_analyst_recom,
    insert_news_articles, insert_ticker_sentiment, insert_calendar_events,
    log_run, get_latest_screener, get_recent_insiders, get_cluster_signals,
    get_top_signals, get_ticker_sentiment)
from scrapers.quote_scraper import scrape_recom_for_tickers
from scrapers.screener_scraper import scrape_all_sectors
from scrapers.insider_scraper import scrape_all_insider_types, detect_cluster_signals
from signals.scorer import score_all_tickers
from signals.scanner import run_all_scans
from scrapers.news_scraper import scrape_news_for_tickers, compute_ticker_sentiment
from scrapers.calendar_scraper import scrape_economic_calendar, get_earnings_calendar

Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(f"{LOG_DIR}/trading_system.log")])
logger = logging.getLogger("main")


def job_scrape_screener(sectors=None):
    start = time.time()
    logger.info("=" * 60)
    logger.info("JOB START: Screener scrape")
    sectors = sectors or SECTORS
    try:
        sector_data = scrape_all_sectors(sectors, delay=REQUEST_DELAY_SECONDS)
        total = 0
        for sector, rows in sector_data.items():
            if rows:
                total += insert_screener_rows(DATABASE_PATH, rows)
                logger.info(f"  Stored {len(rows)} rows for {sector}")
        duration = time.time() - start
        log_run(DATABASE_PATH, "screener_scrape", "SUCCESS", total, duration_s=duration)
        prune_old_snapshots(DATABASE_PATH)
        logger.info(f"JOB DONE: Screener | {total} rows | {duration:.1f}s")
    except Exception as e:
        logger.error(f"Screener FAILED: {e}", exc_info=True)
        log_run(DATABASE_PATH, "screener_scrape", "FAILED", error_msg=str(e), duration_s=time.time()-start)


def job_scrape_insiders():
    start = time.time()
    logger.info("=" * 60)
    logger.info("JOB START: Insider trade scrape")
    try:
        trades = scrape_all_insider_types(delay=REQUEST_DELAY_SECONDS)
        inserted = insert_insider_trades(DATABASE_PATH, trades)
        logger.info(f"  Stored {inserted} new insider trade rows")
        buy_sigs  = detect_cluster_signals(trades, window_days=INSIDER_CLUSTER_DAYS, min_insiders=INSIDER_CLUSTER_BUY_COUNT, signal_type="Buy")
        sell_sigs = detect_cluster_signals(trades, window_days=INSIDER_CLUSTER_DAYS, min_insiders=INSIDER_CLUSTER_BUY_COUNT, signal_type="Sale")
        for sig in buy_sigs + sell_sigs:
            insert_insider_signal(DATABASE_PATH, sig)
        duration = time.time() - start
        log_run(DATABASE_PATH, "insider_scrape", "SUCCESS", inserted, duration_s=duration)
        logger.info(f"JOB DONE: Insider | {inserted} rows | {len(buy_sigs)} buy | {len(sell_sigs)} sell | {duration:.1f}s")
        return trades, buy_sigs + sell_sigs
    except Exception as e:
        logger.error(f"Insider FAILED: {e}", exc_info=True)
        log_run(DATABASE_PATH, "insider_scrape", "FAILED", error_msg=str(e), duration_s=time.time()-start)
        return [], []



def job_generate_signals(sector=None):
    start = time.time()
    logger.info("=" * 60)
    logger.info("JOB START: Signal generation")
    try:
        screener_rows   = get_latest_screener(DATABASE_PATH, sector=None)
        insider_trades  = get_recent_insiders(DATABASE_PATH, days=30)
        cluster_signals = get_cluster_signals(DATABASE_PATH, days=14)
        if not screener_rows:
            logger.warning("No screener data. Run scrape first.")
            return [], {}
        logger.info(f"  Scoring {len(screener_rows)} tickers...")
        signals = score_all_tickers(screener_rows, insider_trades)
        batch_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        score_rows = [{
            "scored_at": batch_ts, "ticker": s.ticker,
            "composite_score": s.composite_score, "momentum_score": s.momentum_score,
            "quality_score": s.quality_score, "insider_score": s.insider_score,
            "reversion_score": s.reversion_score, "rating": s.rating,
            "flags": "|".join(s.flags),
        } for s in signals]
        insert_signal_scores(DATABASE_PATH, score_rows)
        scan_results = run_all_scans(screener_rows, insider_trades, cluster_signals)
        strong_buys = [s for s in signals if s.rating == "STRONG_BUY"]
        buys        = [s for s in signals if s.rating == "BUY"]
        reversions  = [s for s in signals if s.rating == "REVERSION"]
        logger.info(f"  STRONG_BUY: {len(strong_buys)} | BUY: {len(buys)} | HOLD: {len(reversions)}")
        for name, items in scan_results.items():
            if items: logger.info(f"  Scan [{name}]: {len(items)} hits | Top: {items[0].ticker}")
        duration = time.time() - start
        generate_top_signals_of_day(DATABASE_PATH)
        logger.info("Top signals of day generated")
        
        # Detect and log any rating changes
        detect_rating_changes(DATABASE_PATH)
        from scrapers.screener_scraper import scrape_analyst_recom_priority
        scrape_analyst_recom_priority(DATABASE_PATH)
        logger.info("Rating changes detected and logged")
        log_run(DATABASE_PATH, "signal_generation", "SUCCESS", len(signals), duration_s=duration)
        logger.info(f"JOB DONE: Signals | {len(signals)} scored | {duration:.1f}s")
        return signals, scan_results
    except Exception as e:
        logger.error(f"Signals FAILED: {e}", exc_info=True)
        log_run(DATABASE_PATH, "signal_generation", "FAILED", error_msg=str(e), duration_s=time.time()-start)
        return [], {}



def job_recom_bulk():
    """
    Nightly bulk enrichment of analyst_recom for ALL tickers in screener_snapshots.
    Skips tickers already scraped in the last 20 hours (priority job covers those).
    Runs at 02:00 on weeknights.
    """
    start = time.time()
    logger.info("JOB START: Analyst Recom (bulk nightly)")
    try:
        conn = get_connection(DATABASE_PATH)
        cur = conn.cursor()

        # Get all tickers that DON'T have a fresh recom value
        # (not updated in last 20 hours - priority job handles those)
        cur.execute("""
            SELECT DISTINCT ticker FROM screener_snapshots
            WHERE scraped_at >= datetime('now', '-2 days')
            AND (
                analyst_recom IS NULL
                OR ticker NOT IN (
                    SELECT DISTINCT ticker FROM screener_snapshots
                    WHERE analyst_recom IS NOT NULL
                    AND scraped_at >= datetime('now', '-20 hours')
                )
            )
            ORDER BY ticker
        """)
        tickers = [r[0] for r in cur.fetchall()]
        conn.close()

        if not tickers:
            logger.info("JOB DONE: Bulk Recom | all tickers already have fresh data")
            return

        logger.info(f"  Bulk recom targets: {len(tickers)} tickers")

        from scrapers.quote_scraper import scrape_recom_bulk
        recom_map = scrape_recom_bulk(tickers, delay=0.5, threads=2)
        updated = update_analyst_recom(DATABASE_PATH, recom_map)

        duration = time.time() - start
        logger.info(f"JOB DONE: Bulk Recom | {updated} rows updated | {duration/60:.1f} mins")

    except Exception as e:
        logger.error(f"Bulk recom job FAILED: {e}", exc_info=True)

def job_recom_priority():
    """
    Scrape analyst recom from individual FinViz ticker pages
    for high-priority tickers: watchlist + today's top signals.
    Runs after main screener job.
    """
    start = time.time()
    logger.info("JOB START: Analyst Recom (priority tickers)")
    try:
        conn = get_connection(DATABASE_PATH)
        cur = conn.cursor()

        # Get all watchlist tickers (across all users)
        cur.execute("SELECT DISTINCT ticker FROM watchlists")
        watchlist_tickers = [r[0] for r in cur.fetchall()]

        # Get today's top signal tickers
        cur.execute("""
            SELECT DISTINCT ticker FROM top_signals_of_day
            WHERE signal_date = DATE('now')
        """)
        top_tickers = [r[0] for r in cur.fetchall()]

        conn.close()

        # Deduplicate, preserve order (watchlist first)
        seen = set()
        priority_tickers = []
        for t in watchlist_tickers + top_tickers:
            if t not in seen:
                seen.add(t)
                priority_tickers.append(t)

        if not priority_tickers:
            logger.info("JOB DONE: Recom | no priority tickers found")
            return

        logger.info(f"  Priority tickers: {len(priority_tickers)} ({len(watchlist_tickers)} watchlist + {len(top_tickers)} top signals)")

        recom_map = scrape_recom_for_tickers(priority_tickers, delay=1.5)
        updated = update_analyst_recom(DATABASE_PATH, recom_map)

        duration = time.time() - start
        logger.info(f"JOB DONE: Recom | {updated} rows updated | {duration:.1f}s")

    except Exception as e:
        logger.error(f"Recom job FAILED: {e}", exc_info=True)

def job_news_and_calendar(top_n: int = 30):
    """Scrape news sentiment for top BUY signals + economic calendar."""
    start = time.time()
    logger.info("=" * 60)
    logger.info("JOB START: News & Calendar")
    try:
        # Get top signals to focus news scraping
        top_signals = get_top_signals(DATABASE_PATH, limit=top_n)
        tickers = [s["ticker"] for s in top_signals if s.get("rating") in ("BUY","STRONG_BUY","REVERSION")]

        if tickers:
            logger.info(f"  Scraping news for {len(tickers)} top-rated tickers...")
            news_data = scrape_news_for_tickers(tickers[:20], delay=2.0)  # cap at 20 to be polite

            all_articles = []
            sentiment_rows = []
            for ticker, data in news_data.items():
                all_articles.extend(data.get("articles", []))
                sentiment_rows.append({
                    "ticker":        ticker,
                    "avg_sentiment": data["avg_sentiment"],
                    "bullish_count": data["bullish_count"],
                    "bearish_count": data["bearish_count"],
                    "neutral_count": data["neutral_count"],
                    "article_count": data["article_count"],
                })

            insert_news_articles(DATABASE_PATH, all_articles)
            insert_ticker_sentiment(DATABASE_PATH, sentiment_rows)
            logger.info(f"  Stored {len(all_articles)} articles, {len(sentiment_rows)} sentiment scores")

            # Log top bullish/bearish
            ranked = sorted(sentiment_rows, key=lambda x: x["avg_sentiment"], reverse=True)
            if ranked:
                logger.info(f"  Most bullish news: {ranked[0]['ticker']} ({ranked[0]['avg_sentiment']:+.3f})")
                logger.info(f"  Most bearish news: {ranked[-1]['ticker']} ({ranked[-1]['avg_sentiment']:+.3f})")

        # Economic calendar
        logger.info("  Scraping economic calendar...")
        events = scrape_economic_calendar(days_ahead=7)
        if events:
            insert_calendar_events(DATABASE_PATH, events)
            high_impact = [e for e in events if e.get("impact") == "HIGH"]
            logger.info(f"  {len(events)} events, {len(high_impact)} high-impact")

        duration = time.time() - start
        log_run(DATABASE_PATH, "news_calendar", "SUCCESS", len(tickers), duration_s=duration)
        logger.info(f"JOB DONE: News & Calendar | {duration:.1f}s")

    except Exception as e:
        logger.error(f"News/Calendar FAILED: {e}", exc_info=True)
        log_run(DATABASE_PATH, "news_calendar", "FAILED", error_msg=str(e), duration_s=time.time()-start)



    start = time.time()
    logger.info("=" * 60)
    logger.info("JOB START: Signal generation")
    try:
        screener_rows   = get_latest_screener(DATABASE_PATH, sector=None)
        insider_trades  = get_recent_insiders(DATABASE_PATH, days=30)
        cluster_signals = get_cluster_signals(DATABASE_PATH, days=14)

        if not screener_rows:
            logger.warning("No screener data. Run scrape first.")
            return [], {}

        logger.info(f"  Scoring {len(screener_rows)} tickers...")
        signals = score_all_tickers(screener_rows, insider_trades)

        # Single shared timestamp for entire batch so dashboard queries work correctly
        batch_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        score_rows = [{
            "scored_at": batch_ts,
            "ticker": s.ticker,
            "composite_score": s.composite_score,
            "momentum_score": s.momentum_score,
            "quality_score": s.quality_score,
            "insider_score": s.insider_score,
            "reversion_score": s.reversion_score,
            "rating": s.rating,
            "flags": "|".join(s.flags),
        } for s in signals]
        insert_signal_scores(DATABASE_PATH, score_rows)

        scan_results = run_all_scans(screener_rows, insider_trades, cluster_signals)

        strong_buys = [s for s in signals if s.rating == "STRONG_BUY"]
        buys        = [s for s in signals if s.rating == "BUY"]
        reversions  = [s for s in signals if s.rating == "REVERSION"]
        logger.info(f"  STRONG_BUY: {len(strong_buys)} | BUY: {len(buys)} | HOLD: {len(reversions)}")

        if strong_buys:
            logger.info("  Top STRONG_BUY:")
            for s in strong_buys[:5]:
                logger.info(f"    {s.ticker} | Score {s.composite_score} | RSI {s.rsi_14} | {' | '.join(s.flags[:2])}")

        for name, items in scan_results.items():
            if items:
                logger.info(f"  Scan [{name}]: {len(items)} hits | Top: {items[0].ticker}")

        duration = time.time() - start
        generate_top_signals_of_day(DATABASE_PATH)
        logger.info("Top signals of day generated")
        
        # Detect and log any rating changes
        detect_rating_changes(DATABASE_PATH)
        logger.info("Rating changes detected and logged")
        log_run(DATABASE_PATH, "signal_generation", "SUCCESS", len(signals), duration_s=duration)
        logger.info(f"JOB DONE: Signals | {len(signals)} scored | {duration:.1f}s")
        return signals, scan_results
    except Exception as e:
        logger.error(f"Signals FAILED: {e}", exc_info=True)
        log_run(DATABASE_PATH, "signal_generation", "FAILED", error_msg=str(e), duration_s=time.time()-start)
        return [], {}


def main():
    parser = argparse.ArgumentParser(description="Trading System Phase 1+2")
    parser.add_argument("command", choices=["run-once", "signals", "news", "scheduler", "report"])
    parser.add_argument("--sector-only", metavar="SECTOR")
    parser.add_argument("--skip-news", action="store_true", help="Skip news scraping in run-once")
    args = parser.parse_args()

    initialise_schema(DATABASE_PATH)

    if args.command == "report":
        from dashboard.dashboard import main as dash_main
        dash_main()
        return

    if args.command == "signals":
        job_generate_signals(sector=args.sector_only)
        return

    if args.command == "news":
        job_news_and_calendar()
        return

    if args.command == "run-once":
        sectors = [args.sector_only] if args.sector_only else SECTORS
        job_scrape_screener(sectors=sectors)
        job_scrape_insiders()
        job_generate_signals(sector=args.sector_only)
        if not args.skip_news:
            job_news_and_calendar()
        logger.info("One-shot complete.")
        return

    if args.command == "scheduler":
        from apscheduler.executors.pool import ThreadPoolExecutor
        from apscheduler.jobstores.memory import MemoryJobStore

        jobstores  = {"default": MemoryJobStore()}
        executors  = {"default": ThreadPoolExecutor(3)}
        job_defaults = {
            "misfire_grace_time": 3600,   # allow up to 1 hour late
            "coalesce":           True,    # merge missed runs into one
            "max_instances":      1,       # never run same job twice simultaneously
        }

        scheduler = BlockingScheduler(
            timezone     = "Europe/London",
            jobstores    = jobstores,
            executors    = executors,
            job_defaults = job_defaults,
        )

        # ── Scheduled jobs ────────────────────────────
        for t in SCREENER_SCRAPE_TIMES:
            h, m = t.split(":")
            scheduler.add_job(
                job_scrape_screener,
                CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                id=f"screener_{t}", name=f"Screener {t}",
            )

        for t in INSIDER_SCRAPE_TIMES:
            h, m = t.split(":")
            scheduler.add_job(
                job_scrape_insiders,
                CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                id=f"insider_{t}", name=f"Insider {t}",
            )

        for t in SCREENER_SCRAPE_TIMES:
            h, m = t.split(":")
            m2   = (int(m) + 30) % 60
            h2   = int(h) + (1 if int(m) + 30 >= 60 else 0)
            scheduler.add_job(
                job_generate_signals,
                CronTrigger(hour=h2, minute=m2, day_of_week="mon-fri"),
                id=f"signals_{t}", name=f"Signals {h2:02d}:{m2:02d}",
            )

        for t in SCREENER_SCRAPE_TIMES:
            h, m = t.split(":")
            m2 = (int(m) + 35) % 60
            h2 = int(h) + (1 if int(m) + 35 >= 60 else 0)
            scheduler.add_job(
                job_recom_priority,
                CronTrigger(hour=h2, minute=m2, day_of_week="mon-fri"),
                id=f"recom_{t}", name=f"Analyst Recom {h2:02d}:{m2:02d}",
            )

        # Nightly bulk recom job - 02:00 Mon-Fri
        scheduler.add_job(
            job_recom_bulk,
            CronTrigger(hour=2, minute=0, day_of_week="mon-fri"),
            id="recom_bulk_nightly", name="Analyst Recom Bulk 02:00",
        )

        for t in NEWS_SCRAPE_TIMES:
            h, m = t.split(":")
            scheduler.add_job(
                job_news_and_calendar,
                CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                id=f"news_{t}", name=f"News & Calendar {t}",
            )

        # ── Startup job: run signals immediately on launch ──
        from apscheduler.triggers.date import DateTrigger
        from datetime import timedelta
        startup_time = datetime.now() + timedelta(seconds=5)
        scheduler.add_job(
            job_generate_signals,
            DateTrigger(run_date=startup_time),
            id="startup_signals", name="Startup signal generation",
        )

        logger.info("Scheduled jobs:")
        for job in scheduler.get_jobs():
            logger.info(f"  {job.name}")
        logger.info("Scheduler running. Press Ctrl+C to stop.")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")

if __name__ == "__main__":
    main()
