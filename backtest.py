# backtest.py
# ─────────────────────────────────────────────────
# Standalone backtest runner.
# Usage:
#   python backtest.py                  # run full backtest
#   python backtest.py --rating BUY     # specific rating
#   python backtest.py --days 5 10 20   # specific hold periods
#   python backtest.py --show           # show last results
# ─────────────────────────────────────────────────

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import DATABASE_PATH, LOG_DIR, LOG_LEVEL
from signals.backtester import run_full_backtest, save_backtest_results

Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{LOG_DIR}/backtest.log"),
    ],
)
logger = logging.getLogger("backtest")


def show_results():
    """Display last backtest results from DB."""
    import sqlite3
    from rich.console import Console
    from rich.table   import Table
    from rich         import box

    console = Console()

    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()

        cur.execute("""
            SELECT * FROM backtest_results
            WHERE run_at = (SELECT MAX(run_at) FROM backtest_results)
            ORDER BY rating, hold_days
        """)
        rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            console.print("[yellow]No backtest results yet. Run: python backtest.py[/yellow]")
            conn.close()
            return

        tbl = Table(title="Backtest Results", box=box.ROUNDED, show_lines=True)
        tbl.add_column("Rating",    style="bold")
        tbl.add_column("Hold",      justify="right")
        tbl.add_column("Trades",    justify="right")
        tbl.add_column("Win Rate",  justify="right")
        tbl.add_column("Avg Return",justify="right")
        tbl.add_column("Median",    justify="right")
        tbl.add_column("Best",      justify="right")
        tbl.add_column("Worst",     justify="right")
        tbl.add_column("Prof.Factor",justify="right")
        tbl.add_column("Sharpe",    justify="right")

        for r in rows:
            wr_colour = "green" if r["win_rate"] >= 55 else ("yellow" if r["win_rate"] >= 45 else "red")
            ar_colour = "green" if r["avg_return"] > 0 else "red"
            tbl.add_row(
                r["rating"],
                f"{r['hold_days']}d",
                str(r["total_trades"]),
                f"[{wr_colour}]{r['win_rate']:.1f}%[/{wr_colour}]",
                f"[{ar_colour}]{r['avg_return']:+.2f}%[/{ar_colour}]",
                f"{r['median_return']:+.2f}%",
                f"[green]{r['best_trade']:+.2f}%[/green]",
                f"[red]{r['worst_trade']:+.2f}%[/red]",
                str(r["profit_factor"]),
                str(r["sharpe_approx"]),
            )
        console.print(tbl)

        # Top performing trades
        cur.execute("""
            SELECT * FROM backtest_trades
            WHERE run_at = (SELECT MAX(run_at) FROM backtest_results)
              AND return_pct IS NOT NULL
            ORDER BY return_pct DESC
            LIMIT 10
        """)
        top_trades = [dict(r) for r in cur.fetchall()]
        conn.close()

        if top_trades:
            tbl2 = Table(title="Top 10 Trades", box=box.ROUNDED, show_lines=True)
            tbl2.add_column("Ticker",  style="bold cyan")
            tbl2.add_column("Rating")
            tbl2.add_column("Signal Date")
            tbl2.add_column("Score",   justify="right")
            tbl2.add_column("Entry",   justify="right")
            tbl2.add_column("Exit",    justify="right")
            tbl2.add_column("Hold",    justify="right")
            tbl2.add_column("Return",  justify="right")

            for t in top_trades:
                colour = "green" if t["return_pct"] > 0 else "red"
                tbl2.add_row(
                    t["ticker"],
                    t["signal_rating"],
                    t["signal_date"],
                    f"{t['composite_score']:.1f}",
                    f"${t['entry_price']:.2f}",
                    f"${t['exit_price']:.2f}",
                    f"{t['hold_days']}d",
                    f"[{colour}]{t['return_pct']:+.2f}%[/{colour}]",
                )
            console.print(tbl2)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


def main():
    parser = argparse.ArgumentParser(description="Signal Backtester")
    parser.add_argument("--rating",  nargs="+", default=["BUY", "REVERSION"],
                        help="Ratings to test (default: BUY REVERSION)")
    parser.add_argument("--days",    nargs="+", type=int, default=[5, 10, 20],
                        help="Hold periods in days (default: 5 10 20)")
    parser.add_argument("--limit",   type=int, default=50,
                        help="Max signals to test per rating/period (default: 50)")
    parser.add_argument("--min-score", type=float, default=60.0,
                        help="Minimum composite score (default: 60)")
    parser.add_argument("--show",    action="store_true",
                        help="Show last backtest results without running new one")
    args = parser.parse_args()

    if args.show:
        show_results()
        return

    logger.info("=" * 60)
    logger.info("BACKTEST START")
    logger.info(f"  Ratings:    {args.rating}")
    logger.info(f"  Hold days:  {args.days}")
    logger.info(f"  Min score:  {args.min_score}")
    logger.info(f"  Limit:      {args.limit} signals per combination")
    logger.info("=" * 60)

    results = run_full_backtest(
        db_path   = DATABASE_PATH,
        ratings   = args.rating,
        hold_days = args.days,
        min_score = args.min_score,
        limit     = args.limit,
    )

    save_backtest_results(DATABASE_PATH, results)
    show_results()
    logger.info("Backtest complete.")


if __name__ == "__main__":
    main()
