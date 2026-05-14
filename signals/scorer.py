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

from config.constants import MIN_PRICE_FOR_SIGNAL
from signals.line_item_keys import PIOTROSKI_LOOKUPS, ALTMAN_LOOKUPS

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
    else:
        score += 20  # P5: NULL input → neutral contribution

    # 52-week low proximity (max +35 pts)
    low_52w = row.get("low_52w_pct")
    if low_52w is not None:
        # low_52w_pct is % above 52w low (0% = AT the low)
        if low_52w < 5:    score += 35
        elif low_52w < 10: score += 25
        elif low_52w < 20: score += 12
        elif low_52w < 30: score += 5
    else:
        score += 17.5  # P5: NULL input → neutral contribution

    # Below 50-day SMA (confirms pullback) max +25 pts
    sma50 = row.get("sma_50_pct")
    if sma50 is not None:
        if sma50 < -10:   score += 25
        elif sma50 < -5:  score += 15
        elif sma50 < 0:   score += 8
    else:
        score += 12.5  # P5: NULL input → neutral contribution

    return _clamp(score)


def _compute_volume(rvol, pct):
    """
    Returns (score, band) for internal use and testing.
    Bands: 'null' | 'climax' | 'confirmed' | 'mild' | 'low'
    NULL inputs always return (50, 'null') — P5: NULL = neutral.
    """
    if rvol is None or pct is None:
        return 50.0, "null"

    if rvol >= 4.0:
        # Climax/exhaustion zone: extreme volume often precedes reversal
        if pct >= 1.0:        return 65.0, "climax"
        elif pct <= -1.0:     return 35.0, "climax"
        else:                 return 50.0, "climax"

    if rvol >= 1.5:
        # Standard confirmed breakout/breakdown zone
        if pct >= 1.0:        return 80.0, "confirmed"
        elif pct <= -1.0:     return 20.0, "confirmed"
        else:                 return 50.0, "confirmed"

    if rvol >= 0.8:
        # Average volume zone: directional but mild
        if pct >= 1.0:        return 60.0, "mild"
        elif pct <= -1.0:     return 40.0, "mild"
        else:                 return 50.0, "mild"

    # Low conviction — no signal regardless of direction
    return 50.0, "low"


def score_volume(rvol, pct) -> float:
    """Score 0-100 based on relative volume × price-change direction."""
    score, _ = _compute_volume(rvol, pct)
    return score


# ── Phase 2b-ii enrichment scorers ───────────────────────────────────────────

