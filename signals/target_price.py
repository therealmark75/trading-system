"""
SignalIntel Target Price Model
Combines 4 components into a single proprietary target price.

Components:
  1. DCF (35%)            — Discounted cash flow from EPS projections
  2. Technical (25%)      — 52W range + RSI + trend adjustment
  3. Analyst Anchor (30%) — Analyst consensus / FMP price targets
  4. Legal Risk (10%)     — Legal penalty adjusts final output

The model only uses data already in the DB — no live API calls.
FMP analyst price targets (when available) are incorporated in component 3.
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# Legal risk adjustment multipliers on the FINAL target
_LEGAL_ADJUST = {
    "NONE":              1.00,
    "MINOR":             0.98,
    "CLASS_ACTION":      0.95,
    "SEC_INVESTIGATION": 0.90,
    "SEC_ENFORCEMENT":   0.85,
    "CRIMINAL":          0.75,
}

# Sector median P/E ratios (fallback when company P/E is missing)
_SECTOR_PE = {
    "Technology":             28.0,
    "Healthcare":             22.0,
    "Financial":              14.0,
    "Consumer Cyclical":      20.0,
    "Consumer Defensive":     22.0,
    "Industrials":            19.0,
    "Energy":                 13.0,
    "Real Estate":            35.0,
    "Utilities":              18.0,
    "Communication Services": 20.0,
    "Basic Materials":        15.0,
}

# Sector median 5-year EPS growth rates (decimal) for DCF
_SECTOR_GROWTH = {
    "Technology":             0.12,
    "Healthcare":             0.10,
    "Financial":              0.09,
    "Consumer Cyclical":      0.10,
    "Consumer Defensive":     0.06,
    "Industrials":            0.08,
    "Energy":                 0.07,
    "Real Estate":            0.05,
    "Utilities":              0.04,
    "Communication Services": 0.09,
    "Basic Materials":        0.07,
}

_DISCOUNT_RATE  = 0.10   # WACC approximation
_TERMINAL_GROWTH = 0.03  # perpetuity growth
_YEARS = 5


def _dcf_component(row: dict) -> float | None:
    """
    Simplified DCF: project EPS over 5 years, discount back, add terminal value.
    Returns estimated intrinsic price or None if insufficient data.
    """
    price    = row.get("price")
    pe       = row.get("pe_ratio")
    sector   = row.get("sector", "")
    g_next   = row.get("eps_growth_next_yr")   # % like 12.5 → 0.125
    g_5yr    = row.get("sales_growth_5yr")     # 5yr sales growth as proxy if eps unavailable

    if not price or price <= 0:
        return None
    if not pe or pe <= 0 or pe > 300:
        return None

    # Derive current EPS from P/E
    eps = price / pe
    if eps <= 0:
        return None

    # Choose growth rate: prefer explicit forward EPS growth, fall back to sector median
    if g_next is not None and -50 < g_next < 100:
        g = g_next / 100.0
    else:
        g = _SECTOR_GROWTH.get(sector, 0.08)

    # Cap growth to reasonable range
    g = max(-0.15, min(g, 0.25))

    # Sector P/E for terminal multiple
    sector_pe = _SECTOR_PE.get(sector, 18.0)

    # PV of projected EPS streams
    pv = 0.0
    for yr in range(1, _YEARS + 1):
        projected_eps = eps * ((1 + g) ** yr)
        pv += projected_eps / ((1 + _DISCOUNT_RATE) ** yr)

    # Terminal value at year 5: use Gordon growth model on earnings
    eps_year5       = eps * ((1 + g) ** _YEARS)
    terminal_price  = eps_year5 * sector_pe  # exit multiple approach
    pv_terminal     = terminal_price / ((1 + _DISCOUNT_RATE) ** _YEARS)

    intrinsic = pv + pv_terminal
    if intrinsic <= 0:
        return None
    return round(intrinsic, 2)


def _technical_component(row: dict) -> float | None:
    """
    RSI-adjusted fair value derived from the 52-week range and trend signals.
    """
    price       = row.get("price")
    high_52w_pct = row.get("high_52w_pct")   # % distance below 52W high (usually ≤0)
    low_52w_pct  = row.get("low_52w_pct")    # % distance above 52W low (usually ≥0)
    rsi          = row.get("rsi_14")
    sma50_pct    = row.get("sma_50_pct")
    sma200_pct   = row.get("sma_200_pct")

    if not price or price <= 0:
        return None
    if high_52w_pct is None or low_52w_pct is None:
        return None

    # Derive actual 52W prices from percentage distances
    # high_52w_pct = (price - high) / high * 100  → high = price / (1 + high_52w_pct/100)
    # low_52w_pct  = (price - low) / low * 100    → low  = price / (1 + low_52w_pct/100)
    try:
        high_52w = price / (1.0 + high_52w_pct / 100.0) if (1.0 + high_52w_pct / 100.0) != 0 else price
        low_52w  = price / (1.0 + low_52w_pct  / 100.0) if (1.0 + low_52w_pct  / 100.0) != 0 else price
    except ZeroDivisionError:
        return None

    if high_52w <= 0 or low_52w <= 0:
        return None

    midpoint = (high_52w + low_52w) / 2.0

    # RSI-adjusted target
    if rsi is not None:
        if rsi < 30:
            # Oversold: room to recover toward high
            target = high_52w * 0.85
        elif rsi > 70:
            # Overbought: limited upside from here
            target = price * 1.02
        else:
            # Normal range: midpoint with slight upward bias
            target = midpoint * 1.05
    else:
        target = midpoint

    # SMA trend adjustment (±3%)
    trend_adj = 0.0
    if sma50_pct is not None:
        if sma50_pct > 5:
            trend_adj += 0.02
        elif sma50_pct < -5:
            trend_adj -= 0.02
    if sma200_pct is not None:
        if sma200_pct > 0:
            trend_adj += 0.01
        else:
            trend_adj -= 0.01

    target *= (1 + trend_adj)
    target = max(target, price * 0.50)   # never below 50% of current price
    return round(target, 2)


def _analyst_component(row: dict, fmp_price_target: float = None) -> float | None:
    """
    Analyst consensus anchor.
    Prefers FMP price target; falls back to deriving a target from
    analyst recommendation score × sector P/E × current EPS.
    """
    price   = row.get("price")
    pe      = row.get("pe_ratio")
    recom   = row.get("analyst_recom")   # 1=Strong Buy, 5=Strong Sell
    sector  = row.get("sector", "")

    if not price or price <= 0:
        return None

    # Use FMP analyst price target directly if available
    if fmp_price_target and fmp_price_target > 0:
        # Weight the target more strongly when analyst conviction is high
        if recom is not None:
            conviction = abs(recom - 3.0) / 2.0   # 0=neutral, 1=max conviction
            if conviction > 0.4:
                return round(fmp_price_target, 2)
        return round(fmp_price_target, 2)

    # Derive target from analyst score + P/E + EPS
    if recom is None:
        return None
    if pe is None or pe <= 0 or pe > 300:
        pe = _SECTOR_PE.get(sector, 18.0)

    eps = price / pe if pe > 0 else None
    if not eps or eps <= 0:
        return None

    # Map analyst recommendation to P/E multiple adjustment
    # recom 1 (Strong Buy) → apply premium; recom 5 (Strong Sell) → discount
    pe_adj = {1: 1.25, 2: 1.10, 3: 1.00, 4: 0.90, 5: 0.75}
    adj = pe_adj.get(round(recom), 1.00)

    # Interpolate for non-integer values
    r_floor = math.floor(recom)
    r_ceil  = math.ceil(recom)
    if r_floor != r_ceil:
        adj_low  = pe_adj.get(r_floor, 1.00)
        adj_high = pe_adj.get(r_ceil, 1.00)
        frac = recom - r_floor
        adj  = adj_low + frac * (adj_high - adj_low)

    sector_pe  = _SECTOR_PE.get(sector, 18.0)
    target_pe  = sector_pe * adj
    target     = eps * target_pe

    if target <= 0:
        return None
    return round(target, 2)


def compute_target_price(
    row: dict,
    legal_risk_level: str = "NONE",
    fmp_price_target: float = None,
) -> dict:
    """
    Master function. Computes the weighted SignalIntel target price.

    Args:
        row:               screener_snapshots row (dict)
        legal_risk_level:  risk tier string from legal_risk table
        fmp_price_target:  analyst price target from FMP (optional)

    Returns dict:
        target_price    float | None
        target_upside   float | None   (% vs current price)
        components      dict  (per-component values and weights used)
    """
    price = row.get("price")
    if not price or price <= 0:
        return {"target_price": None, "target_upside": None, "components": {}}

    dcf_val      = _dcf_component(row)
    tech_val     = _technical_component(row)
    analyst_val  = _analyst_component(row, fmp_price_target)

    # Build weighted average from available components
    component_weights = {
        "dcf":     (dcf_val,     0.35),
        "technical":(tech_val,   0.25),
        "analyst": (analyst_val, 0.30),
    }

    total_weight = 0.0
    weighted_sum = 0.0
    for name, (val, w) in component_weights.items():
        if val is not None and val > 0:
            weighted_sum += val * w
            total_weight += w

    if total_weight < 0.25:
        # Not enough data for a meaningful estimate
        return {"target_price": None, "target_upside": None, "components": {
            "dcf": dcf_val, "technical": tech_val, "analyst": analyst_val,
        }}

    # Renormalise weights if some components were missing
    base_price = weighted_sum / total_weight

    # Legal risk final adjustment (10% weight via multiplier on output)
    legal_mult = _LEGAL_ADJUST.get(legal_risk_level, 1.00)
    final_price = base_price * legal_mult

    # Sanity bounds: target must be between 20% and 300% of current price
    final_price = max(price * 0.20, min(final_price, price * 3.00))
    final_price = round(final_price, 2)

    upside = round(((final_price - price) / price) * 100, 1)

    return {
        "target_price":  final_price,
        "target_upside": upside,
        "components": {
            "dcf":      dcf_val,
            "technical": tech_val,
            "analyst":  analyst_val,
            "legal_adj": round((legal_mult - 1.0) * 100, 1),
        },
    }


def compute_targets_batch(
    screener_rows: list[dict],
    legal_risk_map: dict = None,
    fmp_targets_map: dict = None,
) -> list[dict]:
    """
    Compute target prices for a list of screener rows.
    Returns list of dicts: {ticker, target_price, target_upside}
    """
    legal_risk_map  = legal_risk_map  or {}
    fmp_targets_map = fmp_targets_map or {}
    results = []

    for row in screener_rows:
        ticker = row.get("ticker", "")
        if not ticker:
            continue

        lr_data    = legal_risk_map.get(ticker, {})
        lr_level   = lr_data.get("risk_level", "NONE") if lr_data else "NONE"
        fmp_target = fmp_targets_map.get(ticker)

        result = compute_target_price(row, lr_level, fmp_target)
        results.append({
            "ticker":        ticker,
            "target_price":  result["target_price"],
            "target_upside": result["target_upside"],
        })

    return results
