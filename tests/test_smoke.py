"""
Smoke tests — every page and key API endpoint must return HTTP 200.
Auth is pre-injected via the `client` fixture (session['user_id'] = 2, markn).
"""
import json
import pytest


PAGE_ROUTES = [
    "/",
    "/ratings",
    "/screener",
    "/penny/screener",
    "/penny",
    "/earnings",
    "/dividends",
    "/events",
    "/markets",
    "/watchlist",
    "/backtest",
    "/about",
    "/contact",
    "/privacy",
    "/terms",
    "/disclaimer",
    "/ticker/AAPL",
    "/news/AAPL",
    "/industry/Technology",
]

API_ROUTES = [
    "/api/overview",
    "/api/signals",
    "/api/signal_summary",
    "/api/sectors",
    "/api/sector-performance",
    "/api/insider_signals",
    "/api/top_signals",
    "/api/theme-counts",
    "/api/market-sessions",
    "/api/backtest/stats",
    "/api/ticker-tape",
    "/api/screener",
    "/api/watchlist",
    "/api/dividends",
    "/api/earnings",
    "/api/run_log",
    "/api/ticker/AAPL",
    "/api/penny/stock-of-day",
    "/api/penny/hot",
    "/api/signals/STRONG_BUY",
    "/api/signals/sector/Technology",
    "/api/industry/Technology",
    "/api/economic-calendar",
    "/api/economic-calendar/high-impact-banner",
    "/api/markets/SPY",
]


