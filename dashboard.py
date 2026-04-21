# dashboard/dashboard.py
# ─────────────────────────────────────────────────
# Rich terminal dashboard. Run any time to get a
# live view of what's in the database.
# Usage: python dashboard/dashboard.py
# ─────────────────────────────────────────────────

import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console   import Console
from rich.table     import Table
from rich.panel     import Panel
from rich.columns   import Columns
from rich.text      import Text
from rich           import box

from config.settings import DATABASE_PATH
from database.db     import (
    get_connection,
    get_latest_screener,
    get_recent_insiders,
    get_cluster_signals,
)

console = Console()


# ── Helpers ───────────────────────────────────────

def fmt_pct(val, positive_green=True) -> str:
    if val is None:
        return "[dim]-[/dim]"
    colour = ("green" if val >= 0 else "red") if positive_green else ("red" if val >= 0 else "green")
    return f"[{colour}]{val:+.2f}%[/{colour}]"


def fmt_price(val) -> str:
    if val is None:
        return "[dim]-[/dim]"
    return f"${val:.2f}"


def fmt_int(val) -> str:
    if val is None:
        return "[dim]-[/dim]"
    return f"{int(val):,}"


def get_db_stats(db_path: str) -> dict:
    conn = get_connection(db_path)
    cur  = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM screener_snapshots")
    screener_total = cur.fetchone()[0]

    cur.execute("SELECT MAX(scraped_at) FROM screener_snapshots")
    last_screener = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM insider_trades")
    insider_total = cur.fetchone()[0]

    cur.execute("SELECT MAX(scraped_at) FROM insider_trades")
    last_insider = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM insider_signals")
    signal_total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT ticker) FROM screener_snapshots")
    unique_tickers = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT sector) FROM screener_snapshots")
    unique_sectors = cur.fetchone()[0]

    conn.close()
    return {
        "screener_total": screener_total,
        "last_screener":  last_screener,
        "insider_total":  insider_total,
        "last_insider":   last_insider,
        "signal_total":   signal_total,
        "unique_tickers": unique_tickers,
        "unique_sectors": unique_sectors,
    }


# ── Views ─────────────────────────────────────────

def show_overview():
    stats = get_db_stats(DATABASE_PATH)

    panel_text = (
        f"[bold cyan]Screener rows:[/bold cyan]  {stats['screener_total']:,}\n"
        f"[bold cyan]Unique tickers:[/bold cyan] {stats['unique_tickers']:,}\n"
        f"[bold cyan]Sectors:[/bold cyan]        {stats['unique_sectors']}\n"
        f"[bold cyan]Last screener:[/bold cyan]  {(stats['last_screener'] or 'Never')[:16]}\n\n"
        f"[bold magenta]Insider rows:[/bold magenta]   {stats['insider_total']:,}\n"
        f"[bold magenta]Last insider:[/bold magenta]   {(stats['last_insider'] or 'Never')[:16]}\n"
        f"[bold magenta]Total signals:[/bold magenta]  {stats['signal_total']:,}"
    )
    console.print(Panel(panel_text, title="[bold white]Database Overview[/bold white]",
                        border_style="blue"))


def show_signals(days: int = 30):
    signals = get_cluster_signals(DATABASE_PATH, days=days)

    if not signals:
        console.print(f"[yellow]No cluster signals in last {days} days.[/yellow]")
        return

    tbl = Table(
        title=f"Insider Cluster Signals (last {days} days)",
        box=box.ROUNDED, show_lines=True,
    )
    tbl.add_column("Ticker",    style="bold cyan", no_wrap=True)
    tbl.add_column("Signal",    style="bold")
    tbl.add_column("Insiders",  justify="right")
    tbl.add_column("Value ($)", justify="right")
    tbl.add_column("Window",    justify="right")
    tbl.add_column("Detected",  no_wrap=True)

    for s in sorted(signals, key=lambda x: x.get("total_value") or 0, reverse=True):
        is_buy   = "BUY" in (s.get("signal_type") or "")
        colour   = "green" if is_buy else "red"
        tbl.add_row(
            s["ticker"],
            f"[{colour}]{s['signal_type']}[/{colour}]",
            str(s["insider_count"]),
            f"{s['total_value']:,.0f}" if s.get("total_value") else "-",
            f"{s['window_days']}d",
            (s.get("detected_at") or "")[:16],
        )
    console.print(tbl)


def show_insiders(days: int = 14, tx_type: str = None):
    rows = get_recent_insiders(DATABASE_PATH, days=days, transaction_type=tx_type)

    if not rows:
        console.print(f"[yellow]No insider trades in last {days} days.[/yellow]")
        return

    tbl = Table(
        title=f"Insider Trades (last {days} days{f', {tx_type}' if tx_type else ''})",
        box=box.ROUNDED, show_lines=True,
    )
    tbl.add_column("Ticker",   style="bold cyan", no_wrap=True)
    tbl.add_column("Insider",  max_width=25)
    tbl.add_column("Title",    max_width=20)
    tbl.add_column("Type",     style="bold")
    tbl.add_column("Shares",   justify="right")
    tbl.add_column("Price",    justify="right")
    tbl.add_column("Value ($)",justify="right")
    tbl.add_column("Date",     no_wrap=True)

    for r in rows[:50]:  # cap at 50 rows for readability
        is_buy  = r.get("transaction_type") == "Buy"
        colour  = "green" if is_buy else "red"
        tbl.add_row(
            r.get("ticker", ""),
            r.get("insider_name", "") or "-",
            r.get("insider_title", "") or "-",
            f"[{colour}]{r.get('transaction_type', '')}[/{colour}]",
            fmt_int(r.get("shares")),
            fmt_price(r.get("price")),
            f"{r.get('value', 0):,.0f}" if r.get("value") else "-",
            r.get("transaction_date", "") or "-",
        )
    console.print(tbl)
    if len(rows) > 50:
        console.print(f"[dim]... and {len(rows)-50} more rows[/dim]")


