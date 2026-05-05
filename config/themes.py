"""
Canonical Discovery Theme definitions.
Single source of truth used by /api/theme-counts and /api/screener?theme=<id>.
"""

THEMES = [
    {
        "id": "strong_buy_momentum",
        "label": "Strong Buy Momentum",
        "emoji": "🚀",
        "color": "#00ff88",
        "description": "Highest conviction signals with strong composite scores",
        "ratings": ["STRONG_BUY"],
        "score_min": 70,
        "momentum_score_min": 70,
        "price_min": 5,
    },
    {
        "id": "dividend_powerhouses",
        "label": "Dividend Powerhouses",
        "emoji": "💰",
        "color": "#facc15",
        "description": "High-yield stocks with consistent dividend growth",
        "ratings": ["STRONG_BUY", "BUY", "STRONG_HOLD"],
        "dividend_yield_min": 3,
    },
    {
        "id": "buy_the_dip",
        "label": "Buy the Dip",
        "emoji": "📉",
        "color": "#00d4ff",
        "description": "RSI oversold stocks with positive signal ratings",
        "ratings": ["STRONG_BUY", "BUY", "STRONG_HOLD"],
        "rsi_max": 35,
    },
    {
        "id": "earnings_this_week",
        "label": "Earnings This Week",
        "emoji": "📅",
        "color": "#ff9500",
        "description": "Stocks reporting earnings in the next 7 days",
        "earnings_days": 7,
    },
    {
        "id": "legally_clean",
        "label": "Legally Clean",
        "emoji": "⚖️",
        "color": "#00ff88",
        "description": "No SEC enforcement, investigations or class actions",
        "ratings": ["STRONG_BUY", "BUY", "STRONG_HOLD"],
        "legally_clean": True,
    },
    {
        "id": "insider_buying_surge",
        "label": "Insider Buying Surge",
        "emoji": "👤",
        "color": "#af52de",
        "description": "Stocks with significant insider accumulation",
        "ratings": ["STRONG_BUY", "BUY", "STRONG_HOLD"],
        "insider_score_min": 70,
    },
    {
        "id": "undervalued",
        "label": "Undervalued",
        "emoji": "💎",
        "color": "#ff6b35",
        "description": "Stocks trading well below their 52-week high with positive signals",
        "ratings": ["STRONG_BUY", "BUY", "STRONG_HOLD"],
        "high_52w_pct_max": -20,
    },
]

THEMES_BY_ID = {t["id"]: t for t in THEMES}