@pytest.mark.parametrize("path", PAGE_ROUTES)
def test_page_returns_200(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"


@pytest.mark.parametrize("path", API_ROUTES)
def test_api_returns_200_and_json(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}"
    data = json.loads(resp.data)
    assert data is not None


def test_screener_theme_strong_buy_momentum(client):
    resp = client.get("/screener?theme=strong_buy_momentum")
    assert resp.status_code == 200

def test_screener_theme_buy_the_dip(client):
    resp = client.get("/screener?theme=buy_the_dip")
    assert resp.status_code == 200

def test_screener_theme_insider_buying_surge(client):
    resp = client.get("/screener?theme=insider_buying_surge")
    assert resp.status_code == 200

def test_screener_theme_legally_clean(client):
    resp = client.get("/screener?theme=legally_clean")
    assert resp.status_code == 200


def test_login_page_no_auth(flask_app):
    """Login page must be reachable without auth."""
    with flask_app.test_client() as c:
        resp = c.get("/login")
        assert resp.status_code == 200


def test_protected_page_redirects_without_auth(flask_app):
    """Dashboard must redirect unauthenticated users to /login."""
    with flask_app.test_client() as c:
        resp = c.get("/")
        assert resp.status_code in (302, 301)
        assert "/login" in resp.headers.get("Location", "")


def test_api_signals_rating_filter(client):
    resp = client.get("/api/signals/BUY")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


# ── New watchlist picker + penny polish tests ─────────────────────

def test_api_watchlists_all_tickers_returns_list(client):
    """
    /api/watchlists/all-tickers must return {tickers:[...]} for the screener.

    Catches: endpoint missing or returning wrong shape.
    Ignores: whether tickers is empty (no watchlist data in test DB).
    """
    resp = client.get("/api/watchlists/all-tickers")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "tickers" in data
    assert isinstance(data["tickers"], list)


def test_api_watchlists_with_ticker_param_has_contains_flag(client):
    """
    GET /api/watchlists?ticker=AAPL must annotate each watchlist with
    contains_ticker boolean so the picker can render checkmarks.

    Catches: missing contains_ticker field on watchlist objects.
    Ignores: whether the user actually has AAPL in a watchlist.
    """
    resp = client.get("/api/watchlists?ticker=AAPL")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "watchlists" in data
    # If user has watchlists, each must have contains_ticker
    for wl in data["watchlists"]:
        assert "contains_ticker" in wl, f"watchlist {wl.get('id')} missing contains_ticker"


def test_penny_screener_has_wl_column_header(client):
    """
    /penny/screener must contain a WL column header so each row has a
    watchlist button (Issue 2 parity with main screener).

    Catches: missing WL header — indicates the column was not added.
    Ignores: button styling, picker JS behaviour (not testable in smoke).
    """
    resp = client.get("/penny/screener")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'wl-picker-btn' in body or 'wl-btn' in body, \
        "penny_screener.html missing watchlist button markup"


def test_screener_has_wl_picker_btn_class(client):
    """
    /screener must use wl-picker-btn class so the shared picker attaches.

    Catches: reverting to old toggleWatchlist() call without picker class.
    Ignores: exact button text (+ vs ✓ depends on user's watchlist state).
    """
    resp = client.get("/screener")
    assert resp.status_code == 200
    assert b'wl-picker-btn' in resp.data


def test_ticker_page_has_wl_picker_btn(client):
    """
    /ticker/AAPL must expose the shared picker button, not the old
    wlBtnClick() custom function.

    Catches: using wlBtnClick() (old custom picker) instead of WlPicker.open().
    Ignores: WlPicker.open internals — those are JS unit-level concerns.
    """
    resp = client.get("/ticker/AAPL")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'WlPicker.open' in body, "ticker page not using shared WlPicker"
    assert 'wlBtnClick' not in body, "old custom wlBtnClick() still present"


def test_penny_page_market_cap_no_raw_float(client):
    """
    /penny must not render a raw float in the Mkt Cap field.
    The fmtMktCap helper should format as $1.22B etc.

    Catches: s.market_cap rendered without formatting (e.g. 1220000000.0).
    Ignores: legitimate short numbers like '$900K', formatted '$1.22B', and
    JavaScript source code that contains the number as part of an expression.

    P15: absence test — verifies the bad pattern is gone, not the good one.
    """
    resp = client.get("/penny")
    assert resp.status_code == 200
    body = resp.data.decode()
    # The raw float pattern would appear as e.g. >1220000000.0< in the HTML
    import re
    # Match a bare 10+-digit number followed by .0 inside an HTML context
    raw_float_in_html = re.search(r'>\s*\d{10,}\.0\s*<', body)
    assert raw_float_in_html is None, \
        f"Raw market cap float found in /penny HTML: {raw_float_in_html.group()}"


def test_wl_picker_partial_included_on_screener(client):
    """
    The shared _watchlist_picker.html partial (via _nav.html) must be present
    on every page. Spot-check: /screener must contain the picker dropdown div.

    Catches: partial not included, picker DOM element missing.
    Ignores: picker CSS specifics, JS function body details.
    """
    resp = client.get("/screener")
    assert b'wl-picker-drop' in resp.data


# ── BUG-001 + BUG-002 tests ──────────────────────────────────────────

def test_nav_tier_badge_present_on_page(client):
    """
    Every authenticated page must expose the tier badge element so users
    can see their subscription level in the nav bar. Spot-check: /screener.

    Catches: tier badge removed from _nav.html or class renamed.
    Ignores: specific tier value (test user is elite), badge styling.
    """
    resp = client.get("/screener")
    assert resp.status_code == 200
    assert b'nav-tier' in resp.data, "_nav.html missing .nav-tier badge element"


def test_nav_tier_badge_present_on_watchlist(client):
    """
    /watchlist must include the tier badge — catches regressions where the
    watchlist page accidentally uses a different base template.

    Catches: watchlist page missing tier badge in nav.
    Ignores: tier value, exact badge text.
    """
    resp = client.get("/watchlist")
    assert resp.status_code == 200
    assert b'nav-tier' in resp.data


def test_api_watchlists_create_tier_limit_returns_structured_error(client):
    """
    When a user at their watchlist limit POSTs to /api/watchlists, the server
    must return a machine-readable error object with error='tier_limit' and
    numeric limit/current fields — not a plain string.

    Catches: plain-string error response ('Watchlist limit reached...') that
             the picker cannot render as a CTA.
    Ignores: exact upgrade_to value, tier_name wording.
    """
    import json as _json
    from database.db import get_connection
    from config.constants import DATABASE_PATH as DB

    # Set tier='starter' (watchlist_limit=5) so the limit check in
    # /api/watchlists fires when we attempt to create a 6th watchlist.
    conn = get_connection(DB)
    conn.execute("UPDATE users SET tier='starter' WHERE id=2")
    conn.execute("DELETE FROM watchlists WHERE user_id=2")
    conn.execute("DELETE FROM watchlists_meta WHERE user_id=2")
    for i in range(5):
        conn.execute(
            "INSERT INTO watchlists_meta(user_id,name,sort_order) VALUES(2,?,?)",
            (f'WL-{i}', i))
    conn.commit()
    conn.close()

    try:
        resp = client.post("/api/watchlists",
                           data=_json.dumps({"name": "WL-overflow"}),
                           content_type="application/json")
        assert resp.status_code == 403
        data = _json.loads(resp.data)
        assert data.get("error") == "tier_limit", f"expected error='tier_limit', got {data}"
        assert "limit" in data and isinstance(data["limit"], int)
        assert "current" in data and isinstance(data["current"], int)
        assert "feature" in data
    finally:
        conn = get_connection(DB)
        conn.execute("DELETE FROM watchlists WHERE user_id=2")
        conn.execute("DELETE FROM watchlists_meta WHERE user_id=2")
        conn.execute("UPDATE users SET tier='elite' WHERE id=2")
        conn.commit()
        conn.close()


def test_no_literal_unauthorized_in_user_facing_html(client):
    """
    The string 'unauthorized' must never appear in any rendered page body —
    it is an internal API term and must not leak to users.

    Catches: login_required returning raw 'unauthorized' that gets
             re-rendered inside a page template.
    Ignores: JS source comments inside _watchlist_picker.html that
             are not visible to end-users (this test hits page routes,
             not API routes, so login_required redirects for pages).
    """
    for path in PAGE_ROUTES:
        resp = client.get(path)
        assert resp.status_code == 200
        assert b'unauthorized' not in resp.data.lower(), \
            f"{path} contains literal 'unauthorized' in HTML body"


# ── BUG-001-REOPENED: tier display reads stale snapshot, not live DB ──
#
# Mechanism: web/app.py:118-129 had a hardcoded auto-upgrade in current_user()
# that fired UPDATE users SET tier='elite' for username='markn' if tier=='free'
# on every request. The result was that any DB write of tier='free' would be
# silently overwritten on the very next page render, and the nav badge would
# always display 'ELITE' for that account regardless of DB state.
#
# These tests pin the contract: the rendered nav badge value must reflect what
# is in the DB at request time, not what current_user() prefers.

def _set_user_tier(user_id, tier):
    from database.db import get_connection
    from config.constants import DATABASE_PATH as DB
    conn = get_connection(DB)
    conn.execute("UPDATE users SET tier=? WHERE id=?", (tier, user_id))
    conn.commit()
    conn.close()


def _get_user_tier(user_id):
    from database.db import get_connection
    from config.constants import DATABASE_PATH as DB
    conn = get_connection(DB)
    row = conn.execute("SELECT tier FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def test_bug001_reopened_nav_renders_db_tier_not_hardcoded_elite(client):
    """
    BUG-001-REOPENED reproduction.

    With DB tier='free' for the logged-in user, the nav badge MUST render
    'FREE' — not 'ELITE' — and the DB row MUST still read 'free' after the
    request (no silent re-upgrade).

    Catches: any current_user() / before_request hook / template global that
             overrides tier away from the DB value (the original bug was a
             hardcoded auto-upgrade for username='markn').
    Ignores: badge styling, whitespace, surrounding markup.
    """
    original = _get_user_tier(2)
    try:
        _set_user_tier(2, 'free')
        resp = client.get("/screener")
        assert resp.status_code == 200
        body = resp.data.decode('utf-8')
        assert 'ELITE' not in body, (
            "Nav rendered 'ELITE' despite DB tier='free' — current_user() or "
            "another hook is overriding the live DB value"
        )
        assert 'FREE' in body, "Nav badge should render 'FREE' for tier='free'"
        # Confirm the request did not silently mutate the DB back to elite
        assert _get_user_tier(2) == 'free', (
            "DB tier was silently overwritten during the request — a hook is "
            "writing tier behind the user's back"
        )
    finally:
        _set_user_tier(2, original)


def test_bug001_p15_elite_string_absent_across_surfaces_when_tier_free(client):
    """
    P15 absence test for BUG-001-REOPENED.

    Across every authenticated page route, the literal string 'ELITE' MUST
    NOT appear in the rendered HTML when DB tier='free' for the logged-in
    user. This asserts the SILENCE — the badge should never fabricate a
    higher tier than the DB indicates, on any surface, regardless of which
    template renders the nav.

    Catches: a fix that patches one render path but leaves another
             (e.g. fixes /screener but a stale value still leaks via /watchlist
             or /backtest), or a future regression that re-introduces a
             tier override anywhere in the request lifecycle.
    Ignores: legitimate 'Elite' / 'elite' lowercase appearances in pricing
             copy or tier comparison docs (this test only forbids the
             uppercase badge form 'ELITE').
    """
    original = _get_user_tier(2)
    try:
        _set_user_tier(2, 'free')
        for path in PAGE_ROUTES:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} returned {resp.status_code}"
            body = resp.data.decode('utf-8')
            assert 'ELITE' not in body, (
                f"{path} rendered 'ELITE' badge despite DB tier='free' — "
                f"tier display is not reading live DB on this surface"
            )
        # And the DB must still read 'free' after touching every page
        assert _get_user_tier(2) == 'free', (
            "After rendering all PAGE_ROUTES, DB tier was silently mutated — "
            "some request handler is writing tier behind the user's back"
        )
    finally:
        _set_user_tier(2, original)


def test_api_session_expired_returns_structured_error(flask_app):
    """
    An unauthenticated call to an API route must return
    error='session_expired' (not 'unauthorized') with a login_url field.

    Catches: login_required returning old raw 'unauthorized' string.
    Ignores: exact HTTP status code beyond being 4xx.
    """
    import json as _json
    with flask_app.test_client() as c:
        resp = c.get("/api/signals")
        assert resp.status_code == 401
        data = _json.loads(resp.data)
        assert data.get("error") == "session_expired", \
            f"expected error='session_expired', got {data.get('error')!r}"
        assert "login_url" in data