def _parse_market_cap_text(s) -> "float | None":
    """Parse market cap text from screener rows ('1.5B' → 1.5e9). Returns None on failure."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip().upper().replace(",", "")
    multipliers = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}
    suffix = s[-1] if s else ""
    try:
        if suffix in multipliers:
            return float(s[:-1]) * multipliers[suffix]
        return float(s)
    except (ValueError, IndexError):
        return None


def score_earnings_surprise(ticker: str, earnings_list: list) -> float:
    """Score 0-100 from up to 4 quarters of EPS surprise data, decay-weighted (4/3/2/1).

    Catches: persistent earnings miss pattern even when recent quarter beat (decay weight).
    Ignores: quarters where surprise_pct is NULL (treated as neutral 0 contribution).
    P5: empty earnings_list → returns neutral 50.0.
    """
    if not earnings_list:
        return 50.0

    def _contribution(surprise_pct) -> float:
        if surprise_pct is None:
            return 0.0
        if surprise_pct > 10:
            return 25.0
        if surprise_pct > 3:
            return 15.0
        if surprise_pct > 0:
            return 7.0
        if surprise_pct > -3:
            return 0.0   # neutral zone: -3% < surprise <= 0%
        if surprise_pct >= -10:
            return -15.0
        return -25.0

    decay_weights = [4, 3, 2, 1]
    total_w = 0.0
    total_c = 0.0
    for i, quarter in enumerate(earnings_list[:4]):
        w = decay_weights[i]
        c = _contribution(quarter.get("surprise_pct"))
        total_w += w
        total_c += w * c

    if total_w == 0:
        return 50.0

    weighted_avg = total_c / total_w   # range [-25, +25]
    return _clamp((weighted_avg + 25.0) * 2.0)


def score_piotroski(ticker: str, financials: dict) -> float:
    """Piotroski F-Score (0-9 binary signals) mapped to 0-100 score.

    Lock 1: if fewer than 2 fiscal years available, return 50.0 immediately.
    Cannot compute change-based signals (F3, F5, F6, F7, F8, F9) with only 1 year.

    Catches: fundamental deterioration (ROA decline, leverage increase, dilution).
    Ignores: companies with < 2 years of data — treated as neutral, never penalised.
    P5: empty financials → returns neutral 50.0.
    """
    all_years: set = set()
    for stmt_data in financials.values():
        all_years.update(stmt_data.keys())

    sorted_years = sorted(all_years, reverse=True)
    if len(sorted_years) < 2:
        return 50.0  # Lock 1

    y0, y1 = sorted_years[0], sorted_years[1]

    def _get(canonical_key: str, year: str):
        stmt_type, raw_key = PIOTROSKI_LOOKUPS[canonical_key]
        return financials.get(stmt_type, {}).get(year, {}).get(raw_key)

    f = 0

    # F1: ROA > 0
    ni = _get("net_income", y0)
    ta = _get("total_assets", y0)
    if ni is not None and ta:
        f += 1 if ni / ta > 0 else 0

    # F2: Operating cash flow > 0
    ocf = _get("operating_cash_flow", y0)
    if ocf is not None:
        f += 1 if ocf > 0 else 0

    # F3: ROA improvement
    ni1 = _get("net_income", y1)
    ta1 = _get("total_assets", y1)
    if ni is not None and ta and ni1 is not None and ta1:
        f += 1 if (ni / ta) > (ni1 / ta1) else 0

    # F4: OCF > Net income (accruals quality)
    if ocf is not None and ni is not None:
        f += 1 if ocf > ni else 0

    # F5: Long-term leverage decreased
    ltd  = _get("long_term_debt", y0)
    ltd1 = _get("long_term_debt", y1)
    if ltd is not None and ta and ltd1 is not None and ta1:
        f += 1 if (ltd / ta) < (ltd1 / ta1) else 0

    # F6: Current ratio improved
    ca  = _get("current_assets", y0)
    cl  = _get("current_liabilities", y0)
    ca1 = _get("current_assets", y1)
    cl1 = _get("current_liabilities", y1)
    if ca is not None and cl and ca1 is not None and cl1:
        f += 1 if (ca / cl) > (ca1 / cl1) else 0

    # F7: No new share dilution
    so  = _get("shares_outstanding", y0)
    so1 = _get("shares_outstanding", y1)
    if so is not None and so1 is not None:
        f += 1 if so <= so1 else 0

    # F8: Gross margin improved
    gp   = _get("gross_profit", y0)
    rev  = _get("total_revenue", y0)
    gp1  = _get("gross_profit", y1)
    rev1 = _get("total_revenue", y1)
    if gp is not None and rev and gp1 is not None and rev1:
        f += 1 if (gp / rev) > (gp1 / rev1) else 0

    # F9: Asset turnover improved
    if rev is not None and ta and rev1 is not None and ta1:
        f += 1 if (rev / ta) > (rev1 / ta1) else 0

    if f >= 7: return 80.0
    if f == 6: return 65.0
    if f == 5: return 50.0
    if f == 4: return 38.0
    return 20.0


def score_altman_penalty(ticker: str, financials: dict, market_cap_text) -> int:
    """Altman Z-Score additive penalty (0, -10, -30, -60).

    All-or-nothing: any required value missing → return 0 (no penalty).
    X4 uses TotalLiabilitiesNetMinorityInterest (classic Altman formula).

    Catches: financial distress (Z < 1.8 = distress zone).
    Ignores: companies with incomplete financial data — no penalty, never punish missing data.
    P5: empty financials → returns 0.
    """
    all_years: set = set()
    for stmt_data in financials.values():
        all_years.update(stmt_data.keys())

    if not all_years:
        return 0

    y0 = max(all_years)

    def _get(canonical_key: str):
        stmt_type, raw_key = ALTMAN_LOOKUPS[canonical_key]
        return financials.get(stmt_type, {}).get(y0, {}).get(raw_key)

    wc  = _get("working_capital")
    ta  = _get("total_assets")
    re  = _get("retained_earnings")
    eb  = _get("ebit")
    tl  = _get("total_liabilities")
    rev = _get("total_revenue")
    mc  = _parse_market_cap_text(market_cap_text)

    if any(v is None for v in (wc, ta, re, eb, tl, rev, mc)):
        return 0
    if ta == 0 or tl == 0:
        return 0

    x1 = wc  / ta
    x2 = re  / ta
    x3 = eb  / ta
    x4 = mc  / tl
    x5 = rev / ta

    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    if z >= 3.0:  return 0
    if z >= 1.8:  return -10
    if z >= 0.0:  return -30
    return -60


def score_inst_ownership(ticker: str, inst_data: "dict | None") -> float:
    """Score 0-100 from institutional ownership percentage (most recent filing).

    Lock 3: pct > 60 → 75.0 (flattened top tier, not 80→65).
    Caps pct at 100 to handle data outliers.

    Catches: low institutional conviction (< 20% held → score 35).
    Ignores: tickers with no institutional holder data — treated as neutral 50.0.
    P5: inst_data is None → returns neutral 50.0.
    """
    if inst_data is None:
        return 50.0
    pct = inst_data.get("total_pct_held")
    if pct is None:
        return 50.0
    pct = min(float(pct), 100.0)
    if pct > 60: return 75.0
    if pct > 40: return 55.0
    if pct > 20: return 45.0
    return 35.0


def score_analyst_momentum(ticker: str, mom_data: "dict | None") -> float:
    """Score 0-100 from net analyst upgrade/downgrade momentum over 90 days.

    net_momentum = upgrades_90d - downgrades_90d (upgrades include 'init' actions).

    Catches: coordinated analyst downgrade cycles before price action.
    Ignores: tickers with no analyst changes in window — treated as neutral 50.0.
    P5: mom_data is None → returns neutral 50.0.
    """
    if mom_data is None:
        return 50.0
    net = mom_data.get("net_momentum")
    if net is None:
        return 50.0
    if net >= 3:  return 80.0
    if net == 2:  return 70.0
    if net == 1:  return 60.0
    if net == 0:  return 50.0
    if net == -1: return 40.0
    if net == -2: return 30.0
    return 20.0   # net <= -3


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
    volume_score:     float = 50.0
    legal_penalty:    int   = 0
    legal_risk_level: str   = "NONE"
    composite_score:  float = 0.0      # sector-adjusted final score
    composite_score_raw: float = 0.0   # pre-sector-modifier score

    # Sector relative strength
    sector_strength_score:   float = 50.0   # 0-100, 50 = neutral
    sector_modifier_applied: float = 0.0    # multiplier delta actually applied

    rating:           str   = "STRONG_HOLD"
    flags:            list  = field(default_factory=list)

    # Raw data for display
    rsi_14:           float = None
    sma_50_pct:       float = None
    sma_200_pct:      float = None
    analyst_recom:    float = None
    short_interest:   float = None
    insider_count:    int   = 0

    # Phase 2b-ii enrichment scores (default neutral until scorers wired in)
    earnings_score:    float = 50.0
    piotroski_score:   float = 50.0
    altman_penalty:    int   = 0
    inst_own_score:    float = 50.0
    analyst_mom_score: float = 50.0


def compute_composite(
    momentum:    float,
    quality:     float,
    insider:     float,
    reversion:   float,
    volume:      float = 50.0,
    earnings:    float = 50.0,
    piotroski:   float = 50.0,
    inst_own:    float = 50.0,
    analyst_mom: float = 50.0,
    weights:     dict  = None,
) -> float:
    """Weighted composite. Normalises by sum(weights) so adding new
    components without changing existing weight values remains valid.
    Phase 2b-ii: total weight 1.60 (was 1.10 in v0.12.0)."""
    if weights is None:
        weights = {
            "momentum":    0.35,
            "quality":     0.30,
            "insider":     0.25,
            "reversion":   0.10,
            "volume":      0.10,
            "earnings":    0.125,
            "piotroski":   0.125,
            "inst_own":    0.125,
            "analyst_mom": 0.125,
        }
    total_w = sum(weights.values())
    raw = (
        momentum    * weights["momentum"]              +
        quality     * weights["quality"]               +
        insider     * weights["insider"]               +
        reversion   * weights["reversion"]             +
        volume      * weights.get("volume",      0.0)  +
        earnings    * weights.get("earnings",    0.0)  +
        piotroski   * weights.get("piotroski",   0.0)  +
        inst_own    * weights.get("inst_own",    0.0)  +
        analyst_mom * weights.get("analyst_mom", 0.0)
    )
    return _clamp(raw / total_w)


def assign_rating(composite: float, reversion: float,
                  insider: float) -> str:
    """
    Rating logic:
    STRONG_BUY  — composite >= 72 AND insider >= 65
    BUY         — composite >= 62
    STRONG_HOLD — composite 45-62
    SELL        — composite < 45
    WEAK_HOLD   — composite < 38 AND insider <= 35
    STRONG_SELL — composite < 25 AND insider <= 20
    HOLD        — reversion >= 75
    """
    if composite >= 72 and insider >= 65:
        return "STRONG_BUY"
    if composite >= 62:
        return "BUY"
    if reversion >= 75:
        return "HOLD"
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


_SECTOR_MODIFIER_WEIGHT = 0.15   # dial to 0.10–0.20 after backtesting


def score_all_tickers(
    ticker_data_rows: list[dict],
    insider_trades: list[dict],
    weights: dict = None,
    legal_risk_map: dict = None,
    sector_strength_map: dict = None,
    earnings_map: dict = None,
    financials_map: dict = None,
    inst_own_map: dict = None,
    analyst_mom_map: dict = None,
) :
    """
    Main entry point. Takes screener rows + insider trades,
    returns sorted list of TickerSignal objects.

    Enrichment map kwargs (Phase 2b-ii):
    - earnings_map:    {ticker: [{eps_actual, eps_estimate, surprise_pct, fiscal_quarter}, ...]}
    - financials_map:  {ticker: {stmt_type: {fiscal_year: {line_item_key: value}}}}
    - inst_own_map:    {ticker: {total_pct_held, holder_count, filing_date}}
    - analyst_mom_map: {ticker: {upgrades_90d, downgrades_90d, net_momentum}}
    Absent maps default to {} → all enrichment scorers return neutral (P5 compliance).
    """
    legal_risk_map      = legal_risk_map      or {}
    sector_strength_map = sector_strength_map or {}
    earnings_map        = earnings_map        or {}
    financials_map      = financials_map      or {}
    inst_own_map        = inst_own_map        or {}
    analyst_mom_map     = analyst_mom_map     or {}
    missing_legal  = []
    results = []

    for row in ticker_data_rows:
        ticker = row.get("ticker", "")
        if not ticker:
            continue

        price = row.get("price")
        if price is not None and price < MIN_PRICE_FOR_SIGNAL:
            logger.debug("Skipped %s — price $%.4f below MIN_PRICE_FOR_SIGNAL", ticker, price)
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
        v_score = score_volume(row.get("rel_volume"), row.get("change_pct"))

        # Phase 2b-ii enrichment scores
        e_score   = score_earnings_surprise(ticker, earnings_map.get(ticker, []))
        p_score   = score_piotroski(ticker, financials_map.get(ticker, {}))
        io_score  = score_inst_ownership(ticker, inst_own_map.get(ticker))
        am_score  = score_analyst_momentum(ticker, analyst_mom_map.get(ticker))
        altman_pen = score_altman_penalty(ticker, financials_map.get(ticker, {}),
                                          row.get("market_cap"))

        legal_data = legal_risk_map.get(ticker)
        if legal_data is None:
            missing_legal.append(ticker)
            legal_penalty    = 0
            legal_risk_level = "NONE"
        else:
            legal_penalty    = legal_data.get("penalty", 0)
            legal_risk_level = legal_data.get("risk_level", "NONE")

        raw_composite = compute_composite(
            m_score, q_score, i_score, r_score, v_score,
            e_score, p_score, io_score, am_score, weights,
        )
        c_score_raw = _clamp(raw_composite + legal_penalty + altman_pen)

        # ── Sector relative strength modifier ────────────────────────────────
        sector           = row.get("sector", "")
        sector_ss        = sector_strength_map.get(sector, 50.0)
        sector_modifier  = (sector_ss - 50.0) / 100.0         # -0.5 … +0.5
        c_score          = _clamp(c_score_raw * (1.0 + sector_modifier * _SECTOR_MODIFIER_WEIGHT))
        modifier_applied = round(c_score - c_score_raw, 2)
        # ─────────────────────────────────────────────────────────────────────

        rating = assign_rating(c_score, r_score, i_score)
        flags  = build_flags(row, i_score, r_score)

        sig = TickerSignal(
            ticker                  = ticker,
            company                 = row.get("company", ""),
            sector                  = sector,
            price                   = row.get("price"),
            change_pct              = row.get("change_pct"),
            momentum_score          = round(m_score, 1),
            quality_score           = round(q_score, 1),
            insider_score           = round(i_score, 1),
            reversion_score         = round(r_score, 1),
            volume_score            = round(v_score, 1),
            legal_penalty           = legal_penalty,
            legal_risk_level        = legal_risk_level,
            composite_score         = round(c_score, 1),
            composite_score_raw     = round(c_score_raw, 1),
            sector_strength_score   = round(sector_ss, 1),
            sector_modifier_applied = modifier_applied,
            rating                  = rating,
            flags                   = flags,
            rsi_14                  = row.get("rsi_14"),
            sma_50_pct              = row.get("sma_50_pct"),
            sma_200_pct             = row.get("sma_200_pct"),
            analyst_recom           = row.get("analyst_recom"),
            short_interest          = row.get("short_interest_pct"),
            insider_count           = len(ticker_insiders),
            earnings_score          = round(e_score, 1),
            piotroski_score         = round(p_score, 1),
            altman_penalty          = altman_pen,
            inst_own_score          = round(io_score, 1),
            analyst_mom_score       = round(am_score, 1),
        )
        results.append(sig)

    if missing_legal:
        sample = missing_legal[:5]
        logger.warning(
            f"  Legal risk missing for {len(missing_legal)} tickers "
            f"({', '.join(sample)}{'...' if len(missing_legal) > 5 else ''}) — flagged for scraping"
        )

    # Sort by composite score descending
    results.sort(key=lambda x: x.composite_score, reverse=True)
    logger.info(f"Scored {len(results)} tickers")
    return results
