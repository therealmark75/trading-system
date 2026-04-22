# dashboard/dashboard.py - Phase 1 + Phase 2
import sys, argparse
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table   import Table
from rich.panel   import Panel
from rich         import box

from config.settings import DATABASE_PATH
from database.db import (get_connection, get_latest_screener, get_recent_insiders,
    get_cluster_signals, get_top_signals, get_signal_summary)

console = Console()

RATING_COLOUR = {
    "STRONG_BUY":  "bold green",
    "BUY":         "green",
    "WATCH":       "yellow",
    "REVERSION":   "cyan",
    "AVOID":       "red",
    "SHORT_WATCH": "bold red",
}

def fmt_pct(val, pos_green=True):
    if val is None: return "[dim]-[/dim]"
    c = ("green" if val >= 0 else "red") if pos_green else ("red" if val >= 0 else "green")
    return f"[{c}]{val:+.2f}%[/{c}]"

def fmt_score(val):
    if val is None: return "[dim]-[/dim]"
    if val >= 70:   return f"[bold green]{val:.1f}[/bold green]"
    if val >= 55:   return f"[green]{val:.1f}[/green]"
    if val >= 45:   return f"[yellow]{val:.1f}[/yellow]"
    return f"[red]{val:.1f}[/red]"

def show_signal_summary():
    summary = get_signal_summary(DATABASE_PATH)
    if not summary:
        console.print("[yellow]No signal scores yet. Run: python main.py signals[/yellow]")
        return
    tbl = Table(title="Signal Rating Distribution", box=box.ROUNDED)
    tbl.add_column("Rating", style="bold")
    tbl.add_column("Count",  justify="right")
    tbl.add_column("Avg Score", justify="right")
    for r in summary:
        colour = RATING_COLOUR.get(r["rating"], "white")
        tbl.add_row(f"[{colour}]{r['rating']}[/{colour}]",
                    str(r["count"]), fmt_score(r["avg_score"]))
    console.print(tbl)

def show_top_signals(rating=None, limit=25):
    rows = get_top_signals(DATABASE_PATH, rating=rating, limit=limit * 3)
    if not rows:
        console.print("[yellow]No signal data yet.[/yellow]")
        return

    # Deduplicate by ticker, keep highest composite score
    seen = {}
    for r in rows:
        t = r.get("ticker","")
        if t not in seen or r.get("composite_score",0) > seen[t].get("composite_score",0):
            seen[t] = r
    rows = sorted(seen.values(), key=lambda x: x.get("composite_score",0), reverse=True)[:limit]

    title = f"Top Signals" + (f" — {rating}" if rating else " — All Ratings")
    tbl = Table(title=title, box=box.ROUNDED, show_lines=True, expand=True)
    tbl.add_column("Ticker",    style="bold cyan", no_wrap=True, min_width=6)
    tbl.add_column("Rating",    style="bold",      no_wrap=True, min_width=10)
    tbl.add_column("Score",     justify="right",   no_wrap=True, min_width=6)
    tbl.add_column("Mom",       justify="right",   no_wrap=True, min_width=5)
    tbl.add_column("Qual",      justify="right",   no_wrap=True, min_width=5)
    tbl.add_column("Ins",       justify="right",   no_wrap=True, min_width=5)
    tbl.add_column("Rev",       justify="right",   no_wrap=True, min_width=5)
    tbl.add_column("Flags",     no_wrap=True,      ratio=1)

    for r in rows:
        colour = RATING_COLOUR.get(r.get("rating"), "white")
        # Convert pipe-separated flags to short emoji-style abbreviations
        raw_flags = (r.get("flags") or "").split("|")
        short_flags = []
        for f in raw_flags:
            f = f.strip()
            if not f: continue
            if "Above 50d"  in f: short_flags.append("↑50d")
            elif "Below 50d" in f: short_flags.append("↓50d")
            if "Above 200d"  in f: short_flags.append("↑200d")
            elif "Below 200d" in f: short_flags.append("↓200d")
            if "52-week high" in f: short_flags.append("🔝52wH")
            if "52-week low"  in f: short_flags.append("📍52wL")
            if "insider buying" in f.lower(): short_flags.append("★Ins")
            if "insider selling" in f.lower(): short_flags.append("⚠Ins")
            if "Overbought" in f: short_flags.append("⚠RSI+")
            if "Oversold"   in f: short_flags.append("↩RSI-")
            if "reversion"  in f.lower(): short_flags.append("↩Rev")
            if "short interest" in f.lower(): short_flags.append("⚠Short")
            if "analyst"    in f.lower(): short_flags.append("✓Rec")

        flags_str = "  ".join(short_flags) if short_flags else "-"

        tbl.add_row(
            r.get("ticker",""),
            f"[{colour}]{r.get('rating','')}[/{colour}]",
            fmt_score(r.get("composite_score")),
            fmt_score(r.get("momentum_score")),
            fmt_score(r.get("quality_score")),
            fmt_score(r.get("insider_score")),
            fmt_score(r.get("reversion_score")),
            f"[dim]{flags_str}[/dim]",
        )
    console.print(tbl)

