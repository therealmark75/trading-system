# main.py - Phase 1 + Phase 2
import sys, time, logging, argparse
from datetime import datetime
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (DATABASE_PATH, SECTORS, SCREENER_SCRAPE_TIMES,
    INSIDER_SCRAPE_TIMES, INSIDER_CLUSTER_BUY_COUNT, INSIDER_CLUSTER_DAYS,
    LOG_DIR, LOG_LEVEL, REQUEST_DELAY_SECONDS)
from database.db import (initialise_schema, insert_screener_rows,
    insert_insider_trades, insert_insider_signal, insert_signal_scores,
    log_run, get_latest_screener, get_recent_insiders, get_cluster_signals)
from scrapers.screener_scraper import scrape_all_sectors
from scrapers.insider_scraper import scrape_all_insider_types, detect_cluster_signals
from signals.scorer import score_all_tickers
from signals.scanner import run_all_scans

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
        screener_rows   = get_latest_screener(DATABASE_PATH, sector=sector)
        insider_trades  = get_recent_insiders(DATABASE_PATH, days=30)
        cluster_signals = get_cluster_signals(DATABASE_PATH, days=14)

        if not screener_rows:
            logger.warning("No screener data. Run scrape first.")
            return [], {}

        logger.info(f"  Scoring {len(screener_rows)} tickers...")
        signals = score_all_tickers(screener_rows, insider_trades)

        score_rows = [{
            "scored_at": datetime.utcnow().isoformat(),
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
        logger.info(f"  STRONG_BUY: {len(strong_buys)} | BUY: {len(buys)} | REVERSION: {len(reversions)}")

        if strong_buys:
            logger.info("  Top STRONG_BUY:")
            for s in strong_buys[:5]:
                logger.info(f"    {s.ticker} | Score {s.composite_score} | RSI {s.rsi_14} | {' | '.join(s.flags[:2])}")

        for name, items in scan_results.items():
            if items:
                logger.info(f"  Scan [{name}]: {len(items)} hits | Top: {items[0].ticker}")

        duration = time.time() - start
        log_run(DATABASE_PATH, "signal_generation", "SUCCESS", len(signals), duration_s=duration)
        logger.info(f"JOB DONE: Signals | {len(signals)} scored | {duration:.1f}s")
        return signals, scan_results
    except Exception as e:
        logger.error(f"Signals FAILED: {e}", exc_info=True)
        log_run(DATABASE_PATH, "signal_generation", "FAILED", error_msg=str(e), duration_s=time.time()-start)
        return [], {}


def main():
    parser = argparse.ArgumentParser(description="Trading System Phase 1+2")
    parser.add_argument("command", choices=["run-once", "signals", "scheduler", "report"])
    parser.add_argument("--sector-only", metavar="SECTOR")
    args = parser.parse_args()

    initialise_schema(DATABASE_PATH)

    if args.command == "report":
        from dashboard.dashboard import main as dash_main
        dash_main()
        return

    if args.command == "signals":
        job_generate_signals(sector=args.sector_only)
        return

    if args.command == "run-once":
        sectors = [args.sector_only] if args.sector_only else SECTORS
        job_scrape_screener(sectors=sectors)
        job_scrape_insiders()
        job_generate_signals(sector=args.sector_only)
        logger.info("One-shot complete.")
        return

    if args.command == "scheduler":
        scheduler = BlockingScheduler(timezone="Europe/London")
        for t in SCREENER_SCRAPE_TIMES:
            h, m = t.split(":")
            scheduler.add_job(job_scrape_screener, CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"), id=f"screener_{t}")
        for t in INSIDER_SCRAPE_TIMES:
            h, m = t.split(":")
            scheduler.add_job(job_scrape_insiders, CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"), id=f"insider_{t}")
        for t in SCREENER_SCRAPE_TIMES:
            h, m = t.split(":")
            m2 = (int(m)+30) % 60
            h2 = int(h) + (1 if int(m)+30 >= 60 else 0)
            scheduler.add_job(job_generate_signals, CronTrigger(hour=h2, minute=m2, day_of_week="mon-fri"), id=f"signals_{t}")
        logger.info("Scheduler started.")
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Stopped.")

if __name__ == "__main__":
    main()