def show_screener(sector: str = None, sort_by: str = "rsi_14", top_n: int = 30):
    rows = get_latest_screener(DATABASE_PATH, sector=sector)

    if not rows:
        console.print("[yellow]No screener data in database yet.[/yellow]")
        return

    # Sort
    def sort_key(r):
        v = r.get(sort_by)
        return v if v is not None else float("-inf")

    rows = sorted(rows, key=sort_key, reverse=True)[:top_n]

    title = f"Screener Snapshot"
    if sector:
        title += f" — {sector}"
    title += f" | Top {top_n} by {sort_by}"

    tbl = Table(title=title, box=box.ROUNDED, show_lines=True)
    tbl.add_column("Ticker",   style="bold cyan", no_wrap=True)
    tbl.add_column("Company",  max_width=22)
    tbl.add_column("Sector",   max_width=18)
    tbl.add_column("Price",    justify="right")
    tbl.add_column("Chg %",    justify="right")
    tbl.add_column("RSI",      justify="right")
    tbl.add_column("50d SMA%", justify="right")
    tbl.add_column("200d SMA%",justify="right")
    tbl.add_column("Vol",      justify="right")
    tbl.add_column("Analyst",  justify="right")

    for r in rows:
        tbl.add_row(
            r.get("ticker", ""),
            (r.get("company") or "")[:22],
            (r.get("sector") or "")[:18],
            fmt_price(r.get("price")),
            fmt_pct(r.get("change_pct")),
            str(r.get("rsi_14", "-") or "-"),
            fmt_pct(r.get("sma_50_pct")),
            fmt_pct(r.get("sma_200_pct")),
            fmt_int(r.get("volume")),
            str(r.get("analyst_recom", "-") or "-"),
        )
    console.print(tbl)


def show_sector_summary():
    """Per-sector stats from the latest screener snapshot."""
    conn = get_connection(DATABASE_PATH)
    cur  = conn.cursor()

    cur.execute("""
        SELECT
            sector,
            COUNT(*) as ticker_count,
            ROUND(AVG(rsi_14), 1) as avg_rsi,
            ROUND(AVG(change_pct), 2) as avg_change,
            ROUND(AVG(sma_50_pct), 2) as avg_50sma,
            ROUND(AVG(analyst_recom), 2) as avg_analyst,
            SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) as gainers,
            SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) as losers
        FROM screener_snapshots
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM screener_snapshots)
          AND sector IS NOT NULL
        GROUP BY sector
        ORDER BY avg_change DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        console.print("[yellow]No screener data yet.[/yellow]")
        return

    tbl = Table(title="Sector Summary (latest snapshot)", box=box.ROUNDED, show_lines=True)
    tbl.add_column("Sector",      style="bold")
    tbl.add_column("Tickers",     justify="right")
    tbl.add_column("Avg Chg%",    justify="right")
    tbl.add_column("Avg RSI",     justify="right")
    tbl.add_column("50d SMA%",    justify="right")
    tbl.add_column("Analyst",     justify="right")
    tbl.add_column("↑ Gainers",   justify="right", style="green")
    tbl.add_column("↓ Losers",    justify="right", style="red")

    for r in rows:
        tbl.add_row(
            r["sector"] or "-",
            str(r["ticker_count"]),
            fmt_pct(r["avg_change"]),
            str(r["avg_rsi"] or "-"),
            fmt_pct(r["avg_50sma"]),
            str(r["avg_analyst"] or "-"),
            str(r["gainers"] or 0),
            str(r["losers"] or 0),
        )
    console.print(tbl)


# ── Main ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trading System Dashboard")
    parser.add_argument("view",
        choices=["overview", "signals", "insiders", "screener", "sectors", "all"],
        nargs="?", default="all",
        help="Which view to display (default: all)")
    parser.add_argument("--sector",   help="Filter screener by sector")
    parser.add_argument("--days",     type=int, default=14,
                        help="Days lookback for insider/signal views (default: 14)")
    parser.add_argument("--sort",     default="rsi_14",
                        help="Screener sort column (default: rsi_14)")
    parser.add_argument("--top",      type=int, default=30,
                        help="Max rows in screener table (default: 30)")
    parser.add_argument("--type",     dest="tx_type",
                        help="Filter insider by type: Buy | Sale | Option Exercise")
    args = parser.parse_args()

    console.rule("[bold blue]Trading System Dashboard[/bold blue]")
    console.print(f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n")

    v = args.view
    if v in ("overview", "all"):  show_overview()
    if v in ("sectors",  "all"):  show_sector_summary()
    if v in ("signals",  "all"):  show_signals(days=args.days)
    if v in ("insiders", "all"):  show_insiders(days=args.days, tx_type=args.tx_type)
    if v in ("screener", "all"):  show_screener(sector=args.sector,
                                                 sort_by=args.sort, top_n=args.top)


if __name__ == "__main__":
    main()