def show_signals(days=14):
    signals = get_cluster_signals(DATABASE_PATH, days=days)
    if not signals:
        console.print(f"[yellow]No cluster signals in last {days} days.[/yellow]")
        return
    tbl = Table(title=f"Insider Cluster Signals (last {days} days)", box=box.ROUNDED, show_lines=True)
    tbl.add_column("Ticker",    style="bold cyan")
    tbl.add_column("Type",      style="bold")
    tbl.add_column("Insiders",  justify="right")
    tbl.add_column("Value ($)", justify="right")
    tbl.add_column("Detected")
    for s in sorted(signals, key=lambda x: x.get("total_value") or 0, reverse=True):
        c = "green" if "BUY" in (s.get("signal_type") or "") else "red"
        tbl.add_row(
            s["ticker"],
            f"[{c}]{s['signal_type']}[/{c}]",
            str(s["insider_count"]),
            f"{s['total_value']:,.0f}" if s.get("total_value") else "-",
            (s.get("detected_at") or "")[:16],
        )
    console.print(tbl)

def show_sector_summary():
    conn = get_connection(DATABASE_PATH)
    cur  = conn.cursor()
    cur.execute("""
        SELECT sector, COUNT(*) as tickers,
               ROUND(AVG(rsi_14),1) as avg_rsi,
               ROUND(AVG(change_pct),2) as avg_change,
               SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) as gainers,
               SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) as losers
        FROM screener_snapshots
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM screener_snapshots)
          AND sector IS NOT NULL
        GROUP BY sector ORDER BY avg_change DESC
    """)
    rows = cur.fetchall()
    conn.close()
    if not rows:
        console.print("[yellow]No screener data yet.[/yellow]")
        return
    tbl = Table(title="Sector Summary", box=box.ROUNDED, show_lines=True)
    tbl.add_column("Sector",   style="bold")
    tbl.add_column("Tickers",  justify="right")
    tbl.add_column("Avg Chg%", justify="right")
    tbl.add_column("Avg RSI",  justify="right")
    tbl.add_column("↑",        justify="right", style="green")
    tbl.add_column("↓",        justify="right", style="red")
    for r in rows:
        tbl.add_row(r["sector"] or "-", str(r["tickers"]),
                    fmt_pct(r["avg_change"]), str(r["avg_rsi"] or "-"),
                    str(r["gainers"] or 0), str(r["losers"] or 0))
    console.print(tbl)

