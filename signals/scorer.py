# signals/scorer.py
# ─────────────────────────────────────────────────
# Phase 2: Multi-factor signal scoring engine.
#
# Scores every ticker across four dimensions:
#   1. Momentum       — price vs SMAs, RSI, relative volume
#   2. Quality        — ROE, margins, EPS growth, analyst rec
#   3. Insider        — conviction-weighted insider activity
#   4. Mean Reversion — oversold RSI + proximity to 52w low
#
# Each dimension scores 0-100.
# Composite score = weighted average.
# Final rating: STRONG_BUY | BUY | WATCH | AVOID | SHORT_WATCH
# ─────────────────────────────────────────────────

import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Insider title conviction weights ─────────────
# Higher = stronger signal per transaction
TITLE_WEIGHTS = {
    "ceo":                    10,
    "chief executive":        10,
    "cfo":                     8,
    "chief financial":         8,
    "coo":                     7,
    "chief operating":         7,
    "president":               7,
    "chairman":                8,
    "director":                6,
    "vp":                      4,
    "vice president":          4,
    "svp":                     5,
    "evp":                     6,
    "general counsel":         4,
    "10%":                     9,   # 10% owner = major stakeholder
    "owner":                   9,
}

DEFAULT_TITLE_WEIGHT = 3   # unknown title


def _title_weight(title: str) -> int:
    if not title:
        return DEFAULT_TITLE_WEIGHT
    tl = title.lower()
    for keyword, weight in TITLE_WEIGHTS.items():
        if keyword in tl:
            return weight
    return DEFAULT_TITLE_WEIGHT


# ── Scoring helpers ───────────────────────────────

def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def score_momentum(row: dict) -> float:
    """
    Score 0-100 based on:
    - Price vs 50-day SMA  (above = bullish)
    - Price vs 200-day SMA (above = bullish)
    - RSI 14 (40-70 sweet spot; extremes penalised)
    - Relative volume (high = interest)
    """
    score = 50.0   # neutral baseline

    # 50-day SMA component (max ±20 pts)
    sma50 = row.get("sma_50_pct")
    if sma50 is not None:
        if sma50 > 10:   score += 20
        elif sma50 > 5:  score += 15
        elif sma50 > 0:  score += 10
        elif sma50 > -5: score -= 5
        elif sma50 > -10:score -= 12
        else:            score -= 20

    # 200-day SMA component (max ±15 pts)
    sma200 = row.get("sma_200_pct")
    if sma200 is not None:
        if sma200 > 10:   score += 15
        elif sma200 > 0:  score += 8
        elif sma200 > -10:score -= 8
        else:             score -= 15

    # RSI component (max ±15 pts)
    rsi = row.get("rsi_14")
    if rsi is not None:
        if 50 <= rsi <= 70:   score += 15   # trending up, not overbought
        elif 40 <= rsi < 50:  score += 5    # mild bullish
        elif 30 <= rsi < 40:  score -= 5    # weakening
        elif rsi < 30:        score -= 15   # oversold (mean reversion scores this separately)
        elif rsi > 80:        score -= 10   # overbought

    # Relative volume (max +10 pts)
    rvol = row.get("rel_volume")
    if rvol is not None:
        if rvol > 2.0:   score += 10
        elif rvol > 1.5: score += 6
        elif rvol > 1.0: score += 3

    return _clamp(score)


def score_quality(row: dict) -> float:
    """
    Score 0-100 based on:
    - Return on Equity
    - EPS growth (this year + next year)
    - Short interest (high = risk)
    - Analyst recommendation (1=Strong Buy, 5=Strong Sell)
    """
    score = 50.0

    # ROE component (max ±20 pts)
    roe = row.get("roe")
    if roe is not None:
        if roe > 30:    score += 20
        elif roe > 20:  score += 15
        elif roe > 10:  score += 8
        elif roe > 0:   score += 2
        elif roe < 0:   score -= 15

    # EPS growth this year (max ±15 pts)
    eps_ty = row.get("eps_growth_this_yr")
    if eps_ty is not None:
        if eps_ty > 30:   score += 15
        elif eps_ty > 15: score += 10
        elif eps_ty > 0:  score += 5
        elif eps_ty < -20:score -= 15
        elif eps_ty < 0:  score -= 5

    # EPS growth next year (max ±10 pts, forward-looking)
    eps_ny = row.get("eps_growth_next_yr")
    if eps_ny is not None:
        if eps_ny > 20:   score += 10
        elif eps_ny > 10: score += 6
        elif eps_ny > 0:  score += 2
        elif eps_ny < 0:  score -= 8

    # Short interest (max -15 pts penalty)
    short = row.get("short_interest_pct")
    if short is not None:
        if short > 30:   score -= 15
        elif short > 20: score -= 10
        elif short > 10: score -= 5

    # Analyst recommendation (1=Strong Buy → 5=Strong Sell)
    analyst = row.get("analyst_recom")
    if analyst is not None:
        if analyst <= 1.5:   score += 15
        elif analyst <= 2.0: score += 10
        elif analyst <= 2.5: score += 5
        elif analyst >= 4.0: score -= 15
        elif analyst >= 3.5: score -= 8

    return _clamp(score)


