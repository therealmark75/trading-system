"""
SignalIntel 12-Month Price Target Model
Blends four components into a single 12-month forward price target.

  Component               Weight   Method
  ─────────────────────────────────────────────────────────────────
  DCF fair value          40%      Project EPS 12 months forward,
                                   apply sector P/E exit multiple
  Analyst consensus       35%      FMP/FinViz analyst target
                                   (industry-standard 12-month)
  Technical projection    15%      Linear regression on price
                                   history extrapolated 12 months,
                                   capped at ±30% from current
  Quality/legal adjust    10%      Fundamental quality proxy and
                                   legal risk applied as multiplier

Fallback when analyst data is missing:
  DCF 60% · Technical 25% · Quality/legal 15%
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# Legal risk multipliers applied inside the quality/legal component
_LEGAL_MULT = {
    "NONE":               1.00,
    "MINOR":              0.98,
    "CLASS_ACTION":       0.95,
    "SEC_INVESTIGATION":  0.90,
    "SEC_ENFORCEMENT":    0.85,
    "CRIMINAL":           0.75,
}

# Sector median P/E ratios (terminal exit multiple for DCF)
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

# Sector median 1-year EPS growth rates (decimal) as DCF fallback
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


def _dcf_component(row: dict) -> float | None:
    """
    12-month forward DCF: project EPS one year out and apply sector P/E.
    Returns estimated 12-month intrinsic price, or None if data insufficient.
    """
    price  = row.get("price")
    pe     = row.get("pe_ratio")
    sector = row.get("sector", "")
    g_next = row.get("eps_growth_next_yr")   # % e.g. 12.5 → 0.125

    if not price or price <= 0:
        return None
    if not pe or pe <= 0 or pe > 300:
        return None

    eps = price / pe
    if eps <= 0:
        return None

    # Growth rate: prefer explicit next-year EPS growth, fall back to sector
    if g_next is not None and -50 < g_next < 100:
        g = g_next / 100.0
    else:
        g = _SECTOR_GROWTH.get(sector, 0.08)

    g = max(-0.15, min(g, 0.25))

    # 12-month forward EPS × sector P/E = 12-month target
    eps_12m     = eps * (1.0 + g)
    sector_pe   = _SECTOR_PE.get(sector, 18.0)
    target      = eps_12m * sector_pe

    if target <= 0:
        return None
    return round(target, 2)


def _linear_regression_slope(xs: list, ys: list):
    """Return (slope, intercept) for a simple OLS fit."""
    n = len(xs)
    if n < 2:
        return None, None
    sum_x  = sum(xs)
    sum_y  = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)
    denom  = n * sum_xx - sum_x ** 2
    if denom == 0:
        return None, None
    slope     = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def _technical_component(row: dict, price_history: list = None) -> float | None:
    """
    12-month technical projection.
    If price_history is provided (list of (days_ago, price) tuples), fits a linear
    regression and extrapolates 12 months (365 days) forward.
    Falls back to a 52W range / SMA trend estimate when history is unavailable.
    Result is capped at ±30% of current price.
    """
    price = row.get("price")
    if not price or price <= 0:
        return None

    # ── Regression-based projection ──────────────────────────────────────────
    if price_history and len(price_history) >= 5:
        # xs = days from oldest point (ascending), ys = price
        sorted_hist = sorted(price_history, key=lambda t: t[0], reverse=True)
        # days_ago → convert to relative x (0 = oldest point)
        max_age = sorted_hist[-1][0]  # largest days_ago = oldest
        xs = [max_age - da for da, _ in sorted_hist]
        ys = [p for _, p in sorted_hist]

        slope, intercept = _linear_regression_slope(xs, ys)
        if slope is not None:
            # x for 12 months forward = max_age + 365 from the origin
            x_target = max_age + 365
            projected = intercept + slope * x_target
            if projected > 0:
                cap_lo = price * 0.70
                cap_hi = price * 1.30
                return round(max(cap_lo, min(projected, cap_hi)), 2)

    # ── Fallback: SMA trend + 52W range ──────────────────────────────────────
    high_52w_pct = row.get("high_52w_pct")
    low_52w_pct  = row.get("low_52w_pct")
    sma50_pct    = row.get("sma_50_pct")
    sma200_pct   = row.get("sma_200_pct")
    rsi          = row.get("rsi_14")

    if high_52w_pct is None or low_52w_pct is None:
        return None

    try:
        high_52w = price / (1.0 + high_52w_pct / 100.0)
        low_52w  = price / (1.0 + low_52w_pct  / 100.0)
    except ZeroDivisionError:
        return None

    if high_52w <= 0 or low_52w <= 0:
        return None

    midpoint = (high_52w + low_52w) / 2.0

    if rsi is not None:
        if rsi < 30:
            target = high_52w * 0.85
        elif rsi > 70:
            target = price * 1.02
        else:
            target = midpoint * 1.05
    else:
        target = midpoint

    # Trend adjustment from SMAs
    trend_adj = 0.0
    if sma50_pct is not None:
        trend_adj += 0.02 if sma50_pct > 5 else (-0.02 if sma50_pct < -5 else 0)
    if sma200_pct is not None:
        trend_adj += 0.01 if sma200_pct > 0 else -0.01

    target = target * (1.0 + trend_adj)
    cap_lo = price * 0.70
    cap_hi = price * 1.30
    return round(max(cap_lo, min(target, cap_hi)), 2)


def _analyst_component(row: dict, fmp_price_target: float = None) -> float | None:
    """
    Analyst consensus 12-month target.
    FMP price target is already a 12-month analyst consensus — use directly.
    Falls back to deriving a target from analyst recommendation score × P/E.
    """
    price  = row.get("price")
    pe     = row.get("pe_ratio")
    recom  = row.get("analyst_recom")   # 1=Strong Buy … 5=Strong Sell
    sector = row.get("sector", "")

    if not price or price <= 0:
        return None

    # FMP target = 12-month analyst consensus — most reliable
    if fmp_price_target and fmp_price_target > 0:
        return round(fmp_price_target, 2)

    # Derive from analyst recommendation + sector P/E
    if recom is None:
        return None
    if pe is None or pe <= 0 or pe > 300:
        pe = _SECTOR_PE.get(sector, 18.0)

    eps = price / pe if pe > 0 else None
    if not eps or eps <= 0:
        return None

    pe_adj = {1: 1.25, 2: 1.10, 3: 1.00, 4: 0.90, 5: 0.75}
    r_floor = math.floor(recom)
    r_ceil  = math.ceil(recom)
    if r_floor == r_ceil:
        adj = pe_adj.get(r_floor, 1.00)
    else:
        adj_lo  = pe_adj.get(r_floor, 1.00)
        adj_hi  = pe_adj.get(r_ceil,  1.00)
        adj     = adj_lo + (recom - r_floor) * (adj_hi - adj_lo)

    sector_pe = _SECTOR_PE.get(sector, 18.0)
    target    = eps * sector_pe * adj
    return round(target, 2) if target > 0 else None


def _quality_legal_target(
    price: float,
    row: dict,
    legal_risk_level: str,
) -> float:
    """
    Quality/legal adjustment component.
    Returns price × quality_mult × legal_mult, bounded at ±15% of current price.
    """
    # Derive quality signal from fundamentals (ROE, EPS growth, analyst rec)
    quality_adj = 0.0

    roe = row.get("roe")
    if roe is not None:
        if roe > 20:   quality_adj += 0.04
        elif roe > 10: quality_adj += 0.02
        elif roe < 0:  quality_adj -= 0.04

    g = row.get("eps_growth_next_yr")
    if g is not None:
        if g > 15:    quality_adj += 0.04
        elif g > 5:   quality_adj += 0.02
        elif g < -10: quality_adj -= 0.04

    recom = row.get("analyst_recom")
    if recom is not None:
        # recom 1=strong buy → +4%, 5=strong sell → -4%
        quality_adj += (3.0 - recom) / 2.0 * 0.04

    quality_adj = max(-0.10, min(quality_adj, 0.10))
    legal_mult  = _LEGAL_MULT.get(legal_risk_level, 1.00)

    target = price * (1.0 + quality_adj) * legal_mult
    return round(max(price * 0.85, min(target, price * 1.15)), 2)


def compute_target_price(
    row: dict,
    legal_risk_level: str = "NONE",
    fmp_price_target: float = None,
    price_history: list = None,
) -> dict:
    """
    Master function — computes the SignalIntel 12-month price target.

    Args:
        row:               screener_snapshots row (dict)
        legal_risk_level:  risk tier string from legal_risk table
        fmp_price_target:  12-month analyst target from FMP (optional)
        price_history:     list of (days_ago, price) tuples for regression

    Returns dict:
        target_price    float | None
        target_upside   float | None   (% vs current price)
        components      dict
    """
    price = row.get("price")
    if not price or price <= 0:
        return {"target_price": None, "target_upside": None, "components": {}}

    dcf_val      = _dcf_component(row)
    tech_val     = _technical_component(row, price_history)
    analyst_val  = _analyst_component(row, fmp_price_target)
    ql_val       = _quality_legal_target(price, row, legal_risk_level)

    analyst_available = analyst_val is not None and analyst_val > 0

    if analyst_available:
        # Standard weights: DCF 40%, analyst 35%, technical 15%, quality/legal 10%
        component_weights = {
            "dcf":      (dcf_val,   0.40),
            "analyst":  (analyst_val, 0.35),
            "technical":(tech_val,  0.15),
            "ql":       (ql_val,    0.10),
        }
    else:
        # Fallback weights: DCF 60%, technical 25%, quality/legal 15%
        component_weights = {
            "dcf":      (dcf_val,  0.60),
            "technical":(tech_val, 0.25),
            "ql":       (ql_val,   0.15),
        }

    total_weight = 0.0
    weighted_sum = 0.0
    for name, (val, w) in component_weights.items():
        if val is not None and val > 0:
            weighted_sum += val * w
            total_weight += w

    if total_weight < 0.20:
        return {"target_price": None, "target_upside": None, "components": {
            "dcf": dcf_val, "technical": tech_val,
            "analyst": analyst_val, "quality_legal": ql_val,
        }}

    final_price = weighted_sum / total_weight
    # Hard bounds: 20%–300% of current price
    final_price = max(price * 0.20, min(final_price, price * 3.00))
    final_price = round(final_price, 2)

    upside = round(((final_price - price) / price) * 100, 1)

    return {
        "target_price":  final_price,
        "target_upside": upside,
        "components": {
            "dcf":           dcf_val,
            "technical":     tech_val,
            "analyst":       analyst_val,
            "quality_legal": ql_val,
        },
    }


def compute_targets_batch(
    screener_rows: list,
    legal_risk_map: dict = None,
    fmp_targets_map: dict = None,
    price_history_map: dict = None,
) -> list:
    """
    Compute 12-month target prices for a list of screener rows.
    Returns list of dicts: {ticker, target_price, target_upside}

    price_history_map: {ticker: [(days_ago, price), ...]} for regression.
    """
    legal_risk_map   = legal_risk_map   or {}
    fmp_targets_map  = fmp_targets_map  or {}
    price_history_map = price_history_map or {}
    results = []

    for row in screener_rows:
        ticker = row.get("ticker", "")
        if not ticker:
            continue

        lr_data      = legal_risk_map.get(ticker, {})
        lr_level     = lr_data.get("risk_level", "NONE") if lr_data else "NONE"
        fmp_target   = fmp_targets_map.get(ticker)
        price_hist   = price_history_map.get(ticker)

        result = compute_target_price(row, lr_level, fmp_target, price_hist)
        results.append({
            "ticker":        ticker,
            "target_price":  result["target_price"],
            "target_upside": result["target_upside"],
        })

    return results
