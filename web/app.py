import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.legal_risk_scraper import get_legal_risk, fetch_legal_risk, save_legal_risk
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
            # Return JSON 401 for API routes, redirect for page routes
            if request.path.startswith('/api/'):
                return jsonify({"error": "unauthorized"}), 401
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
    return render_template("index.html", user=user, top_signals=top,
                           total_tickers=stats["total_tickers"],
                           last_scored=stats["last_scored"])


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
    return render_template('ticker.html', ticker=ticker.upper(), legal_risk=legal_risk_data)



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
        WHERE DATE(ss.scored_at) = DATE((SELECT MAX(scored_at) FROM signal_scores))
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

    # Fair Value calculation (P/E vs sector average)
    sc = screener[0] if screener else None
    sc = dict(sc) if sc else {}
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

    # Check if in watchlist
    in_watchlist = False
    if user:
        wl = db_query("SELECT 1 FROM watchlists WHERE user_id=? AND ticker=?",
                      (user["id"], ticker))
        in_watchlist = len(wl) > 0

    return jsonify({
        "ticker":       ticker,
        "screener":     sc,
        "signal":       dict(signal[0]) if signal else {},
        "insiders":     insiders,
        "news":         news,
        "history":      history,
        "in_watchlist": in_watchlist,
        "fair_value":   {"estimated": fair_value, "discount_pct": fv_discount, "label": fv_label} if fair_value else None,
        "technical":    tech,
    })


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
    return render_template("backtest.html", user=user)

@app.route("/api/backtest/stats")
@login_required
def api_backtest_stats():
    from collections import defaultdict
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
    """)
    periods = cur.fetchall()

    stats = defaultdict(lambda: {'returns':[], 'wins':0, 'total':0, 'days':[]})
    for p in periods:
        r, rating = p['return_pct'], p['new_rating']
        if r is None: continue
        stats[rating]['returns'].append(r)
        stats[rating]['days'].append(p['days_held'] or 0)
        stats[rating]['total'] += 1
        if rating in ('STRONG_BUY','BUY') and r > 0: stats[rating]['wins'] += 1
        elif rating in ('STRONG_SELL','SELL') and r < 0: stats[rating]['wins'] += 1

    # Recent changes feed
    cur.execute("""
        SELECT ticker, old_rating, new_rating, price_at_change, change_date, composite_score
        FROM rating_changes
        WHERE old_rating IS NOT NULL
        ORDER BY change_date DESC, id DESC
        LIMIT 50
    """)
    recent = [dict(r) for r in cur.fetchall()]
    conn.close()

    result = []
    for rating in ['STRONG_BUY','BUY','STRONG_HOLD','HOLD','WEAK_HOLD','SELL','STRONG_SELL']:
        s = stats.get(rating)
        if not s or not s['returns']: continue
        avg = sum(s['returns']) / len(s['returns'])
        win_rate = (s['wins'] / s['total'] * 100) if s['total'] > 0 else 0
        avg_days = sum(s['days']) / len(s['days']) if s['days'] else 0
        result.append({
            'rating': rating,
            'avg_return': round(avg, 2),
            'win_rate': round(win_rate, 1),
            'samples': s['total'],
            'avg_days_held': round(avg_days, 1)
        })

    return jsonify({'stats': result, 'recent': recent})



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

if __name__ == '__main__':
    print("=" * 50)
    print("  SignalIntel Web Dashboard")
    print("  Open: http://localhost:5000")
    print("=" * 50)
    app.run(debug=True, host="0.0.0.0", port=5001)
