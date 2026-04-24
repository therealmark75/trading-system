# web/app.py - Phase 5 full web dashboard
import sys, json, sqlite3
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import (Flask, jsonify, render_template, request,
                   redirect, url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import DATABASE_PATH
from database.db import (
    get_connection, get_latest_screener, get_recent_insiders,
    get_cluster_signals, get_top_signals, get_signal_summary,
    get_ticker_sentiment, initialise_user_schema,
    create_user, get_user_by_username, get_user_by_id,
    get_watchlist, add_to_watchlist, remove_from_watchlist,
    get_top_signals_of_day, generate_top_signals_of_day,
)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "signalintel-secret-change-in-production-2026"

# Ensure user tables exist
initialise_user_schema(DATABASE_PATH)


# ── Auth helpers ──────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def current_user():
    if "user_id" in session:
        return get_user_by_id(DATABASE_PATH, session["user_id"])
    return None

def db_query(sql, params=()):
    conn = get_connection(DATABASE_PATH)
    cur  = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Page routes ───────────────────────────────────

@app.route("/")
@login_required
def index():
    user = current_user()
    # Generate today's top signals if not done yet
    today = datetime.now().strftime("%Y-%m-%d")
    top = get_top_signals_of_day(DATABASE_PATH, today)
    if not top:
        top = generate_top_signals_of_day(DATABASE_PATH)
    return render_template("index.html", user=user, top_signals=top)


@app.route("/login", methods=["GET","POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        user = get_user_by_username(DATABASE_PATH, username)
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"]  = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))
        flash("Invalid username or password")
    return render_template("login.html")


@app.route("/register", methods=["GET","POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username","").strip()
        email    = request.form.get("email","").strip()
        password = request.form.get("password","")
        confirm  = request.form.get("confirm","")
        if not username or not email or not password:
            flash("All fields required")
        elif password != confirm:
            flash("Passwords do not match")
        elif len(password) < 6:
            flash("Password must be at least 6 characters")
        elif get_user_by_username(DATABASE_PATH, username):
            flash("Username already taken")
        else:
            pw_hash = pw_hash = generate_password_hash(password, method='pbkdf2:sha256')
            user_id = create_user(DATABASE_PATH, username, email, pw_hash)
            session["user_id"]  = user_id
            session["username"] = username
            return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/ticker/<ticker>")
@login_required
def ticker_page(ticker):
    return render_template("ticker.html", ticker=ticker.upper())


@app.route("/watchlist")
@login_required
def watchlist():
    user  = current_user()
    items = get_watchlist(DATABASE_PATH, user["id"])
    return render_template("watchlist.html", user=user, items=items)


# ── Watchlist API ─────────────────────────────────

@app.route("/api/watchlist/add", methods=["POST"])
@login_required
def api_watchlist_add():
    user   = current_user()
    ticker = (request.json or {}).get("ticker","").upper()
    notes  = (request.json or {}).get("notes","")
    if not ticker:
        return jsonify({"ok": False, "error": "No ticker"})
    ok = add_to_watchlist(DATABASE_PATH, user["id"], ticker, notes)
    return jsonify({"ok": ok, "ticker": ticker})


@app.route("/api/watchlist/remove", methods=["POST"])
@login_required
def api_watchlist_remove():
    user   = current_user()
    ticker = (request.json or {}).get("ticker","").upper()
    remove_from_watchlist(DATABASE_PATH, user["id"], ticker)
    return jsonify({"ok": True, "ticker": ticker})


@app.route("/api/watchlist")
@login_required
def api_watchlist():
    user  = current_user()
    items = get_watchlist(DATABASE_PATH, user["id"])
    return jsonify(items)


# ── Data API ──────────────────────────────────────

@app.route("/api/overview")
@login_required
def api_overview():
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
@login_required
def api_signals():
    rows = db_query("""
        SELECT ticker, rating, MAX(composite_score) as composite_score,
               momentum_score, quality_score, insider_score,
               reversion_score, flags, MAX(scored_at) as scored_at
        FROM signal_scores
        WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        GROUP BY ticker
        ORDER BY composite_score DESC
        LIMIT 200
    """)
    for r in rows:
        raw = (r.get("flags") or "").split("|")
        r["flag_list"] = [f.strip() for f in raw if f.strip()]
        r.pop("flags", None)
    return jsonify(rows)


@app.route("/api/signals/<rating>")
@login_required
def api_signals_by_rating(rating):
    rows = db_query("""
        SELECT ticker, rating, MAX(composite_score) as composite_score,
               momentum_score, quality_score, insider_score,
               reversion_score, flags, MAX(scored_at) as scored_at
        FROM signal_scores
        WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
          AND rating = ?
        GROUP BY ticker
        ORDER BY composite_score DESC
        LIMIT 100
    """, (rating.upper(),))
    for r in rows:
        raw = (r.get("flags") or "").split("|")
        r["flag_list"] = [f.strip() for f in raw if f.strip()]
        r.pop("flags", None)
    return jsonify(rows)


@app.route("/api/signal_summary")
@login_required
def api_signal_summary():
    return jsonify(get_signal_summary(DATABASE_PATH))


@app.route("/api/sectors")
@login_required
def api_sectors():
    rows = db_query("""
        SELECT
            sector,
            COUNT(DISTINCT ticker) as tickers,
            ROUND(AVG(rsi_14), 1) as avg_rsi,
            ROUND(AVG(change_pct), 2) as avg_change,
            ROUND(AVG(sma_50_pct), 2) as avg_50sma,
            ROUND(AVG(analyst_recom), 2) as avg_analyst,
            SUM(CASE WHEN change_pct > 0 THEN 1 ELSE 0 END) as gainers,
            SUM(CASE WHEN change_pct < 0 THEN 1 ELSE 0 END) as losers
        FROM screener_snapshots
        WHERE scraped_at >= datetime('now', '-2 days')
          AND sector IS NOT NULL
        GROUP BY sector
        ORDER BY avg_change DESC
    """)
    return jsonify(rows)


@app.route("/api/insider_signals")
@login_required
def api_insider_signals():
    rows = db_query("""
        SELECT ticker, signal_type, MAX(insider_count) as insider_count,
               MAX(total_value) as total_value, MAX(detected_at) as detected_at, notes
        FROM insider_signals
        WHERE detected_at >= datetime('now', '-14 days')
        GROUP BY ticker, signal_type
        ORDER BY total_value DESC
    """)
    return jsonify(rows)


@app.route("/api/news")
@login_required
def api_news():
    return jsonify(get_ticker_sentiment(DATABASE_PATH))


@app.route("/api/top_signals")
@login_required
def api_top_signals():
    today = datetime.now().strftime("%Y-%m-%d")
    top   = get_top_signals_of_day(DATABASE_PATH, today)
    if not top:
        top = generate_top_signals_of_day(DATABASE_PATH)
    return jsonify(top)


@app.route("/api/ticker/<ticker>")
@login_required
def api_ticker(ticker):
    ticker = ticker.upper()
    user   = current_user()

    screener = db_query("""
        SELECT * FROM screener_snapshots WHERE ticker = ?
        ORDER BY scraped_at DESC LIMIT 1
    """, (ticker,))

    signal = db_query("""
        SELECT * FROM signal_scores WHERE ticker = ?
        ORDER BY scored_at DESC LIMIT 1
    """, (ticker,))

    insiders = db_query("""
        SELECT * FROM insider_trades WHERE ticker = ?
        ORDER BY transaction_date DESC LIMIT 20
    """, (ticker,))

    news = db_query("""
        SELECT * FROM news_sentiment WHERE ticker = ?
        ORDER BY scraped_at DESC LIMIT 20
    """, (ticker,))

    history = db_query("""
        SELECT DATE(scored_at) as date,
               MAX(composite_score) as composite_score, rating
        FROM signal_scores WHERE ticker = ?
        GROUP BY DATE(scored_at)
        ORDER BY date ASC
    """, (ticker,))

    # Check if in watchlist
    in_watchlist = False
    if user:
        wl = db_query("SELECT 1 FROM watchlists WHERE user_id=? AND ticker=?",
                      (user["id"], ticker))
        in_watchlist = len(wl) > 0

    return jsonify({
        "ticker":      ticker,
        "screener":    screener[0] if screener else {},
        "signal":      signal[0]   if signal   else {},
        "insiders":    insiders,
        "news":        news,
        "history":     history,
        "in_watchlist":in_watchlist,
    })


@app.route("/api/run_log")
@login_required
def api_run_log():
    return jsonify(db_query(
        "SELECT * FROM run_log ORDER BY run_at DESC LIMIT 50"
    ))


@app.route("/api/backtest")
@login_required
def api_backtest():
    try:
        return jsonify(db_query("""
            SELECT * FROM backtest_results
            WHERE run_at = (SELECT MAX(run_at) FROM backtest_results)
            ORDER BY rating, hold_days
        """))
    except Exception:
        return jsonify([])


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  SignalIntel Web Dashboard")
    print("  Open: http://localhost:5000")
    print("="*50 + "\n")
    app.run(debug=True, host="0.0.0.0", port=5001)
