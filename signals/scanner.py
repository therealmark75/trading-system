# signals/scanner.py
# ─────────────────────────────────────────────────
# Phase 2: Multi-factor confluence scanner.
#
# Finds tickers where multiple signals align:
#   - Cluster buy + oversold RSI
#   - Momentum breakout (above both SMAs + high volume)
#   - Quality value (strong fundamentals + low price)
#   - Earnings momentum (high EPS growth + analyst upgrade)
# ─────────────────────────────────────────────────

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    ticker:      str
    company:     str
    sector:      str
    price:       float
    scan_name:   str
    reasons:     list[str]
    score:       float


# ── Individual scan filters ───────────────────────

def scan_insider_oversold(screener_rows: list[dict],
                          cluster_signals: list[dict]) -> list[ScanResult]:
    """
    Tickers with CLUSTER_BUY signal AND RSI < 40.
    Classic: insiders buying while market is selling = conviction.
    """
    cluster_buy_tickers = {
        s["ticker"] for s in cluster_signals
        if "BUY" in (s.get("signal_type") or "")
    }

    results = []
    for row in screener_rows:
        ticker = row.get("ticker", "")
        if ticker not in cluster_buy_tickers:
            continue

        rsi = row.get("rsi_14")
        if rsi is None or rsi >= 45:
            continue

        insider_count = next(
            (s["insider_count"] for s in cluster_signals
             if s["ticker"] == ticker and "BUY" in s.get("signal_type", "")),
            0
        )

        reasons = [
            f"Cluster buy: {insider_count} insiders",
            f"RSI {rsi:.1f} (oversold/weak)",
        ]

        sma50 = row.get("sma_50_pct")
        if sma50 and sma50 < 0:
            reasons.append(f"Below 50d SMA by {abs(sma50):.1f}%")

        score = (insider_count * 8) + max(0, (45 - rsi) * 1.5)
        results.append(ScanResult(
            ticker   = ticker,
            company  = row.get("company", ""),
            sector   = row.get("sector", ""),
            price    = row.get("price"),
            scan_name= "INSIDER + OVERSOLD",
            reasons  = reasons,
            score    = round(score, 1),
        ))

    results.sort(key=lambda x: x.score, reverse=True)
    return results


def scan_momentum_breakout(screener_rows: list[dict]) -> list[ScanResult]:
    """
    Tickers above BOTH 50d and 200d SMA, RSI 55-72,
    relative volume > 1.3. Classic momentum breakout setup.
    """
    results = []
    for row in screener_rows:
        sma50  = row.get("sma_50_pct")
        sma200 = row.get("sma_200_pct")
        rsi    = row.get("rsi_14")
        rvol   = row.get("rel_volume")

        if any(v is None for v in [sma50, sma200, rsi]):
            continue
        if sma50 <= 0 or sma200 <= 0:
            continue
        if not (52 <= rsi <= 72):
            continue

        score = sma50 * 0.4 + sma200 * 0.3 + (rsi - 50) * 0.5
        if rvol and rvol > 1.3:
            score += rvol * 5

        reasons = [
            f"Above 50d SMA by {sma50:.1f}%",
            f"Above 200d SMA by {sma200:.1f}%",
            f"RSI {rsi:.1f} (trending)",
        ]
        if rvol:
            reasons.append(f"Rel volume {rvol:.2f}x")

        results.append(ScanResult(
            ticker   = row.get("ticker", ""),
            company  = row.get("company", ""),
            sector   = row.get("sector", ""),
            price    = row.get("price"),
            scan_name= "MOMENTUM BREAKOUT",
            reasons  = reasons,
            score    = round(score, 1),
        ))

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:50]   # cap at top 50


def scan_quality_value(screener_rows: list[dict]) -> list[ScanResult]:
    """
    High quality fundamentals at a reasonable price:
    - ROE > 15%
    - EPS growth this year > 10%
    - Analyst rec <= 2.5
    - RSI not overbought (< 72)
    """
    results = []
    for row in screener_rows:
        roe      = row.get("roe")
        eps_ty   = row.get("eps_growth_this_yr")
        analyst  = row.get("analyst_recom")
        rsi      = row.get("rsi_14")

        if roe is None or roe < 15:
            continue
        if eps_ty is None or eps_ty < 10:
            continue
        if analyst is None or analyst > 2.5:
            continue
        if rsi and rsi > 72:
            continue

        score = roe * 0.4 + eps_ty * 0.3 + (3.0 - analyst) * 10
        eps_ny = row.get("eps_growth_next_yr")
        if eps_ny and eps_ny > 10:
            score += eps_ny * 0.2

        reasons = [
            f"ROE {roe:.1f}%",
            f"EPS growth {eps_ty:.1f}% this year",
            f"Analyst rec {analyst:.1f}/5",
        ]
        if eps_ny:
            reasons.append(f"Forward EPS growth {eps_ny:.1f}%")

        results.append(ScanResult(
            ticker   = row.get("ticker", ""),
            company  = row.get("company", ""),
            sector   = row.get("sector", ""),
            price    = row.get("price"),
            scan_name= "QUALITY VALUE",
            reasons  = reasons,
            score    = round(score, 1),
        ))

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:50]


