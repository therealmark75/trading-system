# signals/backtester.py
# ─────────────────────────────────────────────────
# Phase 4: Signal backtester.
#
# Takes historical signal scores from the DB and
# tests how those tickers actually performed over
# holding periods of 5, 10, 20, and 60 days.
#
# Uses yfinance for historical price data.
# Produces win rate, avg return, Sharpe-like ratio.
# ─────────────────────────────────────────────────

import logging
import time
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not available. Install with: pip install yfinance")

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


# ── Data classes ──────────────────────────────────

@dataclass
class TradeResult:
    ticker:         str
    signal_date:    str
    signal_rating:  str
    composite_score:float
    entry_price:    float
    hold_days:      int
    exit_price:     float = None
    return_pct:     float = None
    win:            bool  = None
    error:          str   = None


@dataclass
class BacktestSummary:
    rating:         str
    hold_days:      int
    total_trades:   int
    winning_trades: int
    losing_trades:  int
    win_rate:       float
    avg_return:     float
    median_return:  float
    best_trade:     float
    worst_trade:    float
    avg_win:        float
    avg_loss:       float
    profit_factor:  float   # avg_win / abs(avg_loss)
    sharpe_approx:  float   # avg_return / std_return


# ── Price fetcher ─────────────────────────────────

def fetch_price_on_date(ticker: str, date_str: str) -> float | None:
    """Fetch closing price for a ticker on or near a given date."""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        # Fetch a window around the date to handle weekends/holidays
        start = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
        end   = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

        tk   = yf.Ticker(ticker)
        hist = tk.history(start=start, end=end, auto_adjust=True)

        if hist.empty:
            return None

        # Get the closest date to our target
        target = pd.Timestamp(date_str)
        closest_idx = (hist.index - target).abs().argmin()
        return float(hist["Close"].iloc[closest_idx])

    except Exception as e:
        logger.debug(f"Price fetch failed for {ticker} on {date_str}: {e}")
        return None


def fetch_price_after_days(ticker: str, start_date: str, hold_days: int) -> float | None:
    """Fetch closing price hold_days after start_date."""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = start_dt + timedelta(days=hold_days + 5)  # buffer for weekends

        tk   = yf.Ticker(ticker)
        hist = tk.history(
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            auto_adjust=True
        )

        if hist.empty or len(hist) < 2:
            return None

        # Skip the first row (entry), get the row closest to hold_days later
        target_dt = start_dt + timedelta(days=hold_days)
        target_ts = pd.Timestamp(target_dt)

        # Find closest available trading day
        diffs = abs(hist.index - target_ts)
        closest_idx = diffs.argmin()
        return float(hist["Close"].iloc[closest_idx])

    except Exception as e:
        logger.debug(f"Exit price fetch failed for {ticker}: {e}")
        return None


# ── Core backtest functions ───────────────────────

