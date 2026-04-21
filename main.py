# main.py
# ─────────────────────────────────────────────────
# Main entry point for the trading data system.
# Runs scrapers on schedule and logs everything.
# ─────────────────────────────────────────────────

import sys
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron       import CronTrigger

# ── Project imports ───────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    DATABASE_PATH,
    SECTORS,
    SCREENER_SCRAPE_TIMES,
    INSIDER_SCRAPE_TIMES,
    INSIDER_CLUSTER_BUY_COUNT,
    INSIDER_CLUSTER_DAYS,
    LOG_DIR,
    LOG_LEVEL,
    REQUEST_DELAY_SECONDS,
)
from database.db import (
    initialise_schema,
    insert_screener_rows,
    insert_insider_trades,
    insert_insider_signal,
    log_run,
    get_latest_screener,
    get_recent_insiders,
    get_cluster_signals,
)
from scrapers.screener_scraper import scrape_all_sectors
from scrapers.insider_scraper  import scrape_all_insider_types, detect_cluster_signals


# ── Logging setup ─────────────────────────────────
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level    = getattr(logging, LOG_LEVEL),
    format   = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt  = "%Y-%m-%d %H:%M:%S",
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{LOG_DIR}/trading_system.log"),
    ],
)
logger = logging.getLogger("main")


# ── Job: Screener ─────────────────────────────────

def job_scrape_screener():
    """Scheduled job: scrape all sectors and persist to DB."""
    start = time.time()
    logger.info("=" * 60)
    logger.info("JOB START: Screener scrape")

    try:
        sector_data = scrape_all_sectors(SECTORS, delay=REQUEST_DELAY_SECONDS)
        total_rows  = 0

        for sector, rows in sector_data.items():
            if rows:
                inserted = insert_screener_rows(DATABASE_PATH, rows)
                total_rows += inserted
                logger.info(f"  Stored {inserted} rows for {sector}")
            else:
                logger.warning(f"  No data for {sector}")

        duration = time.time() - start
        log_run(DATABASE_PATH, "screener_scrape", "SUCCESS", total_rows, duration_s=duration)
        logger.info(f"JOB DONE: Screener | {total_rows} rows | {duration:.1f}s")

    except Exception as e:
        duration = time.time() - start
        logger.error(f"Screener job FAILED: {e}", exc_info=True)
        log_run(DATABASE_PATH, "screener_scrape", "FAILED", error_msg=str(e), duration_s=duration)


# ── Job: Insider Trades ───────────────────────────

def job_scrape_insiders():
    """Scheduled job: scrape insider trades and detect signals."""
    start = time.time()
    logger.info("=" * 60)
    logger.info("JOB START: Insider trade scrape")

    try:
        trades   = scrape_all_insider_types(delay=REQUEST_DELAY_SECONDS)
        inserted = insert_insider_trades(DATABASE_PATH, trades)
        logger.info(f"  Stored {inserted} new insider trade rows")

        # Detect cluster buy signals from fresh data
        buy_signals  = detect_cluster_signals(
            trades,
            window_days  = INSIDER_CLUSTER_DAYS,
            min_insiders = INSIDER_CLUSTER_BUY_COUNT,
            signal_type  = "Buy",
        )
        sell_signals = detect_cluster_signals(
            trades,
            window_days  = INSIDER_CLUSTER_DAYS,
            min_insiders = INSIDER_CLUSTER_BUY_COUNT,
            signal_type  = "Sale",
        )

        for sig in buy_signals + sell_signals:
            insert_insider_signal(DATABASE_PATH, sig)

        duration = time.time() - start
        log_run(DATABASE_PATH, "insider_scrape", "SUCCESS", inserted, duration_s=duration)
        logger.info(
            f"JOB DONE: Insider | {inserted} new rows | "
            f"{len(buy_signals)} buy signals | {len(sell_signals)} sell signals | {duration:.1f}s"
        )

    except Exception as e:
        duration = time.time() - start
        logger.error(f"Insider job FAILED: {e}", exc_info=True)
        log_run(DATABASE_PATH, "insider_scrape", "FAILED", error_msg=str(e), duration_s=duration)


