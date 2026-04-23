# web/app.py
# ─────────────────────────────────────────────────
# Phase 5: Flask web dashboard backend.
# Serves the web UI and provides JSON API endpoints.
#
# Usage:
#   python web/app.py
#   Then open: http://localhost:5000
# ─────────────────────────────────────────────────

import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, send_from_directory

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import DATABASE_PATH
from database.db import (
    get_connection,
    get_latest_screener,
    get_recent_insiders,
    get_cluster_signals,
    get_top_signals,
    get_signal_summary,
    get_ticker_sentiment,
    get_upcoming_events,
)

app = Flask(__name__,
            template_folder="templates",
            static_folder="static")


# ── Helper ────────────────────────────────────────

def db_query(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_connection(DATABASE_PATH)
    cur  = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Page routes ───────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── API routes ────────────────────────────────────

@app.route("/api/overview")
def api_overview():
    """Database overview stats."""
    rows = db_query("""
        SELECT
            (SELECT COUNT(*) FROM screener_snapshots) as screener_rows,
            (SELECT COUNT(DISTINCT ticker) FROM screener_snapshots) as unique_tickers,
            (SELECT MAX(scraped_at) FROM screener_snapshots) as last_screener,
            (SELECT COUNT(*) FROM insider_trades) as insider_trades,
            (SELECT MAX(scraped_at) FROM insider_trades) as last_insider,
            (SELECT COUNT(*) FROM signal_scores) as signal_scores,
            (SELECT MAX(scored_at) FROM signal_scores) as last_scored
    """)
    return jsonify(rows[0] if rows else {})


@app.route("/api/signals")
def api_signals():
    """Top signal scores."""
    rows = get_top_signals(DATABASE_PATH, limit=200)
    # Deduplicate by ticker
    seen = {}
    for r in rows:
        t = r.get("ticker","")
        if t not in seen or r.get("composite_score",0) > seen[t].get("composite_score",0):
            seen[t] = r
    deduped = sorted(seen.values(), key=lambda x: x.get("composite_score",0), reverse=True)

    # Parse flags into list
    for r in deduped:
        raw = (r.get("flags") or "").split("|")
        r["flag_list"] = [f.strip() for f in raw if f.strip()]
        r.pop("flags", None)

    return jsonify(deduped)


@app.route("/api/signals/<rating>")
def api_signals_by_rating(rating):
    """Signals filtered by rating."""
    rows = get_top_signals(DATABASE_PATH, rating=rating.upper(), limit=100)
    seen = {}
    for r in rows:
        t = r.get("ticker","")
        if t not in seen or r.get("composite_score",0) > seen[t].get("composite_score",0):
            seen[t] = r
    deduped = sorted(seen.values(), key=lambda x: x.get("composite_score",0), reverse=True)
    for r in deduped:
        raw = (r.get("flags") or "").split("|")
        r["flag_list"] = [f.strip() for f in raw if f.strip()]
        r.pop("flags", None)
    return jsonify(deduped)


@app.route("/api/signal_summary")
def api_signal_summary():
    """Rating distribution."""
    rows = get_signal_summary(DATABASE_PATH)
    return jsonify(rows)


@app.route("/api/sectors")
def api_sectors():
    """Sector summary from latest screener."""
    rows = db_query("""
        SELECT
            sector,
            COUNT(DISTINCT ticker) as tickers,
            ROUND(AVG(rsi_14), 1) as avg_rsi,
            ROUND(AVG(change_pct), 2) as avg_change,
            ROUND(AVG(sma_50_pct), 2) as avg_50sma,
            ROUND(AVG(analyst_recom), 2) as avg_analyst,
            SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) as gainers,
            SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) as losers,
            ROUND(AVG(pe_ratio), 1) as avg_pe
        FROM screener_snapshots
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM screener_snapshots)
          AND sector IS NOT NULL
        GROUP BY sector
        ORDER BY avg_change DESC
    """)
    return jsonify(rows)


@app.route("/api/insider_signals")
def api_insider_signals():
    """Recent cluster signals."""
    rows = get_cluster_signals(DATABASE_PATH, days=14)
    return jsonify(rows)


@app.route("/api/insider_trades")
def api_insider_trades():
    """Recent insider trades."""
    rows = get_recent_insiders(DATABASE_PATH, days=14)
    return jsonify(rows[:100])


@app.route("/api/news")
def api_news():
    """Latest news sentiment scores."""
    rows = get_ticker_sentiment(DATABASE_PATH)
    return jsonify(rows)


@app.route("/api/ticker/<ticker>")
def api_ticker(ticker):
    """Full data for a single ticker."""
    ticker = ticker.upper()

    # Latest screener data
    screener = db_query("""
        SELECT * FROM screener_snapshots
        WHERE ticker = ?
        ORDER BY scraped_at DESC LIMIT 1
    """, (ticker,))

    # Latest signal score
    signal = db_query("""
        SELECT * FROM signal_scores
        WHERE ticker = ?
        ORDER BY scored_at DESC LIMIT 1
    """, (ticker,))

    # Recent insider trades
    insiders = db_query("""
        SELECT * FROM insider_trades
        WHERE ticker = ?
        ORDER BY transaction_date DESC LIMIT 20
    """, (ticker,))

    # News headlines
    news = db_query("""
        SELECT * FROM news_sentiment
        WHERE ticker = ?
        ORDER BY scraped_at DESC LIMIT 20
    """, (ticker,))

    # Signal history (last 30 days)
    history = db_query("""
        SELECT DATE(scored_at) as date, composite_score, rating
        FROM signal_scores
        WHERE ticker = ?
        ORDER BY scored_at ASC
    """, (ticker,))

    return jsonify({
        "ticker":   ticker,
        "screener": screener[0] if screener else {},
        "signal":   signal[0]   if signal   else {},
        "insiders": insiders,
        "news":     news,
        "history":  history,
    })


@app.route("/api/run_log")
def api_run_log():
    """Recent job run history."""
    rows = db_query("""
        SELECT * FROM run_log
        ORDER BY run_at DESC LIMIT 50
    """)
    return jsonify(rows)


@app.route("/api/backtest")
def api_backtest():
    """Latest backtest results."""
    try:
        rows = db_query("""
            SELECT * FROM backtest_results
            WHERE run_at = (SELECT MAX(run_at) FROM backtest_results)
            ORDER BY rating, hold_days
        """)
        return jsonify(rows)
    except Exception:
        return jsonify([])


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Trading System Web Dashboard")
    print("  Open: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=False, host="0.0.0.0", port=5000)