def score_insider(ticker: str, insider_trades: list[dict],
                  window_days: int = 30) -> float:
    """
    Score 0-100 based on conviction-weighted insider activity.
    Buys add points, sales subtract, weighted by title seniority.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window_days)
    relevant = [
        t for t in insider_trades
        if t.get("ticker") == ticker and t.get("transaction_date")
    ]

    if not relevant:
        return 50.0   # neutral — no data

    buy_score  = 0.0
    sell_score = 0.0

    for t in relevant:
        try:
            td = datetime.strptime(t["transaction_date"], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if td < cutoff:
            continue

        weight = _title_weight(t.get("insider_title", ""))
        tx     = t.get("transaction_type", "")

        if tx == "Buy":
            buy_score  += weight
        elif tx == "Sale":
            sell_score += weight

    # Net conviction: positive = bullish, negative = bearish
    net = buy_score - sell_score

    # Map net score to 0-100
    # Net of +20 → score ~90, net of -20 → score ~10
    mapped = 50.0 + (net * 2.0)
    return _clamp(mapped)


def score_mean_reversion(row: dict) -> float:
    """
    Score 0-100 for mean reversion setups:
    - Oversold RSI (< 35) is good for reversion longs
    - Near 52-week low (within 10%) adds conviction
    - Below 50-day SMA confirms pullback
    High score = strong reversion candidate.
    """
    score = 0.0

    rsi = row.get("rsi_14")
    if rsi is not None:
        if rsi < 20:      score += 40
        elif rsi < 25:    score += 35
        elif rsi < 30:    score += 28
        elif rsi < 35:    score += 20
        elif rsi < 40:    score += 10
        else:             score += 0   # not oversold

    # 52-week low proximity (max +35 pts)
    low_52w = row.get("low_52w_pct")
    if low_52w is not None:
        # low_52w_pct is % above 52w low (0% = AT the low)
        if low_52w < 5:    score += 35
        elif low_52w < 10: score += 25
        elif low_52w < 20: score += 12
        elif low_52w < 30: score += 5

    # Below 50-day SMA (confirms pullback) max +25 pts
    sma50 = row.get("sma_50_pct")
    if sma50 is not None:
        if sma50 < -10:   score += 25
        elif sma50 < -5:  score += 15
        elif sma50 < 0:   score += 8

    return _clamp(score)


# ── Composite scorer ──────────────────────────────

@dataclass
class TickerSignal:
    ticker:           str
    company:          str
    sector:           str
    price:            float
    change_pct:       float

    momentum_score:   float = 0.0
    quality_score:    float = 0.0
    insider_score:    float = 0.0
    reversion_score:  float = 0.0
    composite_score:  float = 0.0

    rating:           str   = "STRONG_HOLD"
    flags:            list  = field(default_factory=list)

    # Raw data for display
    rsi_14:           float = None
    sma_50_pct:       float = None
    sma_200_pct:      float = None
    analyst_recom:    float = None
    short_interest:   float = None
    insider_count:    int   = 0


def compute_composite(
    momentum: float,
    quality:  float,
    insider:  float,
    reversion:float,
    weights:  dict = None,
) -> float:
    """Weighted composite. Default weights favour momentum + quality."""
    if weights is None:
        weights = {
            "momentum":  0.35,
            "quality":   0.30,
            "insider":   0.25,
            "reversion": 0.10,
        }
    return _clamp(
        momentum  * weights["momentum"]  +
        quality   * weights["quality"]   +
        insider   * weights["insider"]   +
        reversion * weights["reversion"]
    )


def assign_rating(composite: float, reversion: float,
                  insider: float) -> str:
    """
    Rating logic:
    STRONG_BUY   — composite >= 72 AND insider >= 65