# ── CLI report ────────────────────────────────────

def print_report():
    """Print a quick terminal summary of what's in the DB."""
    from rich.console import Console
    from rich.table   import Table

    console = Console()

    # ── Insider cluster signals ───────────────────
    signals = get_cluster_signals(DATABASE_PATH, days=14)
    if signals:
        tbl = Table(title="Insider Cluster Signals (last 14 days)", show_lines=True)
        tbl.add_column("Ticker", style="bold cyan")
        tbl.add_column("Type",   style="bold")
        tbl.add_column("Insiders")
        tbl.add_column("Total Value ($)")
        tbl.add_column("Detected At")

        for s in signals:
            colour = "green" if "BUY" in s["signal_type"] else "red"
            tbl.add_row(
                s["ticker"],
                f"[{colour}]{s['signal_type']}[/{colour}]",
                str(s["insider_count"]),
                f"{s['total_value']:,.0f}" if s["total_value"] else "-",
                s["detected_at"][:16],
            )
        console.print(tbl)
    else:
        console.print("[yellow]No cluster signals in last 14 days.[/yellow]")

    # ── Latest screener snapshot (top 20 by RSI) ──
    rows = get_latest_screener(DATABASE_PATH)
    if rows:
        console.print(f"\n[bold]Screener snapshot:[/bold] {len(rows)} tickers total")
        top = sorted(
            [r for r in rows if r.get("rsi_14")],
            key=lambda r: r["rsi_14"],
            reverse=True,
        )[:20]

        tbl2 = Table(title="Top 20 by RSI (14)", show_lines=True)
        for col in ["ticker", "sector", "price", "change_pct", "rsi_14",
                    "sma_50_pct", "volume"]:
            tbl2.add_column(col)

        for r in top:
            tbl2.add_row(
                r.get("ticker", ""),
                r.get("sector", ""),
                str(r.get("price", "")),
                f"{r.get('change_pct', 0):.2f}%" if r.get("change_pct") is not None else "-",
                str(r.get("rsi_14", "")),
                str(r.get("sma_50_pct", "")),
                str(r.get("volume", "")),
            )
        console.print(tbl2)


# ── Entry point ───────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trading System Phase 1")
    parser.add_argument(
        "command",
        choices=["run-once", "scheduler", "report"],
        help=(
            "run-once: scrape everything once and exit | "
            "scheduler: run on schedule indefinitely | "
            "report: print DB summary to terminal"
        ),
    )
    parser.add_argument(
        "--sector-only",
        metavar="SECTOR",
        help="Limit screener scrape to one sector (e.g. 'Technology')",
    )
    args = parser.parse_args()

    # Always ensure schema exists
    initialise_schema(DATABASE_PATH)

    if args.command == "report":
        print_report()
        return

    if args.command == "run-once":
        logger.info("Running full scrape (one-shot mode)")
        sectors = [args.sector_only] if args.sector_only else SECTORS
        # Screener
        sector_data = scrape_all_sectors(sectors, delay=REQUEST_DELAY_SECONDS)
        for sector, rows in sector_data.items():
            if rows:
                insert_screener_rows(DATABASE_PATH, rows)
        # Insider
        job_scrape_insiders()
        logger.info("One-shot scrape complete.")
        return

    if args.command == "scheduler":
        logger.info("Starting scheduled mode...")
        scheduler = BlockingScheduler(timezone="Europe/London")

        # Parse screener times like "08:00" → hour/minute
        for t in SCREENER_SCRAPE_TIMES:
            h, m = t.split(":")
            scheduler.add_job(
                job_scrape_screener,
                CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                id=f"screener_{t}",
                name=f"Screener scrape at {t}",
            )

        for t in INSIDER_SCRAPE_TIMES:
            h, m = t.split(":")
            scheduler.add_job(
                job_scrape_insiders,
                CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                id=f"insider_{t}",
                name=f"Insider scrape at {t}",
            )

        logger.info("Scheduled jobs:")
        for job in scheduler.get_jobs():
            logger.info(f"  {job.name}")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