def show_db_overview():
    conn = get_connection(DATABASE_PATH)
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM screener_snapshots"); s = cur.fetchone()[0]
    cur.execute("SELECT MAX(scraped_at) FROM screener_snapshots"); ls = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM insider_trades"); it = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM signal_scores"); ss = cur.fetchone()[0]
    cur.execute("SELECT MAX(scored_at) FROM signal_scores"); lsg = cur.fetchone()[0]
    conn.close()
    txt = (f"[cyan]Screener rows:[/cyan]  {s:,}   [dim]last: {(ls or 'never')[:16]}[/dim]\n"
           f"[magenta]Insider trades:[/magenta] {it:,}\n"
           f"[green]Signal scores:[/green]  {ss:,}   [dim]last: {(lsg or 'never')[:16]}[/dim]")
    console.print(Panel(txt, title="[bold white]Database Overview[/bold white]", border_style="blue"))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("view", nargs="?", default="all",
        choices=["all","overview","sectors","signals","insiders","scores",
                 "strong_buys","reversion","news","calendar"])
    parser.add_argument("--days",   type=int, default=14)
    parser.add_argument("--limit",  type=int, default=25)
    parser.add_argument("--rating", help="Filter scores by rating")
    args = parser.parse_args()

    console.rule("[bold blue]Trading System Dashboard[/bold blue]")
    console.print(f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n")

    v = args.view
    if v in ("overview","all"):   show_db_overview()
    if v in ("sectors", "all"):   show_sector_summary()
    if v in ("signals", "all"):   show_signals(days=args.days)
    if v in ("scores",  "all"):   show_signal_summary()
    if v in ("all",):             show_top_signals(limit=args.limit)
    if v == "strong_buys":        show_top_signals(rating="STRONG_BUY", limit=args.limit)
    if v == "reversion":          show_top_signals(rating="REVERSION",  limit=args.limit)
    if v == "scores":             show_top_signals(rating=args.rating,   limit=args.limit)
    if v in ("news",    "all"):   show_news_sentiment(limit=args.limit)
    if v in ("calendar","all"):   show_calendar(days=args.days)

if __name__ == "__main__":
    main()


def show_news_sentiment(limit=20):
    from database.db import get_ticker_sentiment, get_connection
    rows = get_ticker_sentiment(DATABASE_PATH)
    if not rows:
        console.print("[yellow]No news sentiment data yet. Run: python main.py news[/yellow]")
        return

    tbl = Table(title="News Sentiment (latest)", box=box.ROUNDED, show_lines=True)
    tbl.add_column("Ticker",    style="bold cyan")
    tbl.add_column("Avg Sentiment", justify="right")
    tbl.add_column("Bullish",   justify="right", style="green")
    tbl.add_column("Bearish",   justify="right", style="red")
    tbl.add_column("Articles",  justify="right")

    for r in rows[:limit]:
        s = r.get("avg_sentiment", 0)
        colour = "green" if s > 0.05 else ("red" if s < -0.05 else "yellow")
        bar    = "█" * min(10, int(abs(s) * 20))
        tbl.add_row(
            r.get("ticker",""),
            f"[{colour}]{s:+.3f} {bar}[/{colour}]",
            str(r.get("bullish_count",0)),
            str(r.get("bearish_count",0)),
            str(r.get("article_count",0)),
        )
    console.print(tbl)


def show_calendar(days=7):
    from database.db import get_upcoming_events
    events = get_upcoming_events(DATABASE_PATH, days=days)
    if not events:
        console.print(f"[yellow]No calendar events. Run: python main.py news[/yellow]")
        return

    tbl = Table(title=f"Economic Calendar (next {days} days)", box=box.ROUNDED, show_lines=True)
    tbl.add_column("Date",    no_wrap=True)
    tbl.add_column("Event",   max_width=40)
    tbl.add_column("Impact",  style="bold")
    tbl.add_column("Sectors", max_width=35)
    tbl.add_column("Forecast")

    impact_colours = {"HIGH": "bold red", "MEDIUM": "yellow", "LOW": "dim", "NONE": "dim"}
    for e in events:
        ic = impact_colours.get(e.get("impact",""), "white")
        tbl.add_row(
            e.get("event_date",""),
            e.get("event_name",""),
            f"[{ic}]{e.get('impact','')}[/{ic}]",
            e.get("affected_sectors",""),
            e.get("forecast","") or "-",
        )
    console.print(tbl)