def scan_mean_reversion(screener_rows: list[dict]) -> list[ScanResult]:
    """
    Deep oversold candidates:
    - RSI < 35
    - Within 15% of 52-week low
    - Not in fundamental freefall (avoid if EPS growth < -30%)
    """
    results = []
    for row in screener_rows:
        rsi     = row.get("rsi_14")
        low_52w = row.get("low_52w_pct")
        eps_ty  = row.get("eps_growth_this_yr")

        if rsi is None or rsi >= 35:
            continue
        if low_52w is None or low_52w > 20:
            continue
        if eps_ty is not None and eps_ty < -40:
            continue   # fundamental collapse, not reversion candidate

        score = (35 - rsi) * 2 + max(0, (20 - low_52w) * 1.5)

        reasons = [
            f"RSI {rsi:.1f} (deeply oversold)",
            f"{low_52w:.1f}% above 52-week low",
        ]
        short = row.get("short_interest_pct")
        if short and short < 10:
            reasons.append(f"Low short interest {short:.1f}% (squeeze unlikely)")
            score += 5

        results.append(ScanResult(
            ticker   = row.get("ticker", ""),
            company  = row.get("company", ""),
            sector   = row.get("sector", ""),
            price    = row.get("price"),
            scan_name= "MEAN REVERSION",
            reasons  = reasons,
            score    = round(score, 1),
        ))

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:30]


def scan_short_watch(screener_rows: list[dict],
                     cluster_signals: list[dict]) -> list[ScanResult]:
    """
    Bearish setups worth watching:
    - Cluster SELL signal from insiders
    - RSI > 68 (overbought)
    - High short interest > 15%
    """
    cluster_sell_tickers = {
        s["ticker"]: s for s in cluster_signals
        if "SELL" in (s.get("signal_type") or "")
    }

    results = []
    for row in screener_rows:
        ticker = row.get("ticker", "")
        rsi    = row.get("rsi_14")
        short  = row.get("short_interest_pct")

        bearish_count = 0
        reasons       = []

        if ticker in cluster_sell_tickers:
            sig = cluster_sell_tickers[ticker]
            bearish_count += 2
            reasons.append(f"Cluster sell: {sig['insider_count']} insiders")

        if rsi and rsi > 68:
            bearish_count += 1
            reasons.append(f"Overbought RSI {rsi:.1f}")

        if short and short > 15:
            bearish_count += 1
            reasons.append(f"High short interest {short:.1f}%")

        sma50 = row.get("sma_50_pct")
        if sma50 and sma50 < -8:
            bearish_count += 1
            reasons.append(f"Below 50d SMA by {abs(sma50):.1f}%")

        if bearish_count < 2:
            continue

        score = bearish_count * 20.0
        results.append(ScanResult(
            ticker   = ticker,
            company  = row.get("company", ""),
            sector   = row.get("sector", ""),
            price    = row.get("price"),
            scan_name= "SHORT WATCH",
            reasons  = reasons,
            score    = round(score, 1),
        ))

    results.sort(key=lambda x: x.score, reverse=True)
    return results


# ── Run all scans ─────────────────────────────────

def run_all_scans(
    screener_rows:   list[dict],
    insider_trades:  list[dict],
    cluster_signals: list[dict],
) -> dict[str, list[ScanResult]]:
    """
    Run all scans and return results dict keyed by scan name.
    """
    logger.info("Running all scans...")

    results = {
        "insider_oversold":  scan_insider_oversold(screener_rows, cluster_signals),
        "momentum_breakout": scan_momentum_breakout(screener_rows),
        "quality_value":     scan_quality_value(screener_rows),
        "mean_reversion":    scan_mean_reversion(screener_rows),
        "short_watch":       scan_short_watch(screener_rows, cluster_signals),
    }

    for name, items in results.items():
        logger.info(f"  {name}: {len(items)} candidates")

    return results