def backtest_signals_from_db(
    db_path:    str,
    rating:     str = "BUY",
    hold_days:  int = 20,
    min_score:  float = 60.0,
    limit:      int = 100,
    delay:      float = 0.5,
) -> list[TradeResult]:
    """
    Pull historical signal scores from DB and test their performance.

    Args:
        db_path:   Path to SQLite database
        rating:    Signal rating to test (BUY, REVERSION, STRONG_BUY)
        hold_days: How many days to hold after signal
        min_score: Minimum composite score to include
        limit:     Max signals to test
        delay:     Delay between yfinance calls (be polite)

    Returns:
        List of TradeResult objects
    """
    if not YFINANCE_AVAILABLE:
        logger.error("yfinance required for backtesting. pip install yfinance")
        return []

    # Pull historical signals from DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    cur.execute("""
        SELECT DISTINCT ticker, DATE(scored_at) as signal_date,
               rating, composite_score
        FROM signal_scores
        WHERE rating = ?
          AND composite_score >= ?
        GROUP BY ticker, DATE(scored_at)
        ORDER BY scored_at DESC
        LIMIT ?
    """, (rating, min_score, limit))

    signals = [dict(r) for r in cur.fetchall()]
    conn.close()

    logger.info(f"Backtesting {len(signals)} {rating} signals over {hold_days}-day hold...")

    results = []
    for i, sig in enumerate(signals):
        ticker      = sig["ticker"]
        signal_date = sig["signal_date"]

        logger.info(f"  [{i+1}/{len(signals)}] {ticker} | signal: {signal_date}")

        # Get entry price (day of signal)
        entry_price = fetch_price_on_date(ticker, signal_date)
        if not entry_price:
            results.append(TradeResult(
                ticker=ticker, signal_date=signal_date,
                signal_rating=rating, composite_score=sig["composite_score"],
                entry_price=0, hold_days=hold_days, error="No entry price"
            ))
            time.sleep(delay)
            continue

        # Get exit price (hold_days later)
        exit_price = fetch_price_after_days(ticker, signal_date, hold_days)
        if not exit_price:
            results.append(TradeResult(
                ticker=ticker, signal_date=signal_date,
                signal_rating=rating, composite_score=sig["composite_score"],
                entry_price=entry_price, hold_days=hold_days, error="No exit price"
            ))
            time.sleep(delay)
            continue

        return_pct = ((exit_price - entry_price) / entry_price) * 100
        win        = return_pct > 0

        results.append(TradeResult(
            ticker=ticker, signal_date=signal_date,
            signal_rating=rating, composite_score=sig["composite_score"],
            entry_price=round(entry_price, 2),
            hold_days=hold_days,
            exit_price=round(exit_price, 2),
            return_pct=round(return_pct, 2),
            win=win,
        ))

        logger.info(f"    Entry: ${entry_price:.2f} → Exit: ${exit_price:.2f} | "
                    f"Return: {return_pct:+.2f}% | {'WIN' if win else 'LOSS'}")
        time.sleep(delay)

    return results


def compute_summary(results: list[TradeResult], rating: str, hold_days: int) -> BacktestSummary:
    """Compute aggregate statistics from a list of TradeResults."""
    valid = [r for r in results if r.return_pct is not None]

    if not valid:
        return BacktestSummary(
            rating=rating, hold_days=hold_days, total_trades=0,
            winning_trades=0, losing_trades=0, win_rate=0,
            avg_return=0, median_return=0, best_trade=0, worst_trade=0,
            avg_win=0, avg_loss=0, profit_factor=0, sharpe_approx=0,
        )

    returns  = [r.return_pct for r in valid]
    wins     = [r for r in valid if r.win]
    losses   = [r for r in valid if not r.win]

    avg_return = sum(returns) / len(returns)
    sorted_r   = sorted(returns)
    median_r   = sorted_r[len(sorted_r) // 2]

    avg_win  = sum(r.return_pct for r in wins)  / len(wins)  if wins   else 0
    avg_loss = sum(r.return_pct for r in losses) / len(losses) if losses else 0

    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    # Approximate Sharpe (return / std, annualised roughly)
    if len(returns) > 1:
        import statistics
        std = statistics.stdev(returns)
        sharpe = (avg_return / std) * (252 / hold_days) ** 0.5 if std > 0 else 0
    else:
        sharpe = 0

    return BacktestSummary(
        rating=rating,
        hold_days=hold_days,
        total_trades=len(valid),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=round(len(wins) / len(valid) * 100, 1),
        avg_return=round(avg_return, 2),
        median_return=round(median_r, 2),
        best_trade=round(max(returns), 2),
        worst_trade=round(min(returns), 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        profit_factor=round(profit_factor, 2),
        sharpe_approx=round(sharpe, 2),
    )


def run_full_backtest(
    db_path:   str,
    ratings:   list[str] = None,
    hold_days: list[int] = None,
    min_score: float = 60.0,
    limit:     int = 50,
) -> dict:
    """
    Run backtests across multiple ratings and holding periods.

    Returns nested dict: {rating: {hold_days: BacktestSummary}}
    """
    ratings   = ratings   or ["BUY", "REVERSION", "STRONG_BUY"]
    hold_days = hold_days or [5, 10, 20]

    all_results = {}

    for rating in ratings:
        all_results[rating] = {}
        for days in hold_days:
            logger.info(f"\nBacktesting {rating} | {days}-day hold...")
            results = backtest_signals_from_db(
                db_path=db_path, rating=rating,
                hold_days=days, min_score=min_score, limit=limit,
            )
            summary = compute_summary(results, rating, days)
            all_results[rating][days] = {
                "summary": summary,
                "trades":  results,
            }
            logger.info(
                f"  {rating} {days}d: Win rate {summary.win_rate}% | "
                f"Avg return {summary.avg_return:+.2f}% | "
                f"Profit factor {summary.profit_factor}"
            )

    return all_results


def save_backtest_results(db_path: str, results: dict) -> None:
    """Persist backtest summaries to the database."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at          TEXT,
            rating          TEXT,
            hold_days       INTEGER,
            total_trades    INTEGER,
            win_rate        REAL,
            avg_return      REAL,
            median_return   REAL,
            best_trade      REAL,
            worst_trade     REAL,
            profit_factor   REAL,
            sharpe_approx   REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at          TEXT,
            ticker          TEXT,
            signal_date     TEXT,
            signal_rating   TEXT,
            composite_score REAL,
            entry_price     REAL,
            exit_price      REAL,
            hold_days       INTEGER,
            return_pct      REAL,
            win             INTEGER,
            error           TEXT
        )
    """)

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    for rating, days_data in results.items():
        for days, data in days_data.items():
            s = data["summary"]
            conn.execute("""
                INSERT INTO backtest_results
                    (run_at, rating, hold_days, total_trades, win_rate,
                     avg_return, median_return, best_trade, worst_trade,
                     profit_factor, sharpe_approx)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (now, s.rating, s.hold_days, s.total_trades, s.win_rate,
                  s.avg_return, s.median_return, s.best_trade, s.worst_trade,
                  s.profit_factor, s.sharpe_approx))

            for t in data["trades"]:
                if t.return_pct is not None:
                    conn.execute("""
                        INSERT INTO backtest_trades
                            (run_at, ticker, signal_date, signal_rating,
                             composite_score, entry_price, exit_price,
                             hold_days, return_pct, win, error)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (now, t.ticker, t.signal_date, t.signal_rating,
                          t.composite_score, t.entry_price, t.exit_price,
                          t.hold_days, t.return_pct, int(t.win) if t.win is not None else None,
                          t.error))

    conn.commit()
    conn.close()
    logger.info("Backtest results saved to database.")