BUY          — composite >= 62
STRONG_HOLD  — composite 45-62
SELL         — composite < 45
WEAK_HOLD    — composite < 38 AND insider <= 35
STRONG_SELL  — composite < 25 AND insider <= 20
HOLD         — reversion >= 75
    """
    if reversion >= 75:
        return "HOLD"
    if composite >= 72 and insider >= 65:
        return "STRONG_BUY"
    if composite >= 62:
        return "BUY"
    if composite < 25 and insider <= 20:
        return "STRONG_SELL"
    if composite < 38 and insider <= 35:
        return "WEAK_HOLD"
    if composite < 45:
        return "SELL"
    return "STRONG_HOLD"

def build_flags(row: dict, insider_score: float,
                reversion_score: float) -> list[str]:
    """Human-readable flags summarising why a ticker scored the way it did."""
    flags = []

    rsi = row.get("rsi_14")
    if rsi is not None:
        if rsi > 75:   flags.append("⚠ Overbought RSI")
        elif rsi < 30: flags.append("↩ Oversold RSI")

    sma50 = row.get("sma_50_pct")
    if sma50 is not None:
        if sma50 > 0:  flags.append("↑ Above 50d SMA")
        else:          flags.append("↓ Below 50d SMA")

    sma200 = row.get("sma_200_pct")
    if sma200 is not None:
        if sma200 > 0: flags.append("↑ Above 200d SMA")
        else:          flags.append("↓ Below 200d SMA")

    if insider_score >= 70:  flags.append("★ Strong insider buying")
    elif insider_score <= 30:flags.append("⚠ Insider selling pressure")

    short = row.get("short_interest_pct")
    if short and short > 20: flags.append(f"⚠ High short interest {short:.1f}%")

    analyst = row.get("analyst_recom")
    if analyst and analyst <= 1.8: flags.append("✓ Strong analyst consensus")

    low_52w = row.get("low_52w_pct")
    if low_52w is not None and low_52w < 10:
        flags.append("📍 Near 52-week low")

    high_52w = row.get("high_52w_pct")
    if high_52w is not None and high_52w > -5:
        flags.append("🔝 Near 52-week high")

    if reversion_score >= 75: flags.append("↩ Mean reversion candidate")

    return flags


def score_all_tickers(
    screener_rows: list[dict],
    insider_trades: list[dict],
    weights: dict = None,
) :
    """
    Main entry point. Takes screener rows + insider trades,
    returns sorted list of TickerSignal objects.
    """
    results = []

    for row in screener_rows:
        ticker = row.get("ticker", "")
        if not ticker:
            continue

        # Count recent insider transactions for this ticker
        ticker_insiders = [
            t for t in insider_trades
            if t.get("ticker") == ticker
        ]

        m_score = score_momentum(row)
        q_score = score_quality(row)
        i_score = score_insider(ticker, insider_trades)
        r_score = score_mean_reversion(row)
        c_score = compute_composite(m_score, q_score, i_score, r_score, weights)
        rating  = assign_rating(c_score, r_score, i_score)
        flags   = build_flags(row, i_score, r_score)

        sig = TickerSignal(
            ticker          = ticker,
            company         = row.get("company", ""),
            sector          = row.get("sector", ""),
            price           = row.get("price"),
            change_pct      = row.get("change_pct"),
            momentum_score  = round(m_score, 1),
            quality_score   = round(q_score, 1),
            insider_score   = round(i_score, 1),
            reversion_score = round(r_score, 1),
            composite_score = round(c_score, 1),
            rating          = rating,
            flags           = flags,
            rsi_14          = row.get("rsi_14"),
            sma_50_pct      = row.get("sma_50_pct"),
            sma_200_pct     = row.get("sma_200_pct"),
            analyst_recom   = row.get("analyst_recom"),
            short_interest  = row.get("short_interest_pct"),
            insider_count   = len(ticker_insiders),
        )
        results.append(sig)

    # Sort by composite score descending
    results.sort(key=lambda x: x.composite_score, reverse=True)
    logger.info(f"Scored {len(results)} tickers")
    return results
