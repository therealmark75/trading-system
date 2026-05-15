import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.legal_risk_scraper import get_legal_risk, fetch_legal_risk, save_legal_risk
# web/app.py - Phase 5 full web dashboard
import sys, json, sqlite3, requests as http_requests
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import (Flask, jsonify, render_template, request,
                   redirect, url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.constants import DATABASE_PATH, MIN_PRICE_FOR_SIGNAL, SCORING_ENGINE_VERSION
from database.db import (
    get_connection, get_latest_screener, get_recent_insiders,
    get_cluster_signals, get_top_signals, get_signal_summary,
    get_ticker_sentiment, initialise_user_schema,
    create_user, get_user_by_username, get_user_by_email, get_user_by_id,
    get_watchlist, get_watchlists_meta, get_or_create_default_watchlist,
    create_watchlist, rename_watchlist, delete_watchlist,
    add_to_watchlist, remove_from_watchlist,
    toggle_watchlist_alerts,
    create_default_watchlist, is_default_watchlist,
    get_top_signals_of_day, generate_top_signals_of_day,
)
from config.tiers import can_create_watchlist, watchlist_limit, get_tier, next_tier
from config.settings import FLASK_SECRET_KEY

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = FLASK_SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Ensure user tables exist
initialise_user_schema(DATABASE_PATH)

# Ensure contact_submissions table exists
def _init_contact_table():
    conn = get_connection(DATABASE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contact_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
_init_contact_table()

def _init_penny_tables():
    conn = get_connection(DATABASE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS penny_stock_of_day (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            ticker TEXT NOT NULL,
            composite_score REAL,
            rating TEXT,
            selected_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
_init_penny_tables()


def _init_market_tables():
    conn = get_connection(DATABASE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_history (
            symbol  TEXT NOT NULL,
            date    TEXT NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.commit()
    conn.close()
_init_market_tables()

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

def _send_telegram(msg):
    try:
        http_requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=5,
        )
    except Exception:
        pass


# ── Auth helpers ──────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            # Return JSON 401 for API routes, redirect for page routes
            if request.path.startswith('/api/'):
                return jsonify({"error": "session_expired",
                                "message": "Your session has expired. Please log in again.",
                                "login_url": "/login"}), 401
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
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(DISTINCT ticker) as total_tickers,
               MAX(scored_at) as last_scored
        FROM signal_scores
    """)
    stats = dict(cur.fetchone())
    conn.close()
    wl_rows = db_query("SELECT DISTINCT ticker FROM watchlists WHERE user_id=?", (user["id"],)) if user else []
    watchlist_tickers = {r["ticker"] for r in wl_rows}
    return render_template("index.html", user=user, top_signals=top,
                           total_tickers=stats["total_tickers"],
                           last_scored=stats["last_scored"],
                           watchlist_tickers=watchlist_tickers)


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
        elif len(password) < 8:
            flash("Password must be at least 8 characters")
        elif get_user_by_username(DATABASE_PATH, username):
            flash("Username already taken")
        elif get_user_by_email(DATABASE_PATH, email):
            flash("An account with that email already exists")
        else:
            pw_hash = generate_password_hash(password, method='pbkdf2:sha256')
            user_id = create_user(DATABASE_PATH, username, email, pw_hash)
            # Every new user gets a default watchlist immediately on signup.
            create_default_watchlist(DATABASE_PATH, user_id)
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
    legal_risk_data = get_legal_risk(ticker.upper())
    if legal_risk_data is None:
        try:
            result = fetch_legal_risk(ticker.upper())
            save_legal_risk(ticker.upper(), result)
            legal_risk_data = result
        except:
            legal_risk_data = {
                'risk_level': 'NONE', 'risk_label': 'Unavailable',
                'risk_color': '#6b7280', 'penalty': 0, 'findings': [],
                'scraped_at': None,
            }
    from_page = request.args.get('from', '')
    user = current_user()
    page_user_watchlists = []
    page_ticker_wl_ids = []
    if user:
        page_user_watchlists = get_watchlists_meta(DATABASE_PATH, user["id"])
        wl_rows = db_query(
            "SELECT watchlist_id FROM watchlists WHERE user_id=? AND ticker=?",
            (user["id"], ticker.upper())
        )
        page_ticker_wl_ids = [r["watchlist_id"] for r in wl_rows]
    return render_template('ticker.html', ticker=ticker.upper(),
                           legal_risk=legal_risk_data, from_page=from_page,
                           user=user,
                           user_watchlists=page_user_watchlists,
                           ticker_watchlist_ids=page_ticker_wl_ids)



@app.route("/industry/<path:industry_name>")
@login_required
def industry_page(industry_name):
    return render_template("industry.html", industry=industry_name)

@app.route("/api/industry/<path:industry_name>")
@login_required  
def api_industry(industry_name):
    rows = db_query("""
        SELECT ss.ticker, ss.company, ss.price, ss.change_pct,
               ss.market_cap, ss.sector, ss.industry,
               sc.rating, sc.composite_score,
               sc.momentum_score, sc.quality_score,
               sc.insider_score, sc.reversion_score,
               ss.analyst_recom
        FROM screener_snapshots ss
        LEFT JOIN signal_scores sc ON ss.ticker = sc.ticker
            AND DATE(sc.scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        WHERE ss.industry = ?
        AND ss.scraped_at >= datetime('now', '-2 days')
        GROUP BY ss.ticker
        ORDER BY sc.composite_score DESC NULLS LAST
        LIMIT 200
    """, (industry_name,))
    return jsonify(rows)

@app.route("/signals")
def signals_redirect():
    """Redirect /signals?rating=X to /?rating=X for nav links from ratings page."""
    rating = request.args.get("rating", "")
    return redirect(f"/?rating={rating}" if rating else "/")

@app.route("/watchlist")
@login_required
def watchlist():
    user  = current_user()
    wls   = get_watchlists_meta(DATABASE_PATH, user["id"])
    active_id = request.args.get("wl", type=int)
    if not active_id and wls:
        active_id = wls[0]["id"]
    items = get_watchlist(DATABASE_PATH, user["id"], active_id) if active_id else []
    tier  = get_tier(user.get("tier", "free"))
    limit = tier["watchlist_limit"]
    return render_template("watchlist.html", user=user, items=items,
                           watchlists=wls, active_watchlist_id=active_id,
                           tier=tier, watchlist_limit=limit,
                           min_price_for_signal=MIN_PRICE_FOR_SIGNAL)


# ── Watchlist API ─────────────────────────────────

@app.route("/api/watchlists", methods=["GET"])
@login_required
def api_watchlists_list():
    user   = current_user()
    ticker = request.args.get("ticker", "").upper() or None
    wls    = get_watchlists_meta(DATABASE_PATH, user["id"])
    tier   = get_tier(user.get("tier", "free"))
    if ticker:
        # Annotate each watchlist with contains_ticker flag
        wl_ids_with_ticker = {
            r["watchlist_id"]
            for r in db_query(
                "SELECT DISTINCT watchlist_id FROM watchlists WHERE user_id=? AND ticker=?",
                (user["id"], ticker)
            )
        }
        for wl in wls:
            wl["contains_ticker"] = wl["id"] in wl_ids_with_ticker
    return jsonify({"watchlists": wls, "limit": tier["watchlist_limit"],
                    "count": len(wls), "tier_name": tier["display_name"]})


@app.route("/api/watchlists/membership")
@login_required
def api_watchlists_membership():
    """Return per-watchlist membership for a specific ticker. Used by the watchlist picker on open."""
    user   = current_user()
    ticker = request.args.get("ticker", "").upper()
    if not ticker:
        return jsonify({"error": "ticker param required"}), 400
    wls = get_watchlists_meta(DATABASE_PATH, user["id"])
    member_ids = {
        r["watchlist_id"]
        for r in db_query(
            "SELECT DISTINCT watchlist_id FROM watchlists WHERE user_id=? AND ticker=?",
            (user["id"], ticker)
        )
    }
    return jsonify({
        "ticker": ticker,
        "watchlists": [{"id": wl["id"], "name": wl["name"], "contains_ticker": wl["id"] in member_ids}
                       for wl in wls],
    })


@app.route("/api/watchlists/all-tickers")
@login_required
def api_watchlists_all_tickers():
    """Return flat list of all ticker symbols across all user watchlists."""
    user = current_user()
    rows = db_query(
        "SELECT DISTINCT ticker FROM watchlists WHERE user_id=?", (user["id"],)
    )
    return jsonify({"tickers": [r["ticker"] for r in rows]})


@app.route("/api/watchlists", methods=["POST"])
@login_required
def api_watchlists_create():
    user       = current_user()
    body       = request.json or {}
    name       = body.get("name", "").strip()
    add_ticker = body.get("add_ticker", "").strip().upper()
    if not name:
        return jsonify({"ok": False, "error": "Name required"}), 400
    wls = get_watchlists_meta(DATABASE_PATH, user["id"])
    tier_key = user.get("tier", "free")
    if not can_create_watchlist(tier_key, len(wls)):
        limit    = watchlist_limit(tier_key)
        tier_cfg = get_tier(tier_key)
        return jsonify({
            "ok":         False,
            "error":      "tier_limit",
            "feature":    "watchlists",
            "tier":       tier_key,
            "tier_name":  tier_cfg["display_name"],
            "limit":      limit,
            "current":    len(wls),
            "upgrade_to": next_tier(tier_key),
        }), 403
    try:
        result = create_watchlist(DATABASE_PATH, user["id"], name)
        if add_ticker:
            add_to_watchlist(DATABASE_PATH, user["id"], add_ticker, "", watchlist_id=result["id"])
        return jsonify({"ok": True, **result})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409


@app.route("/api/watchlists/<int:wl_id>", methods=["PATCH"])
@login_required
def api_watchlists_rename(wl_id):
    user     = current_user()
    new_name = (request.json or {}).get("name", "").strip()
    if not new_name:
        return jsonify({"ok": False, "error": "Name required"}), 400
    try:
        ok = rename_watchlist(DATABASE_PATH, user["id"], wl_id, new_name)
        if not ok:
            return jsonify({"ok": False, "error": "Not found"}), 404
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409


@app.route("/api/watchlists/<int:wl_id>", methods=["DELETE"])
@login_required
def api_watchlists_delete(wl_id):
    user = current_user()
    confirm = request.args.get("confirm") == "true"
    if not confirm:
        return jsonify({"ok": False, "error": "Pass ?confirm=true to delete"}), 400
    if is_default_watchlist(DATABASE_PATH, user["id"], wl_id):
        return jsonify({
            "ok": False,
            "error": "The default watchlist cannot be deleted. You can rename it instead.",
        }), 400
    wls = get_watchlists_meta(DATABASE_PATH, user["id"])
    if len(wls) <= 1:
        return jsonify({"ok": False, "error": "Cannot delete your only watchlist"}), 400
    ok = delete_watchlist(DATABASE_PATH, user["id"], wl_id)
    if not ok:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/watchlists/<int:wl_id>/toggle_alerts", methods=["POST"])
@login_required
def api_watchlists_toggle_alerts(wl_id):
    user   = current_user()
    result = toggle_watchlist_alerts(DATABASE_PATH, user["id"], wl_id)
    if result is None:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True, **result})


@app.route("/api/watchlists/<int:wl_id>/tickers", methods=["POST"])
@login_required
def api_watchlists_add_ticker(wl_id):
    user   = current_user()
    ticker = (request.json or {}).get("ticker", "").upper()
    notes  = (request.json or {}).get("notes", "")
    if not ticker:
        return jsonify({"ok": False, "error": "No ticker"}), 400
    ok = add_to_watchlist(DATABASE_PATH, user["id"], ticker, notes, watchlist_id=wl_id)
    return jsonify({"ok": ok, "ticker": ticker})


@app.route("/api/watchlists/<int:wl_id>/tickers/<ticker>", methods=["DELETE"])
@login_required
def api_watchlists_remove_ticker(wl_id, ticker):
    user = current_user()
    remove_from_watchlist(DATABASE_PATH, user["id"], ticker.upper(), watchlist_id=wl_id)
    return jsonify({"ok": True, "ticker": ticker.upper()})


@app.route("/api/watchlist/add", methods=["POST"])
@login_required
def api_watchlist_add():
    user        = current_user()
    ticker      = (request.json or {}).get("ticker", "").upper()
    notes       = (request.json or {}).get("notes", "")
    watchlist_id = (request.json or {}).get("watchlist_id")
    if not ticker:
        return jsonify({"ok": False, "error": "No ticker"})
    ok = add_to_watchlist(DATABASE_PATH, user["id"], ticker, notes,
                          watchlist_id=watchlist_id)
    return jsonify({"ok": ok, "ticker": ticker})


@app.route("/api/watchlist/remove", methods=["POST"])
@login_required
def api_watchlist_remove():
    user        = current_user()
    ticker      = (request.json or {}).get("ticker", "").upper()
    watchlist_id = (request.json or {}).get("watchlist_id")
    remove_from_watchlist(DATABASE_PATH, user["id"], ticker, watchlist_id=watchlist_id)
    return jsonify({"ok": True, "ticker": ticker})


@app.route("/api/watchlist")
@login_required
def api_watchlist():
    user        = current_user()
    watchlist_id = request.args.get("wl", type=int)
    items = get_watchlist(DATABASE_PATH, user["id"], watchlist_id)
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
        SELECT ss.ticker, ss.rating, MAX(ss.composite_score) as composite_score,
        ss.momentum_score, ss.quality_score, ss.insider_score,
        ss.reversion_score, ss.flags, MAX(ss.scored_at) as scored_at,
        sc.sector, sc.industry
FROM signal_scores ss
LEFT JOIN (
    SELECT ticker, sector, industry
    FROM screener_snapshots
    GROUP BY ticker
) sc ON ss.ticker = sc.ticker
        WHERE DATE(ss.scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        GROUP BY ss.ticker
        ORDER BY ss.composite_score DESC
    """)
    for r in rows:
        raw = (r.get("flags") or "").split("|")
        r["flag_list"] = [f.strip() for f in raw if f.strip()]
        r.pop("flags", None)
    return jsonify(rows)
@app.route("/api/signals/sector/<sector>")
@login_required
def api_signals_by_sector(sector):
    rows = db_query("""
        SELECT ss.ticker, ss.rating, MAX(ss.composite_score) as composite_score,
               ss.momentum_score, ss.quality_score, ss.insider_score,
               ss.reversion_score, ss.flags, MAX(ss.scored_at) as scored_at,
               sc.sector, sc.industry
        FROM signal_scores ss
        LEFT JOIN (
            SELECT ticker, sector, industry
            FROM screener_snapshots
            GROUP BY ticker
        ) sc ON ss.ticker = sc.ticker
        WHERE DATE(ss.scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        AND sc.sector = ?
        GROUP BY ss.ticker
        ORDER BY ss.composite_score DESC
    """, (sector,))
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
        ORDER BY sector ASC
    """)
    return jsonify(rows)


@app.route("/api/sector-performance")
@login_required
def api_sector_performance():
    """Latest sector relative strength ranking (all 11 sectors)."""
    rows = db_query("""
        SELECT sector, etf_symbol, return_7d, return_30d,
               rank_7d, sector_strength_score, date
        FROM sector_performance
        WHERE date = (SELECT MAX(date) FROM sector_performance)
        ORDER BY rank_7d ASC
    """)
    return jsonify([dict(r) for r in rows] if rows else [])


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


@app.route("/api/search")
@login_required
def api_search():
    q = request.args.get("q", "").strip().upper()
    if not q:
        return jsonify({"results": []})
    prefix = q + "%"
    substr = "%" + q + "%"
    rows = db_query("""
        SELECT ss.ticker, ss.company,
               sig.rating, sig.composite_score, sig.target_price, sig.target_upside
        FROM (
            SELECT ticker, company
            FROM screener_snapshots
            WHERE ticker LIKE ? OR UPPER(company) LIKE ?
            GROUP BY ticker
        ) ss
        LEFT JOIN (
            SELECT ticker, rating, composite_score, target_price, target_upside,
                   MAX(scored_at) as max_ts
            FROM signal_scores
            GROUP BY ticker
        ) sig ON ss.ticker = sig.ticker
        ORDER BY
            CASE WHEN ss.ticker LIKE ? THEN 0 ELSE 1 END,
            sig.composite_score DESC
        LIMIT 10
    """, (prefix, substr, prefix))
    return jsonify({"results": [dict(r) for r in rows]})


@app.route("/dividends")
@login_required
def dividends():
    user = current_user()
    try:
        from config.settings import FMP_API_KEY
        has_key = bool(FMP_API_KEY)
    except Exception:
        has_key = False
    sectors = db_query("""
        SELECT DISTINCT sector FROM screener_snapshots WHERE sector IS NOT NULL ORDER BY sector
    """)
    return render_template("dividends.html", user=user, has_fmp_key=has_key,
                           sectors=[r["sector"] for r in sectors])


@app.route("/api/dividends")
@login_required
def api_dividends():
    from scrapers.fmp_scraper import get_dividends, _ensure_tables
    _ensure_tables(DATABASE_PATH)
    user       = current_user()
    min_yield  = request.args.get("min_yield", type=float, default=0)
    sector     = request.args.get("sector", "")
    rating_f   = request.args.get("rating", "")
    aristocrat = request.args.get("aristocrat", "0") == "1"
    sort_col   = request.args.get("sort", "dividend_yield")
    sort_dir   = request.args.get("dir", "desc")

    allowed_sorts = {"dividend_yield", "annual_dividend", "payout_ratio",
                     "ex_dividend_date", "payment_date", "dividend_growth_5yr",
                     "consecutive_years", "composite_score", "ticker"}
    if sort_col not in allowed_sorts:
        sort_col = "dividend_yield"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    rows = get_dividends(DATABASE_PATH,
                         min_yield=min_yield,
                         sector=sector or None,
                         rating=rating_f or None,
                         aristocrat=aristocrat)

    # Sort in Python for flexibility
    def sort_key(r):
        v = r.get(sort_col)
        return (v is None, v if v is not None else 0)
    rows.sort(key=sort_key, reverse=(sort_dir == "desc"))

    # Mark watchlist
    wl = set()
    if user:
        wl_rows = db_query("SELECT ticker FROM watchlists WHERE user_id = ?", (user["id"],))
        wl = {r["ticker"] for r in wl_rows}
    for r in rows:
        r["in_watchlist"] = r.get("ticker") in wl

    return jsonify({"rows": rows, "total": len(rows)})


@app.route("/api/dividends/refresh", methods=["POST"])
@login_required
def api_dividends_refresh():
    data   = request.get_json() or {}
    tickers = data.get("tickers")   # optional list
    try:
        from scrapers.fmp_scraper import job_refresh_dividends
        n = job_refresh_dividends(DATABASE_PATH, tickers=tickers)
        return jsonify({"ok": True, "saved": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/earnings")
@login_required
def earnings():
    user = current_user()
    try:
        from config.settings import FMP_API_KEY
        has_key = bool(FMP_API_KEY)
    except Exception:
        has_key = False
    return render_template("earnings.html", user=user, has_fmp_key=has_key)


@app.route("/api/earnings")
@login_required
def api_earnings():
    from scrapers.fmp_scraper import get_earnings_calendar, _ensure_tables
    from datetime import datetime, timedelta
    _ensure_tables(DATABASE_PATH)
    view     = request.args.get("view", "week")   # week / next_week / month
    rating_f = request.args.get("rating", "")
    user     = current_user()

    today = datetime.now().date()
    if view == "week":
        from_d = today.strftime("%Y-%m-%d")
        to_d   = (today + timedelta(days=7)).strftime("%Y-%m-%d")
    elif view == "next_week":
        from_d = (today + timedelta(days=7)).strftime("%Y-%m-%d")
        to_d   = (today + timedelta(days=14)).strftime("%Y-%m-%d")
    else:
        from_d = today.strftime("%Y-%m-%d")
        to_d   = (today + timedelta(days=30)).strftime("%Y-%m-%d")

    rows = get_earnings_calendar(DATABASE_PATH, from_d, to_d)

    # Filter by rating
    if rating_f:
        rows = [r for r in rows if r.get("rating") == rating_f]

    # Mark watchlist tickers
    wl = set()
    if user:
        wl_rows = db_query("SELECT ticker FROM watchlists WHERE user_id = ?", (user["id"],))
        wl = {r["ticker"] for r in wl_rows}
    for r in rows:
        r["in_watchlist"] = r["ticker"] in wl

    return jsonify({"rows": rows, "total": len(rows)})


@app.route("/api/earnings/refresh", methods=["POST"])
@login_required
def api_earnings_refresh():
    try:
        from scrapers.fmp_scraper import job_refresh_earnings
        n = job_refresh_earnings(DATABASE_PATH, days_ahead=30)
        return jsonify({"ok": True, "saved": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/ratings")
@login_required
def ratings():
    user = current_user()
    distribution = db_query("""
        SELECT rating, COUNT(DISTINCT ticker) as count,
               ROUND(AVG(composite_score), 1) as avg_score,
               ROUND(MIN(composite_score), 1) as min_score,
               ROUND(MAX(composite_score), 1) as max_score
        FROM signal_scores
        WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        GROUP BY rating
        ORDER BY avg_score DESC
    """)
    last_run = db_query("SELECT MAX(scored_at) as ts FROM signal_scores")
    return render_template("ratings.html", user=user,
                           distribution=distribution,
                           last_run=last_run[0]["ts"] if last_run else None)


@app.route("/events")
@login_required
def events_page():
    user = current_user()
    return render_template("events.html", user=user)


@app.route("/api/economic-calendar")
@login_required
def api_economic_calendar():
    impact = request.args.get("impact", "")
    country = request.args.get("country", "")
    from_date = request.args.get("from", "")
    to_date = request.args.get("to", "")

    conditions = []
    params = []
    if impact:
        conditions.append("impact = ?")
        params.append(impact)
    if country:
        conditions.append("country = ?")
        params.append(country.upper())
    if from_date:
        conditions.append("event_date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("event_date <= ?")
        params.append(to_date)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = db_query(f"""
        SELECT event_date, event_name, impact, country, currency,
               estimate, actual, previous, unit
        FROM economic_calendar
        {where}
        ORDER BY event_date ASC, impact DESC
        LIMIT 500
    """, params)
    return jsonify(rows)


@app.route("/api/economic-calendar/high-impact-banner")
@login_required
def api_high_impact_banner():
    """Return high-impact US events within next 7 days for events page banner."""
    rows = db_query("""
        SELECT event_date, event_name
        FROM economic_calendar
        WHERE impact = 'High'
          AND event_date >= DATE('now')
          AND event_date <= DATE('now', '+7 days')
          AND country = 'US'
        ORDER BY event_date ASC
        LIMIT 20
    """)
    return jsonify(rows)


@app.route("/api/theme-counts")
@login_required
def api_theme_counts():
    """Return stock counts for all 7 discovery theme cards.
    Queries here are canonical — identical logic to /api/screener?theme=<id>.
    """
    from datetime import date as _date, timedelta as _td
    latest_ss_cte = """
        SELECT s.*
        FROM screener_snapshots s
        INNER JOIN (
            SELECT ticker, MAX(scraped_at) AS max_ts
            FROM screener_snapshots
            WHERE scraped_at >= datetime('now', '-2 days')
            GROUP BY ticker
        ) lts ON s.ticker = lts.ticker AND s.scraped_at = lts.max_ts
    """
    latest_sig_cte = """
        SELECT ticker, rating, composite_score, momentum_score, insider_score
        FROM signal_scores
        WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
    """

    conn = get_connection(DATABASE_PATH)
    cur  = conn.cursor()

    def q(sql, params=()):
        cur.execute(sql, params)
        row = cur.fetchone()
        return (row[0] or 0) if row else 0

    # strong_buy_momentum: STRONG_BUY, score≥70, momentum≥70, price≥5
    strong_buy_momentum = q(f"""
        SELECT COUNT(*) FROM ({latest_ss_cte}) ss
        JOIN ({latest_sig_cte}) sig ON ss.ticker = sig.ticker
        WHERE sig.rating = 'STRONG_BUY'
          AND sig.composite_score >= 70
          AND sig.momentum_score >= 70
          AND ss.price >= 5
    """)

    # dividend_powerhouses: yield≥3, good rating, joined with screener data
    dividend_powerhouses = q(f"""
        SELECT COUNT(DISTINCT ss.ticker) FROM ({latest_ss_cte}) ss
        JOIN ({latest_sig_cte}) sig ON ss.ticker = sig.ticker
        JOIN dividends dv ON ss.ticker = dv.ticker
        WHERE dv.dividend_yield >= 3
          AND sig.rating IN ('STRONG_BUY','BUY','STRONG_HOLD')
    """)

    # buy_the_dip: rsi≤35, STRONG_BUY/BUY/STRONG_HOLD
    buy_the_dip = q(f"""
        SELECT COUNT(*) FROM ({latest_ss_cte}) ss
        JOIN ({latest_sig_cte}) sig ON ss.ticker = sig.ticker
        WHERE ss.rsi_14 <= 35
          AND sig.rating IN ('STRONG_BUY','BUY','STRONG_HOLD')
    """)

    # earnings_this_week: upcoming earnings, must also be in screener data
    future_date = (_date.today() + _td(days=7)).isoformat()
    earnings_this_week = q(f"""
        SELECT COUNT(DISTINCT ec.ticker)
        FROM earnings_calendar ec
        JOIN ({latest_ss_cte}) ss ON ec.ticker = ss.ticker
        WHERE ec.earnings_date BETWEEN DATE('now') AND ?
    """, (future_date,))

    # legally_clean: risk_label None/Minor, good rating
    # LEFT JOIN so tickers with no legal_risk record are treated as clean
    legally_clean = q(f"""
        SELECT COUNT(DISTINCT ss.ticker) FROM ({latest_ss_cte}) ss
        JOIN ({latest_sig_cte}) sig ON ss.ticker = sig.ticker
        LEFT JOIN legal_risk lr ON ss.ticker = lr.ticker
        WHERE (lr.risk_label IS NULL OR lr.risk_label IN ('None','Minor'))
          AND sig.rating IN ('STRONG_BUY','BUY','STRONG_HOLD')
    """)

    # insider_buying_surge: insider_score≥70, good rating
    insider_buying_surge = q(f"""
        SELECT COUNT(*) FROM ({latest_sig_cte}) sig
        WHERE sig.insider_score >= 70
          AND sig.rating IN ('STRONG_BUY','BUY','STRONG_HOLD')
    """)

    # undervalued: 20%+ below 52w high, good rating
    undervalued = q(f"""
        SELECT COUNT(*) FROM ({latest_ss_cte}) ss
        JOIN ({latest_sig_cte}) sig ON ss.ticker = sig.ticker
        WHERE ss.high_52w_pct <= -20
          AND sig.rating IN ('STRONG_BUY','BUY','STRONG_HOLD')
    """)

    conn.close()
    return jsonify({
        "strong_buy_momentum":  strong_buy_momentum,
        "dividend_powerhouses": dividend_powerhouses,
        "buy_the_dip":          buy_the_dip,
        "earnings_this_week":   earnings_this_week,
        "legally_clean":        legally_clean,
        "insider_buying_surge": insider_buying_surge,
        "undervalued":          undervalued,
    })


@app.route("/api/economic-calendar/refresh", methods=["POST"])
@login_required
def api_economic_calendar_refresh():
    try:
        from scrapers.fmp_scraper import refresh_economic_calendar
        n = refresh_economic_calendar(DATABASE_PATH)
        return jsonify({"ok": True, "saved": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Markets ─────────────────────────────────────────────────────────────────

@app.route("/markets")
@login_required
def markets_page():
    from config.markets import MAJOR_INDICES, SP_SECTORS, CURRENCIES, CRYPTO_TOP_10
    return render_template(
        "markets.html",
        user=current_user(),
        indices=MAJOR_INDICES,
        sectors=SP_SECTORS,
        currencies=CURRENCIES,
        crypto=CRYPTO_TOP_10,
    )


@app.route("/markets/<path:symbol>")
@login_required
def market_chart_page(symbol):
    from config.markets import MAJOR_INDICES, SP_SECTORS, CURRENCIES, CRYPTO_TOP_10
    all_items = MAJOR_INDICES + SP_SECTORS + CURRENCIES + CRYPTO_TOP_10
    label = next((i["label"] for i in all_items if i.get("tv") == symbol or i.get("symbol") == symbol), symbol)
    return render_template("market_chart.html", user=current_user(), tv_symbol=symbol, label=label)


@app.route("/api/market-sessions")
@login_required
def api_market_sessions():
    from utils.market_sessions import get_all_sessions
    return jsonify(get_all_sessions())


@app.route("/api/markets/<path:symbol>")
@login_required
def api_market_symbol(symbol):
    rows = db_query(
        "SELECT date, close FROM market_history WHERE symbol = ? ORDER BY date ASC",
        (symbol,),
    )
    if not rows:
        return jsonify({"symbol": symbol, "data": [], "latest": None,
                        "prev_close": None, "change_pct": 0}), 200

    data = [{"time": r["date"], "value": r["close"]}
            for r in rows if r["close"] is not None]
    if not data:
        return jsonify({"symbol": symbol, "data": [], "latest": None,
                        "prev_close": None, "change_pct": 0}), 200

    latest    = data[-1]["value"]
    prev_close = data[-2]["value"] if len(data) >= 2 else latest
    change_pct = ((latest - prev_close) / prev_close * 100) if prev_close else 0

    return jsonify({
        "symbol":     symbol,
        "data":       data,
        "latest":     latest,
        "prev_close": prev_close,
        "change_pct": round(change_pct, 2),
    })


@app.route("/screener")
@login_required
def screener():
    user = current_user()
    sectors = db_query("""
        SELECT DISTINCT sector FROM screener_snapshots
        WHERE sector IS NOT NULL ORDER BY sector
    """)
    return render_template("screener.html", user=user,
                           sectors=[r["sector"] for r in sectors])


@app.route("/api/screener")
@login_required
def api_screener():
    """
    Filterable screener endpoint. All params are optional query strings:
    sector, rating (comma-sep), score_min, score_max,
    mcap (any/micro/small/mid/large), pe_min, pe_max,
    rsi_min, rsi_max, upside_min,
    exchange (comma-sep: NYSE, NASDAQ, AMEX, Other),
    momentum_score_min, insider_score_min, volume_min,
    high_52w_pct_max, dividend_yield_min, earnings_days, legally_clean,
    sort (column), dir (asc/desc), page, per_page
    """
    sector    = request.args.get("sector", "")
    ratings   = [r for r in request.args.get("rating", "").split(",") if r]
    score_min = request.args.get("score_min", type=float, default=0)
    score_max = request.args.get("score_max", type=float, default=100)
    mcap      = request.args.get("mcap", "any")
    pe_min    = request.args.get("pe_min", type=float)
    pe_max    = request.args.get("pe_max", type=float)
    rsi_min   = request.args.get("rsi_min", type=float)
    rsi_max   = request.args.get("rsi_max", type=float)
    upside_min = request.args.get("upside_min", type=float)
    short_min  = request.args.get("short_min", type=float)
    price_max  = request.args.get("price_max", type=float)
    price_min  = request.args.get("price_min", type=float)
    relvol_min = request.args.get("relvol_min", type=float)
    # Theme-specific params
    momentum_score_min  = request.args.get("momentum_score_min", type=float)
    insider_score_min   = request.args.get("insider_score_min", type=float)
    volume_min          = request.args.get("volume_min", type=float)
    high_52w_pct_max    = request.args.get("high_52w_pct_max", type=float)
    dividend_yield_min  = request.args.get("dividend_yield_min", type=float)
    earnings_days       = request.args.get("earnings_days", type=int)
    legally_clean_param = request.args.get("legally_clean", "").lower() in ("1", "true", "yes")
    exchanges  = [e for e in request.args.get("exchange", "").split(",") if e]
    sort_col  = request.args.get("sort", "composite_score")
    sort_dir  = request.args.get("dir", "desc").lower()
    page      = max(1, request.args.get("page", type=int, default=1))
    per_page  = min(200, request.args.get("per_page", type=int, default=50))

    allowed_sorts = {
        "ticker", "company", "sector", "market_cap", "composite_score",
        "target_price", "target_upside", "price", "change_pct", "volume",
        "pe_ratio", "rsi_14", "rating", "high_52w_pct", "low_52w_pct",
        "momentum_score", "quality_score", "insider_score",
        "short_interest_pct", "insider_transactions", "beta",
        "rel_volume", "avg_volume", "sector_strength_score", "exchange",
    }
    if sort_col not in allowed_sorts:
        sort_col = "composite_score"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    # Base FROM: one row per ticker (latest scraped_at in last 2 days).
    # This prevents market_cap ambiguity when a ticker has multiple scraped rows
    # with different (or NULL) market_cap values within the window.
    latest_ss = """
        SELECT s.*
        FROM screener_snapshots s
        INNER JOIN (
            SELECT ticker, MAX(scraped_at) AS max_ts
            FROM screener_snapshots
            WHERE scraped_at >= datetime('now', '-2 days')
            GROUP BY ticker
        ) lts ON s.ticker = lts.ticker AND s.scraped_at = lts.max_ts
    """

    where = ["1=1"]
    params = []

    if sector:
        where.append("ss.sector = ?")
        params.append(sector)
    if ratings:
        placeholders = ",".join("?" * len(ratings))
        where.append(f"sig.rating IN ({placeholders})")
        params.extend(ratings)
    if score_min > 0:
        where.append("sig.composite_score >= ?")
        params.append(score_min)
    if score_max < 100:
        where.append("sig.composite_score <= ?")
        params.append(score_max)
    if mcap == "micro":
        where.append("(ss.market_cap IS NULL OR ss.market_cap = '' OR CAST(ss.market_cap AS REAL) < 300000000)")
    elif mcap == "small":
        where.append("CAST(ss.market_cap AS REAL) >= 300000000 AND CAST(ss.market_cap AS REAL) < 2000000000")
    elif mcap == "mid":
        where.append("CAST(ss.market_cap AS REAL) >= 2000000000 AND CAST(ss.market_cap AS REAL) < 10000000000")
    elif mcap == "large":
        where.append("CAST(ss.market_cap AS REAL) >= 10000000000")
    if pe_min is not None:
        where.append("ss.pe_ratio >= ?")
        params.append(pe_min)
    if pe_max is not None:
        where.append("ss.pe_ratio <= ?")
        params.append(pe_max)
    if rsi_min is not None:
        where.append("ss.rsi_14 >= ?")
        params.append(rsi_min)
    if rsi_max is not None:
        where.append("ss.rsi_14 <= ?")
        params.append(rsi_max)
    if upside_min is not None:
        where.append("sig.target_upside >= ?")
        params.append(upside_min)
    if short_min is not None:
        where.append("ss.short_interest_pct >= ?")
        params.append(short_min)
    if price_max is not None:
        where.append("ss.price <= ?")
        params.append(price_max)
    if price_min is not None:
        where.append("ss.price >= ?")
        params.append(price_min)
    if relvol_min is not None:
        where.append("ss.rel_volume >= ?")
        params.append(relvol_min)
    if exchanges:
        placeholders = ",".join("?" * len(exchanges))
        where.append(f"COALESCE(tm.exchange, 'Other') IN ({placeholders})")
        params.extend(exchanges)
    # Theme-specific conditions
    if momentum_score_min is not None:
        where.append("sig.momentum_score >= ?")
        params.append(momentum_score_min)
    if insider_score_min is not None:
        where.append("sig.insider_score >= ?")
        params.append(insider_score_min)
    if volume_min is not None:
        where.append("ss.volume >= ?")
        params.append(volume_min)
    if high_52w_pct_max is not None:
        where.append("ss.high_52w_pct <= ?")
        params.append(high_52w_pct_max)

    # Optional JOINs (dividend, earnings, legal)
    extra_joins = []
    if dividend_yield_min is not None:
        extra_joins.append("JOIN dividends dv ON ss.ticker = dv.ticker")
        where.append("dv.dividend_yield >= ?")
        params.append(dividend_yield_min)
    if earnings_days is not None:
        from datetime import date as _date, timedelta as _td
        future = (_date.today() + _td(days=earnings_days)).isoformat()
        extra_joins.append("JOIN earnings_calendar ec ON ss.ticker = ec.ticker")
        where.append("ec.earnings_date BETWEEN DATE('now') AND ?")
        params.append(future)
    if legally_clean_param:
        extra_joins.append("LEFT JOIN legal_risk lr ON ss.ticker = lr.ticker")
        where.append("(lr.risk_label IS NULL OR lr.risk_label IN ('None','Minor'))")

    extra_joins_sql = "\n        ".join(extra_joins)
    where_sql = " AND ".join(where)

    # Map sort column to correct table prefix
    _ss_cols = {"ticker","company","sector","market_cap","price","change_pct","volume",
                "pe_ratio","rsi_14","high_52w_pct","low_52w_pct",
                "short_interest_pct","insider_transactions","beta",
                "eps_growth_this_yr","eps_growth_next_yr",
                "rel_volume","avg_volume"}
    _sig_cols = {"rating","composite_score","target_price","target_upside",
                 "momentum_score","quality_score","insider_score","reversion_score",
                 "sector_strength_score"}
    # Columns stored as TEXT but containing numeric values — must cast for correct sort order
    _numeric_text_cols = {"market_cap"}
    if sort_col in _ss_cols:
        if sort_col in _numeric_text_cols:
            order_sql = f"CAST(ss.{sort_col} AS REAL) {sort_dir.upper()} NULLS LAST"
        else:
            order_sql = f"ss.{sort_col} {sort_dir.upper()} NULLS LAST"
    elif sort_col in _sig_cols:
        order_sql = f"sig.{sort_col} {sort_dir.upper()} NULLS LAST"
    elif sort_col == "exchange":
        order_sql = f"tm.exchange {sort_dir.upper()} NULLS LAST"
    else:
        order_sql = f"sig.composite_score DESC NULLS LAST"
    offset    = (page - 1) * per_page

    sig_subq = """
        SELECT ticker, rating, composite_score, target_price, target_upside,
               momentum_score, quality_score, insider_score, reversion_score,
               sector_strength_score,
               MAX(scored_at) as scored_at
        FROM signal_scores
        WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        GROUP BY ticker
    """

    count_rows = db_query(f"""
        SELECT COUNT(*) as total
        FROM ({latest_ss}) ss
        LEFT JOIN ({sig_subq}) sig ON ss.ticker = sig.ticker
        LEFT JOIN ticker_metadata tm ON ss.ticker = tm.ticker
        {extra_joins_sql}
        WHERE {where_sql}
    """, params)
    total = count_rows[0]["total"] if count_rows else 0

    rows = db_query(f"""
        SELECT ss.ticker, ss.company, ss.sector,
               ss.market_cap, ss.price, ss.change_pct, ss.volume,
               ss.pe_ratio, ss.rsi_14,
               ss.high_52w_pct, ss.low_52w_pct,
               ss.eps_growth_this_yr, ss.eps_growth_next_yr,
               ss.short_interest_pct, ss.insider_transactions, ss.beta,
               ss.rel_volume, ss.avg_volume, tm.exchange,
               sig.rating, sig.composite_score,
               sig.momentum_score, sig.quality_score, sig.insider_score,
               sig.reversion_score, sig.target_price, sig.target_upside,
               sig.sector_strength_score
        FROM ({latest_ss}) ss
        LEFT JOIN ({sig_subq}) sig ON ss.ticker = sig.ticker
        LEFT JOIN ticker_metadata tm ON ss.ticker = tm.ticker
        {extra_joins_sql}
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])

    target_count = sum(1 for r in rows if r.get("target_price") is not None)
    target_banner = None
    if rows and target_count < len(rows) * 0.5:
        target_banner = "Target prices are being recalculated — check back shortly."

    return jsonify({
        "rows":          rows,
        "total":         total,
        "page":          page,
        "per_page":      per_page,
        "pages":         max(1, (total + per_page - 1) // per_page),
        "target_banner": target_banner,
    })


@app.route("/api/ticker/<ticker>")
@login_required
def api_ticker(ticker):
    ticker = ticker.upper()
    user   = current_user()

    screener = db_query("""
        SELECT * FROM screener_snapshots WHERE ticker = ?
        ORDER BY scraped_at DESC LIMIT 1
    """, (ticker,))

    metadata_row = db_query(
        "SELECT * FROM ticker_metadata WHERE ticker = ?", (ticker,)
    )

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

    # Fair Value calculation (P/E vs sector average)
    sc = screener[0] if screener else None
    sc = dict(sc) if sc else {}
    tm = dict(metadata_row[0]) if metadata_row else {}
    fair_value = None
    fv_discount = None
    fv_label = None
    if sc.get('pe_ratio') and sc.get('sector') and sc.get('price'):
        sector_pe = db_query("""
            SELECT ROUND(AVG(pe_ratio),1) as avg_pe, ROUND(AVG(roe),1) as avg_roe
            FROM screener_snapshots
            WHERE sector = ? AND pe_ratio > 0 AND pe_ratio < 200
        """, (sc['sector'],))
        if sector_pe and sector_pe[0]['avg_pe']:
            avg_pe = sector_pe[0]['avg_pe']
            avg_roe = sector_pe[0]['avg_roe'] or 15
            stock_pe = sc['pe_ratio']
            price = sc['price']
            # Simple fair value: if P/E below sector avg, stock is undervalued
            fair_value = round(price * (avg_pe / stock_pe), 2)
            fv_discount = round(((fair_value - price) / price) * 100, 1)
            if fv_discount > 15:
                fv_label = 'UNDERVALUED'
            elif fv_discount < -15:
                fv_label = 'OVERVALUED'
            else:
                fv_label = 'FAIR VALUE'

    # Technical summary
    tech = {'buy': 0, 'neutral': 0, 'sell': 0, 'signals': []}
    if sc:
        rsi = sc.get('rsi_14')
        sma50 = sc.get('sma_50_pct')
        sma200 = sc.get('sma_200_pct')
        high52 = sc.get('high_52w_pct')
        low52 = sc.get('low_52w_pct')

        if rsi:
            if rsi < 35:
                tech['buy'] += 1; tech['signals'].append({'name':'RSI(14)','value':round(rsi,1),'signal':'BUY'})
            elif rsi > 65:
                tech['sell'] += 1; tech['signals'].append({'name':'RSI(14)','value':round(rsi,1),'signal':'SELL'})
            else:
                tech['neutral'] += 1; tech['signals'].append({'name':'RSI(14)','value':round(rsi,1),'signal':'NEUTRAL'})

        if sma50 is not None:
            if sma50 > 0:
                tech['buy'] += 1; tech['signals'].append({'name':'SMA 50','value':round(sma50,1),'signal':'BUY'})
            else:
                tech['sell'] += 1; tech['signals'].append({'name':'SMA 50','value':round(sma50,1),'signal':'SELL'})

        if sma200 is not None:
            if sma200 > 0:
                tech['buy'] += 1; tech['signals'].append({'name':'SMA 200','value':round(sma200,1),'signal':'BUY'})
            else:
                tech['sell'] += 1; tech['signals'].append({'name':'SMA 200','value':round(sma200,1),'signal':'SELL'})

        if high52 is not None:
            if high52 > -10:
                tech['buy'] += 1; tech['signals'].append({'name':'52W High','value':round(high52,1),'signal':'BUY'})
            elif high52 < -30:
                tech['sell'] += 1; tech['signals'].append({'name':'52W High','value':round(high52,1),'signal':'SELL'})
            else:
                tech['neutral'] += 1; tech['signals'].append({'name':'52W High','value':round(high52,1),'signal':'NEUTRAL'})

        if low52 is not None:
            if low52 > 50:
                tech['buy'] += 1; tech['signals'].append({'name':'52W Low','value':round(low52,1),'signal':'BUY'})
            else:
                tech['neutral'] += 1; tech['signals'].append({'name':'52W Low','value':round(low52,1),'signal':'NEUTRAL'})

        total = tech['buy'] + tech['neutral'] + tech['sell']
        if total > 0:
            if tech['buy'] > tech['sell'] + tech['neutral']:
                tech['overall'] = 'BUY'
            elif tech['sell'] > tech['buy'] + tech['neutral']:
                tech['overall'] = 'SELL'
            else:
                tech['overall'] = 'NEUTRAL'
        else:
            tech['overall'] = 'NEUTRAL'

    # Check watchlist membership
    in_watchlist = False
    user_watchlists = []
    ticker_watchlist_ids = set()
    if user:
        wl_rows = db_query(
            "SELECT watchlist_id FROM watchlists WHERE user_id=? AND ticker=?",
            (user["id"], ticker)
        )
        ticker_watchlist_ids = {r["watchlist_id"] for r in wl_rows}
        in_watchlist = bool(ticker_watchlist_ids)
        user_watchlists = get_watchlists_meta(DATABASE_PATH, user["id"])

    legal = get_legal_risk(ticker)
    if legal is None:
        legal = {"risk_level": "NONE", "risk_label": "No data", "risk_color": "#6b7280", "penalty": 0}

    analyst_ts = db_query(
        "SELECT MAX(scraped_at) as ts FROM screener_snapshots WHERE ticker = ? AND analyst_recom IS NOT NULL",
        (ticker,)
    )
    analyst_updated_at = analyst_ts[0]['ts'] if analyst_ts and analyst_ts[0]['ts'] else None

    next_earnings_date = None
    try:
        from scrapers.fmp_scraper import _ensure_tables
        _ensure_tables(DATABASE_PATH)
        ec = db_query(
            "SELECT earnings_date FROM earnings_calendar WHERE ticker = ? AND earnings_date >= DATE('now') ORDER BY earnings_date ASC LIMIT 1",
            (ticker,)
        )
        if ec:
            next_earnings_date = ec[0]['earnings_date']
    except Exception:
        pass

    # Sector performance ranking for bar chart on ticker page
    sector_perf = db_query("""
        SELECT sector, etf_symbol, return_7d, return_30d, rank_7d, sector_strength_score
        FROM sector_performance
        WHERE date = (SELECT MAX(date) FROM sector_performance)
        ORDER BY rank_7d ASC
    """)
    sector_perf_list = [dict(r) for r in sector_perf] if sector_perf else []

    return jsonify({
        "ticker":               ticker,
        "screener":             sc,
        "metadata":             tm,
        "signal":               dict(signal[0]) if signal else {},
        "insiders":             insiders,
        "news":                 news,
        "history":              history,
        "in_watchlist":         in_watchlist,
        "user_watchlists":      user_watchlists,
        "ticker_watchlist_ids": list(ticker_watchlist_ids),
        "fair_value":           {"estimated": fair_value, "discount_pct": fv_discount, "label": fv_label} if fair_value else None,
        "technical":            tech,
        "legal_risk":           legal,
        "analyst_updated_at":   analyst_updated_at,
        "next_earnings_date":   next_earnings_date,
        "sector_performance":   sector_perf_list,
    })


@app.route("/api/ticker/<ticker>/events")
@login_required
def api_ticker_events(ticker):
    ticker = ticker.upper()
    events = []

    # Rating changes (last 15)
    rc = db_query("""
        SELECT change_date as date, old_rating, new_rating, price_at_change, composite_score
        FROM rating_changes WHERE ticker = ?
        ORDER BY change_date DESC LIMIT 15
    """, (ticker,))
    for r in rc:
        up_tiers = {'STRONG_BUY','BUY','STRONG_HOLD'}
        down_tiers = {'SELL','STRONG_SELL','WEAK_HOLD'}
        direction = 'up' if r['new_rating'] in up_tiers else 'down' if r['new_rating'] in down_tiers else 'neutral'
        events.append({
            'type': 'rating',
            'date': r['date'],
            'title': f"Rating changed: {(r['old_rating'] or '?').replace('_',' ')} → {(r['new_rating'] or '?').replace('_',' ')}",
            'detail': f"Score {r['composite_score']:.1f} · Price ${r['price_at_change']:.2f}" if r['composite_score'] and r['price_at_change'] else None,
            'direction': direction,
            'new_rating': r['new_rating'],
        })

    # Insider trades (last 10)
    it = db_query("""
        SELECT transaction_date as date, insider_name, insider_title, transaction_type, shares, price, value
        FROM insider_trades WHERE ticker = ?
        ORDER BY transaction_date DESC LIMIT 10
    """, (ticker,))
    for r in it:
        is_buy = (r['transaction_type'] or '').upper() in ('BUY','P - PURCHASE','PURCHASE')
        val_str = f"${r['value']:,.0f}" if r['value'] else ''
        sh_str  = f"{int(r['shares']):,} shares" if r['shares'] else ''
        events.append({
            'type': 'insider',
            'date': r['date'],
            'title': f"Insider {'Buy' if is_buy else 'Sell'}: {r['insider_name'] or 'Unknown'} ({r['insider_title'] or ''})",
            'detail': ' · '.join(filter(None, [sh_str, val_str])),
            'direction': 'up' if is_buy else 'down',
            'new_rating': None,
        })

    # Legal risk entry
    lr = db_query("""
        SELECT scraped_at as date, risk_level, risk_label, filing_type
        FROM legal_risk WHERE ticker = ?
        ORDER BY scraped_at DESC LIMIT 1
    """, (ticker,))
    if lr:
        r = lr[0]
        direction = 'down' if r['risk_level'] not in ('NONE','MINOR') else 'neutral'
        events.append({
            'type': 'legal',
            'date': (r['date'] or '')[:10],
            'title': f"Legal risk assessed: {r['risk_label'] or r['risk_level']}",
            'detail': f"Filing: {r['filing_type']}" if r['filing_type'] else None,
            'direction': direction,
            'new_rating': None,
        })

    # Upcoming earnings
    ec = db_query("""
        SELECT earnings_date as date, timing, eps_estimate, eps_last_year
        FROM earnings_calendar WHERE ticker = ? AND earnings_date >= DATE('now')
        ORDER BY earnings_date ASC LIMIT 1
    """, (ticker,))
    for r in ec:
        est = f"EPS est. ${r['eps_estimate']:.2f}" if r['eps_estimate'] else None
        events.append({
            'type': 'earnings',
            'date': r['date'],
            'title': f"Earnings report ({r['timing'] or 'TBD'})",
            'detail': est,
            'direction': 'neutral',
            'new_rating': None,
        })

    # Sort all events by date desc, take last 10
    events.sort(key=lambda e: e['date'] or '', reverse=True)
    return jsonify({'events': events[:10]})


@app.route("/api/run_log")
@login_required
def api_run_log():
    return jsonify(db_query(
        "SELECT * FROM run_log ORDER BY run_at DESC LIMIT 50"
    ))



@app.route("/api/portfolios")
@login_required
def api_portfolios():
    user = current_user()
    rows = db_query("SELECT * FROM portfolios WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC", (user["id"],))
    return jsonify(rows)

@app.route("/api/portfolios/create", methods=["POST"])
@login_required
def api_create_portfolio():
    user = current_user()
    data = request.get_json()
    count = db_query("SELECT COUNT(*) as c FROM portfolios WHERE user_id = ? AND is_active = 1", (user["id"],))
    if count[0]["c"] >= 5:
        return jsonify({"error": "Maximum 5 portfolios allowed"}), 400
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Portfolio name required"}), 400
    balance = float(data.get("starting_balance", 10000))
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO portfolios (user_id, name, description, starting_balance, cash_balance, created_at) VALUES (?,?,?,?,?,?)",
        (user["id"], name, data.get("description",""), balance, balance, datetime.now().isoformat()))
    conn.commit()
    return jsonify({"id": cur.lastrowid, "message": "Portfolio created"})

@app.route("/api/portfolios/<int:portfolio_id>")
@login_required
def api_portfolio_detail(portfolio_id):
    user = current_user()
    port = db_query("SELECT * FROM portfolios WHERE id = ? AND user_id = ?", (portfolio_id, user["id"]))
    if not port:
        return jsonify({"error": "Not found"}), 404
    port = port[0]
    holdings = db_query("""
        SELECT h.*, s.price as current_price, sig.rating, sig.composite_score
        FROM portfolio_holdings h
        LEFT JOIN (SELECT ticker, price FROM screener_snapshots GROUP BY ticker) s ON h.ticker = s.ticker
        LEFT JOIN (SELECT ticker, rating, composite_score FROM signal_scores 
                   WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
                   GROUP BY ticker) sig ON h.ticker = sig.ticker
        WHERE h.portfolio_id = ?
    """, (portfolio_id,))
    transactions = db_query("SELECT * FROM portfolio_transactions WHERE portfolio_id = ? ORDER BY executed_at DESC LIMIT 50", (portfolio_id,))
    
    # Calculate P&L for each holding
    total_value = port["cash_balance"]
    for h in holdings:
        cp = h.get("current_price") or h["avg_buy_price"]
        if h["direction"] == "LONG":
            pnl = (cp - h["avg_buy_price"]) * h["shares"] * h["leverage"]
        else:
            pnl = (h["avg_buy_price"] - cp) * h["shares"] * h["leverage"]
        h["pnl"] = round(pnl, 2)
        h["pnl_pct"] = round((pnl / (h["avg_buy_price"] * h["shares"])) * 100, 2)
        h["current_price"] = cp
        position_value = cp * h["shares"]
        total_value += position_value
    
    port["total_value"] = round(total_value, 2)
    port["total_return"] = round(total_value - port["starting_balance"], 2)
    port["total_return_pct"] = round(((total_value - port["starting_balance"]) / port["starting_balance"]) * 100, 2)
    
    return jsonify({"portfolio": port, "holdings": holdings, "transactions": transactions})

@app.route("/api/portfolios/<int:portfolio_id>/trade", methods=["POST"])
@login_required
def api_trade(portfolio_id):
    user = current_user()
    port = db_query("SELECT * FROM portfolios WHERE id = ? AND user_id = ?", (portfolio_id, user["id"]))
    if not port:
        return jsonify({"error": "Not found"}), 404
    port = port[0]
    data = request.get_json()
    
    ticker   = data.get("ticker","").upper()
    action   = data.get("action","").upper()  # BUY, SELL, SHORT, COVER
    shares   = float(data.get("shares", 0))
    leverage = int(data.get("leverage", 1))
    direction = "SHORT" if action in ["SHORT","COVER"] else "LONG"
    
    # Get current price
    price_row = db_query("SELECT price FROM screener_snapshots WHERE ticker = ? ORDER BY scraped_at DESC LIMIT 1", (ticker,))
    if not price_row:
        return jsonify({"error": "Ticker not found"}), 404
    price = float(price_row[0]["price"])
    total = price * shares
    margin_required = total / leverage

    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()

    if action in ["BUY", "SHORT"]:
        if port["cash_balance"] < margin_required:
            return jsonify({"error": "Insufficient funds"}), 400
        # Check existing holding
        existing = db_query("SELECT * FROM portfolio_holdings WHERE portfolio_id = ? AND ticker = ? AND direction = ?",
            (portfolio_id, ticker, direction))
        if existing:
            e = existing[0]
            new_shares = e["shares"] + shares
            new_avg = ((e["avg_buy_price"] * e["shares"]) + (price * shares)) / new_shares
            cur.execute("UPDATE portfolio_holdings SET shares=?, avg_buy_price=?, current_price=?, margin_used=margin_used+? WHERE id=?",
                (new_shares, new_avg, price, margin_required, e["id"]))
        else:
            cur.execute("INSERT INTO portfolio_holdings (portfolio_id, ticker, shares, avg_buy_price, current_price, direction, leverage, margin_used, opened_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (portfolio_id, ticker, shares, price, price, direction, leverage, margin_required, datetime.now().isoformat()))
        cur.execute("UPDATE portfolios SET cash_balance=cash_balance-? WHERE id=?", (margin_required, portfolio_id))

    elif action in ["SELL", "COVER"]:
        existing = db_query("SELECT * FROM portfolio_holdings WHERE portfolio_id = ? AND ticker = ? AND direction = ?",
            (portfolio_id, ticker, direction))
        if not existing:
            return jsonify({"error": "No position to close"}), 400
        e = existing[0]
        if shares > e["shares"]:
            return jsonify({"error": "Cannot sell more than you hold"}), 400
        if direction == "LONG":
            pnl = (price - e["avg_buy_price"]) * shares * e["leverage"]
        else:
            pnl = (e["avg_buy_price"] - price) * shares * e["leverage"]
        proceeds = (e["margin_used"] / e["shares"]) * shares + pnl
        if e["shares"] - shares < 0.0001:
            cur.execute("DELETE FROM portfolio_holdings WHERE id=?", (e["id"],))
        else:
            cur.execute("UPDATE portfolio_holdings SET shares=shares-?, margin_used=margin_used-? WHERE id=?",
                (shares, (e["margin_used"]/e["shares"])*shares, e["id"]))
        cur.execute("UPDATE portfolios SET cash_balance=cash_balance+? WHERE id=?", (proceeds, portfolio_id))

    # Log transaction
    cur.execute("INSERT INTO portfolio_transactions (portfolio_id, ticker, type, shares, price, total_value, leverage, direction, executed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (portfolio_id, ticker, action, shares, price, total, leverage, direction, datetime.now().isoformat()))
    
    conn.commit()
    return jsonify({"message": f"{action} executed", "price": price, "total": total})

@app.route("/api/portfolios/<int:portfolio_id>/check_margins", methods=["POST"])
@login_required  
def api_check_margins(portfolio_id):
    holdings = db_query("""
        SELECT h.*, s.price as current_price
        FROM portfolio_holdings h
        LEFT JOIN (SELECT ticker, price FROM screener_snapshots GROUP BY ticker) s ON h.ticker = s.ticker
        WHERE h.portfolio_id = ?
    """, (portfolio_id,))
    
    margin_calls = []
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    
    for h in holdings:
        cp = h.get("current_price") or h["avg_buy_price"]
        if h["direction"] == "LONG":
            pnl = (cp - h["avg_buy_price"]) * h["shares"] * h["leverage"]
        else:
            pnl = (h["avg_buy_price"] - cp) * h["shares"] * h["leverage"]
        margin_loss_pct = abs(pnl) / h["margin_used"] * 100 if h["margin_used"] > 0 else 0
        
        if pnl < 0 and margin_loss_pct >= 100:
            # BUST - auto liquidate
            cur.execute("DELETE FROM portfolio_holdings WHERE id=?", (h["id"],))
            cur.execute("UPDATE portfolios SET cash_balance=cash_balance+? WHERE id=?", 
                (max(0, h["margin_used"] + pnl), portfolio_id))
            cur.execute("INSERT INTO margin_calls (portfolio_id, holding_id, ticker, margin_level, status, issued_at, resolved_at) VALUES (?,?,?,?,?,?,?)",
                (portfolio_id, h["id"], h["ticker"], margin_loss_pct, "BUSTED", datetime.now().isoformat(), datetime.now().isoformat()))
            margin_calls.append({"ticker": h["ticker"], "status": "BUSTED"})
        elif pnl < 0 and margin_loss_pct >= 75:
            cur.execute("INSERT OR IGNORE INTO margin_calls (portfolio_id, holding_id, ticker, margin_level, status, issued_at) VALUES (?,?,?,?,?,?)",
                (portfolio_id, h["id"], h["ticker"], margin_loss_pct, "WARNING", datetime.now().isoformat()))
            margin_calls.append({"ticker": h["ticker"], "status": "WARNING", "margin_level": margin_loss_pct})
    
    conn.commit()
    # Check if portfolio is fully bust
    port = db_query("SELECT * FROM portfolios WHERE id=?", (portfolio_id,))
    if port and port[0]["cash_balance"] <= 0 and not holdings:
        cur.execute("UPDATE portfolios SET is_active=0 WHERE id=?", (portfolio_id,))
        conn.commit()
        return jsonify({"bust": True, "margin_calls": margin_calls})
    
    return jsonify({"bust": False, "margin_calls": margin_calls})


@app.route("/backtest")
@login_required
def backtest():
    user = current_user()
    try:
        conn = get_connection(DATABASE_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT scoring_version FROM rating_changes "
            "WHERE scoring_version IS NOT NULL ORDER BY scoring_version"
        )
        available_versions = [r[0] for r in cur.fetchall()]
        conn.close()
    except Exception:
        available_versions = []
    if not available_versions:
        available_versions = [SCORING_ENGINE_VERSION]
    return render_template(
        "backtest.html", user=user,
        scoring_version=SCORING_ENGINE_VERSION,
        available_versions=available_versions,
    )

@app.route("/api/backtest/stats")
@login_required
def api_backtest_stats():
    from collections import defaultdict
    try:
        return _api_backtest_stats_inner()
    except Exception as e:
        logger.error(f"[Backtest] stats endpoint error: {e}", exc_info=True)
        return jsonify({"stats": [], "recent": [], "sector_comparison": {"note": f"Data temporarily unavailable: {e}"},
                        "message": "Backtest data temporarily unavailable — check server logs."})

def _api_backtest_stats_inner():
    from collections import defaultdict
    version = request.args.get("version", SCORING_ENGINE_VERSION)
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            rc1.new_rating,
            rc1.ticker,
            rc1.price_at_change as entry_price,
            rc1.change_date as entry_date,
            rc2.price_at_change as exit_price,
            rc2.change_date as exit_date,
            ROUND(((rc2.price_at_change - rc1.price_at_change) / rc1.price_at_change) * 100, 2) as return_pct,
            CAST(julianday(rc2.change_date) - julianday(rc1.change_date) AS INTEGER) as days_held
        FROM rating_changes rc1
        JOIN rating_changes rc2
            ON rc1.ticker = rc2.ticker
            AND rc2.change_date > rc1.change_date
            AND NOT EXISTS (
                SELECT 1 FROM rating_changes rc3
                WHERE rc3.ticker = rc1.ticker
                AND rc3.change_date > rc1.change_date
                AND rc3.change_date < rc2.change_date
            )
        WHERE rc1.price_at_change IS NOT NULL
        AND rc2.price_at_change IS NOT NULL
        AND rc1.price_at_change > 0
        AND COALESCE(rc1.scoring_version, '0.9.0') = ?
    """, (version,))
    periods = cur.fetchall()

    stats = defaultdict(lambda: {'returns':[], 'wins':0, 'total':0, 'days':[], 'trades':[]})
    for p in periods:
        r, rating = p['return_pct'], p['new_rating']
        if r is None: continue
        stats[rating]['returns'].append(r)
        stats[rating]['days'].append(p['days_held'] or 0)
        stats[rating]['total'] += 1
        is_win = (r < 0) if rating in ('STRONG_SELL', 'SELL', 'WEAK_HOLD') else (r > 0)
        if is_win: stats[rating]['wins'] += 1
        stats[rating]['trades'].append({
            'ticker':      p['ticker'],
            'entry_date':  p['entry_date'],
            'entry_price': p['entry_price'],
            'exit_price':  p['exit_price'],
            'return_pct':  r,
            'days_held':   p['days_held'] or 0,
        })

    # Recent changes feed
    cur.execute("""
        SELECT ticker, old_rating, new_rating, price_at_change, change_date, composite_score
        FROM rating_changes
        WHERE old_rating IS NOT NULL
          AND COALESCE(scoring_version, '0.9.0') = ?
        ORDER BY change_date DESC, id DESC
        LIMIT 50
    """, (version,))
    recent = [dict(r) for r in cur.fetchall()]
    conn.close()

    result = []
    for rating in ['STRONG_BUY','BUY','STRONG_HOLD','HOLD','WEAK_HOLD','SELL','STRONG_SELL']:
        s = stats.get(rating)
        if not s or not s['returns']: continue
        avg = sum(s['returns']) / len(s['returns'])
        win_rate = (s['wins'] / s['total'] * 100) if s['total'] > 0 else 0
        avg_days = sum(s['days']) / len(s['days']) if s['days'] else 0
        all_trades = sorted(s['trades'], key=lambda x: x['return_pct'], reverse=True)
        result.append({
            'rating':       rating,
            'avg_return':   round(avg, 2),
            'win_rate':     round(win_rate, 1),
            'samples':      s['total'],
            'avg_days_held': round(avg_days, 1),
            'trades':       all_trades[:20],
        })

    # Sector comparison: top-3 vs bottom-3 sector Strong Buys
    # Uses current sector rankings as proxy (historical sector data accumulates over time)
    sector_comparison = {"top_avg": None, "bottom_avg": None, "spread": None,
                         "top_n": 0, "bottom_n": 0, "note": ""}
    try:
        # Get current sector rankings
        sp_rows = db_query("""
            SELECT sector, rank_7d FROM sector_performance
            WHERE date = (SELECT MAX(date) FROM sector_performance)
        """)
        top_sectors    = {r["sector"] for r in sp_rows if r["rank_7d"] and r["rank_7d"] <= 3}
        bottom_sectors = {r["sector"] for r in sp_rows if r["rank_7d"] and r["rank_7d"] >= 9}

        # Strong Buy periods with sector info
        sb_periods = db_query("""
            SELECT rc1.ticker,
                   ROUND(((rc2.price_at_change - rc1.price_at_change) / rc1.price_at_change) * 100, 2) as return_pct,
                   CAST(julianday(rc2.change_date) - julianday(rc1.change_date) AS INTEGER) as days_held,
                   (SELECT sector FROM screener_snapshots WHERE ticker = rc1.ticker ORDER BY scraped_at DESC LIMIT 1) as sector
            FROM rating_changes rc1
            JOIN rating_changes rc2
                ON rc1.ticker = rc2.ticker
                AND rc2.change_date > rc1.change_date
                AND NOT EXISTS (
                    SELECT 1 FROM rating_changes rc3
                    WHERE rc3.ticker = rc1.ticker
                    AND rc3.change_date > rc1.change_date
                    AND rc3.change_date < rc2.change_date
                )
            WHERE rc1.new_rating = 'STRONG_BUY'
              AND rc1.price_at_change IS NOT NULL AND rc1.price_at_change > 0
              AND rc2.price_at_change IS NOT NULL
              AND CAST(julianday(rc2.change_date) - julianday(rc1.change_date) AS INTEGER) >= 30
        """)

        top_returns    = [r["return_pct"] for r in sb_periods if r["sector"] in top_sectors and r["return_pct"] is not None]
        bottom_returns = [r["return_pct"] for r in sb_periods if r["sector"] in bottom_sectors and r["return_pct"] is not None]
        top_avg    = round(sum(top_returns) / len(top_returns), 2) if top_returns else None
        bottom_avg = round(sum(bottom_returns) / len(bottom_returns), 2) if bottom_returns else None
        spread     = round(top_avg - bottom_avg, 2) if top_avg is not None and bottom_avg is not None else None

        sector_comparison = {
            "top_avg": top_avg, "bottom_avg": bottom_avg, "spread": spread,
            "top_n": len(top_returns), "bottom_n": len(bottom_returns),
            "top_sectors": sorted(top_sectors), "bottom_sectors": sorted(bottom_sectors),
            "note": "Uses current sector rankings as proxy. Historical accuracy improves as daily sector data accumulates." if sp_rows else "No sector data yet.",
        }
    except Exception as e:
        sector_comparison["note"] = f"Sector comparison unavailable: {e}"

    return jsonify({
        'stats': result,
        'recent': recent,
        'sector_comparison': sector_comparison,
        'version': version,
        'current_version': SCORING_ENGINE_VERSION,
    })



@app.route('/news/<ticker>')
@login_required
def ticker_news(ticker):
    conn = get_connection(DATABASE_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT headline, url, source, published, sentiment
        FROM news_sentiment
        WHERE ticker = ?
        ORDER BY scraped_at DESC
        LIMIT 50
    """, (ticker,))
    articles = [dict(r) for r in cur.fetchall()]
    conn.close()
    return render_template('ticker_news.html', ticker=ticker, articles=articles)

# ── Penny & Small Cap ─────────────────────────────────

def _penny_why(stock):
    reasons = []
    ms  = stock.get("momentum_score") or 0
    qs  = stock.get("quality_score")  or 0
    ins = stock.get("insider_score")  or 0
    rev = stock.get("reversion_score") or 0
    rsi = stock.get("rsi_14")
    upside = stock.get("target_upside")
    cs  = stock.get("composite_score") or 0

    if ms >= 75:
        reasons.append(f"Strong price momentum (score {ms:.0f}/100) — trending above key moving averages with sustained upward pressure.")
    elif ms >= 55:
        reasons.append(f"Building momentum (score {ms:.0f}/100) — early signs of upward trend emerging.")
    if ins >= 70:
        reasons.append(f"Significant insider buying activity (score {ins:.0f}/100) — company insiders actively increasing their stake, the strongest conviction signal available.")
    if qs >= 65:
        reasons.append(f"Solid fundamentals for a penny stock (quality score {qs:.0f}/100) — above-average business metrics relative to its peer group.")
    if rsi and rsi < 33:
        reasons.append(f"Oversold RSI at {rsi:.0f} — potential mean reversion bounce back toward the mean.")
    if upside and upside > 25:
        reasons.append(f"Model target price implies {upside:.0f}% potential upside from current levels.")
    if not reasons:
        reasons.append(f"Highest composite signal score ({cs:.0f}/100) among penny stocks in today's scan — no single dominant driver but the strongest overall reading in this universe.")
    return reasons


def _select_penny_stock_of_day():
    today = datetime.utcnow().date().isoformat()
    conn = get_connection(DATABASE_PATH)
    cur  = conn.cursor()

    # Return today's pick if already computed
    cur.execute("SELECT ticker FROM penny_stock_of_day WHERE date = ?", (today,))
    row = cur.fetchone()
    if row:
        conn.close()
        return row[0]

    # Select best penny stock — prefer STRONG_BUY/BUY, then highest composite
    cur.execute("""
        SELECT ss.ticker, sig.composite_score, sig.rating
        FROM screener_snapshots ss
        JOIN signal_scores sig ON ss.ticker = sig.ticker
        WHERE ss.scraped_at = (SELECT MAX(scraped_at) FROM screener_snapshots)
          AND sig.scored_at = (SELECT MAX(scored_at) FROM signal_scores)
          AND ss.price > 0 AND ss.price < 5
          AND sig.composite_score IS NOT NULL
        ORDER BY
            CASE sig.rating
                WHEN 'STRONG_BUY' THEN 1
                WHEN 'BUY'        THEN 2
                WHEN 'STRONG_HOLD'THEN 3
                ELSE 4
            END ASC,
            sig.composite_score DESC
        LIMIT 1
    """)
    pick = cur.fetchone()
    if pick:
        cur.execute(
            "INSERT OR REPLACE INTO penny_stock_of_day (date, ticker, composite_score, rating) VALUES (?,?,?,?)",
            (today, pick[0], pick[1], pick[2])
        )
        conn.commit()
        ticker = pick[0]
    else:
        ticker = None
    conn.close()
    return ticker


@app.route("/penny")
@login_required
def penny():
    user = current_user()
    return render_template("penny.html", user=user)


@app.route("/penny/screener")
@login_required
def penny_screener():
    user = current_user()
    return render_template("penny_screener.html", user=user)


@app.route("/api/penny/stock-of-day")
@login_required
def api_penny_stock_of_day():
    ticker = _select_penny_stock_of_day()
    if not ticker:
        return jsonify({"stock": None})

    rows = db_query("""
        SELECT ss.ticker, ss.company, ss.sector, ss.industry,
               ss.price, ss.change_pct, ss.volume, ss.rsi_14,
               ss.high_52w_pct, ss.low_52w_pct, ss.rel_volume, ss.avg_volume,
               ss.market_cap, ss.beta,
               sig.rating, sig.composite_score,
               sig.momentum_score, sig.quality_score,
               sig.insider_score, sig.reversion_score,
               sig.target_price, sig.target_upside,
               lr.risk_label, lr.risk_color, lr.penalty
        FROM screener_snapshots ss
        JOIN signal_scores sig ON ss.ticker = sig.ticker
        LEFT JOIN legal_risk lr ON ss.ticker = lr.ticker
        WHERE ss.ticker = ?
          AND ss.scraped_at = (SELECT MAX(scraped_at) FROM screener_snapshots)
          AND sig.scored_at = (SELECT MAX(scored_at) FROM signal_scores)
        LIMIT 1
    """, (ticker,))

    if not rows:
        return jsonify({"stock": None})

    stock = rows[0]
    stock["why"] = _penny_why(stock)
    return jsonify({"stock": stock, "date": datetime.utcnow().date().isoformat()})


@app.route("/api/penny/hot")
@login_required
def api_penny_hot():
    # One row per ticker (latest snapshot + latest signal score)
    lts_sq = """
        SELECT ticker, MAX(scraped_at) AS max_ts
        FROM screener_snapshots
        WHERE scraped_at >= datetime('now', '-2 days')
        GROUP BY ticker
    """
    sig_sq = """
        SELECT ticker, rating, composite_score, MAX(scored_at) AS scored_at
        FROM signal_scores
        WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
        GROUP BY ticker
    """

    exchanges = ["NASDAQ", "NYSE", "OTC"]
    result = {}
    for exch in exchanges:
        rows = db_query(f"""
            SELECT ss.ticker, ss.company, ss.price, ss.change_pct, ss.volume,
                   sig.rating, sig.composite_score
            FROM screener_snapshots ss
            INNER JOIN ({lts_sq}) lts ON ss.ticker = lts.ticker AND ss.scraped_at = lts.max_ts
            LEFT JOIN ({sig_sq}) sig ON ss.ticker = sig.ticker
            INNER JOIN ticker_metadata tm ON ss.ticker = tm.ticker
            WHERE ss.price > 0 AND ss.price < 5
              AND tm.exchange = ?
              AND ss.change_pct IS NOT NULL
            ORDER BY ABS(ss.change_pct) DESC
            LIMIT 5
        """, (exch,))
        result[exch] = rows

    # Fallback: if no exchange data, return top movers regardless of exchange
    has_data = any(result[e] for e in exchanges)
    if not has_data:
        top = db_query(f"""
            SELECT ss.ticker, ss.company, ss.price, ss.change_pct, ss.volume,
                   sig.rating, sig.composite_score
            FROM screener_snapshots ss
            INNER JOIN ({lts_sq}) lts ON ss.ticker = lts.ticker AND ss.scraped_at = lts.max_ts
            LEFT JOIN ({sig_sq}) sig ON ss.ticker = sig.ticker
            WHERE ss.price > 0 AND ss.price < 5
              AND ss.change_pct IS NOT NULL
            ORDER BY ABS(ss.change_pct) DESC
            LIMIT 15
        """)
        return jsonify({"no_exchange": True, "movers": top})

    return jsonify(result)


# ── Ticker tape ───────────────────────────────────────

@app.route("/api/ticker-tape")
@login_required
def api_ticker_tape():
    """Top ~40 movers from today's screener for the scrolling tape."""
    conn = get_connection(DATABASE_PATH)
    cur  = conn.cursor()
    cur.execute("""
        SELECT ss.ticker, ss.price, ss.change_pct, sig.rating
        FROM screener_snapshots ss
        INNER JOIN (
            SELECT ticker, MAX(scraped_at) AS max_ts
            FROM screener_snapshots
            WHERE scraped_at >= datetime('now', '-2 days')
            GROUP BY ticker
        ) lts ON ss.ticker = lts.ticker AND ss.scraped_at = lts.max_ts
        LEFT JOIN (
            SELECT ticker, rating, MAX(scored_at) AS scored_at
            FROM signal_scores
            WHERE DATE(scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
            GROUP BY ticker
        ) sig ON ss.ticker = sig.ticker
        WHERE ss.price IS NOT NULL AND ss.change_pct IS NOT NULL
          AND ABS(ss.change_pct) > 0
        ORDER BY ABS(ss.change_pct) DESC
        LIMIT 40
    """)
    rows = cur.fetchall()
    conn.close()
    return jsonify([
        {"ticker": r[0], "price": r[1], "change_pct": r[2], "rating": r[3]}
        for r in rows
    ])


# ── Static / public pages ─────────────────────────────

@app.route("/about")
def about():
    return render_template("about.html", user=current_user())

@app.route("/contact", methods=["GET", "POST"])
def contact():
    success = False
    if request.method == "POST":
        name    = request.form.get("name", "").strip()
        email   = request.form.get("email", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if name and email and subject and message:
            conn = get_connection(DATABASE_PATH)
            conn.execute(
                "INSERT INTO contact_submissions (name, email, subject, message) VALUES (?,?,?,?)",
                (name, email, subject, message)
            )
            conn.commit()
            conn.close()
            _send_telegram(f"📧 New contact form submission from {name}: {subject}")
            success = True
    return render_template("contact.html", user=current_user(), success=success)

@app.route("/privacy")
def privacy():
    return render_template("privacy.html", user=current_user())

@app.route("/terms")
def terms():
    return render_template("terms.html", user=current_user())

@app.route("/disclaimer")
def disclaimer():
    return render_template("disclaimer.html", user=current_user())


if __name__ == '__main__':
    print("=" * 50)
    print("  SignalIntel Web Dashboard")
    print("  Open: http://localhost:5001")
    print("=" * 50)
    app.run(debug=True, host="0.0.0.0", port=5001)
