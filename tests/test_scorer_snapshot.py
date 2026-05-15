"""
Behaviour-preservation snapshot test for score_all_tickers.

Runs a fixed synthetic dataset through score_all_tickers and asserts that
composite_score_raw, composite_score, rating, legal_penalty, and
sector_modifier_applied match the values captured before the Phase 2b-i
substrate refactor (screener_rows -> ticker_data_rows rename + kwargs).

Catches:
  - ticker_data_rows rename silently breaking the loop (e.g. by introducing
    a NameError or empty-input path at the wrong level)
  - A weight default changing and shifting composite scores
  - Legal penalty being applied twice (or not at all)
  - Sector modifier formula drift (wrong multiplier, wrong denominator)
  - New or changed or {} defaulting behaviour for the four Phase 2b-ii kwargs

Ignores:
  - Changes to scorer sub-functions not reflected in these 5 output fields
  - The order of tickers within the same composite_score band
  - Scoring logic for the Phase 2b-ii enrichment kwargs (earnings_map,
    financials_map, inst_own_map, analyst_mom_map) — those are None here
    and expected to produce no-op 50.0/0 contributions
"""
import pytest
from signals.scorer import score_all_tickers


# ── Synthetic input dataset ───────────────────────────────────────────────────

_SYNTHETIC_ROWS = [
    # Profile: STRONG_BUY — high momentum, high quality, strong insider buying
    {"ticker": "SB01", "company": "StrongBuy Corp", "sector": "Technology",
     "price": 50.0, "change_pct": 2.5, "sma_50_pct": 12.0, "sma_200_pct": 15.0,
     "rsi_14": 62.0, "rel_volume": 1.8, "roe": 35.0, "eps_growth_this_yr": 40.0,
     "eps_growth_next_yr": 25.0, "short_interest_pct": 3.0, "analyst_recom": 1.4,
     "low_52w_pct": 40.0, "high_52w_pct": -1.0},

    # Profile: BUY — solid fundamentals, no insider boost
    {"ticker": "BU02", "company": "Buy Corp", "sector": "Technology",
     "price": 45.0, "change_pct": 1.5, "sma_50_pct": 7.0, "sma_200_pct": 8.0,
     "rsi_14": 58.0, "rel_volume": 1.3, "roe": 22.0, "eps_growth_this_yr": 20.0,
     "eps_growth_next_yr": 15.0, "short_interest_pct": 5.0, "analyst_recom": 2.0,
     "low_52w_pct": 30.0, "high_52w_pct": -8.0},

    # Profile: STRONG_HOLD — neutral momentum, decent quality
    {"ticker": "SH03", "company": "StrongHold Inc", "sector": "Healthcare",
     "price": 40.0, "change_pct": 0.5, "sma_50_pct": 3.0, "sma_200_pct": 4.0,
     "rsi_14": 52.0, "rel_volume": 1.0, "roe": 12.0, "eps_growth_this_yr": 8.0,
     "eps_growth_next_yr": 5.0, "short_interest_pct": 8.0, "analyst_recom": 2.8,
     "low_52w_pct": 20.0, "high_52w_pct": -15.0},

    # Profile: HOLD — oversold RSI + near 52w low trips mean reversion path
    {"ticker": "HO04", "company": "Hold Ltd", "sector": "Healthcare",
     "price": 35.0, "change_pct": -0.5, "sma_50_pct": -8.0, "sma_200_pct": -5.0,
     "rsi_14": 22.0, "rel_volume": 0.9, "roe": 5.0, "eps_growth_this_yr": 2.0,
     "eps_growth_next_yr": 3.0, "short_interest_pct": 12.0, "analyst_recom": 3.0,
     "low_52w_pct": 2.0, "high_52w_pct": -40.0},

    # Profile: SELL (weak-side) — composite ~33, insider neutral (no data → 50)
    {"ticker": "WH05", "company": "WeakHold SA", "sector": "Energy",
     "price": 30.0, "change_pct": -1.0, "sma_50_pct": -6.0, "sma_200_pct": -8.0,
     "rsi_14": 38.0, "rel_volume": 0.8, "roe": 2.0, "eps_growth_this_yr": -5.0,
     "eps_growth_next_yr": -2.0, "short_interest_pct": 18.0, "analyst_recom": 3.6,
     "low_52w_pct": 15.0, "high_52w_pct": -25.0},

    # Profile: SELL — weak fundamentals, composite ~24
    {"ticker": "SE06", "company": "Sell Plc", "sector": "Energy",
     "price": 25.0, "change_pct": -2.0, "sma_50_pct": -12.0, "sma_200_pct": -15.0,
     "rsi_14": 35.0, "rel_volume": 0.7, "roe": -5.0, "eps_growth_this_yr": -25.0,
     "eps_growth_next_yr": -15.0, "short_interest_pct": 25.0, "analyst_recom": 4.2,
     "low_52w_pct": 8.0, "high_52w_pct": -35.0},

    # Profile: STRONG_SELL — collapsed fundamentals, RSI mid-range + far from 52w low
    # so reversion_score stays low (~15) and does not trip the reversion >= 75 HOLD branch.
    # insider_score = 0 (CEO+CFO+Chairman all selling).
    # Phase 2b-ii: enrichment maps drive earnings/piotroski/analyst_mom to worst tier;
    # Altman Z < 0 (market_cap "240M" + distressed balance sheet) → altman_penalty = -60.
    # Combined: composite ~19 before Altman, clamped to 0 after -60 penalty → STRONG_SELL.
    {"ticker": "SS07", "company": "StrongSell Corp", "sector": "Financials",
     "price": 20.0, "change_pct": -2.5, "sma_50_pct": -7.0, "sma_200_pct": -20.0,
     "rsi_14": 58.0, "rel_volume": 0.7, "roe": -25.0, "eps_growth_this_yr": -45.0,
     "eps_growth_next_yr": -30.0, "short_interest_pct": 35.0, "analyst_recom": 4.8,
     "low_52w_pct": 45.0, "high_52w_pct": -50.0, "market_cap": "240M"},

    # Profile: legal MINOR row — composite is reduced by -5 penalty
    {"ticker": "LM08", "company": "Legal Minor Inc", "sector": "Technology",
     "price": 42.0, "change_pct": 0.8, "sma_50_pct": 5.0, "sma_200_pct": 6.0,
     "rsi_14": 55.0, "rel_volume": 1.1, "roe": 15.0, "eps_growth_this_yr": 12.0,
     "eps_growth_next_yr": 8.0, "short_interest_pct": 7.0, "analyst_recom": 2.5,
     "low_52w_pct": 25.0, "high_52w_pct": -12.0},

    # Profile: legal NONE row (explicit entry, zero penalty)
    {"ticker": "LN09", "company": "Legal None Corp", "sector": "Healthcare",
     "price": 38.0, "change_pct": 0.3, "sma_50_pct": 2.0, "sma_200_pct": 1.0,
     "rsi_14": 50.0, "rel_volume": 1.0, "roe": 10.0, "eps_growth_this_yr": 6.0,
     "eps_growth_next_yr": 4.0, "short_interest_pct": 9.0, "analyst_recom": 2.7,
     "low_52w_pct": 22.0, "high_52w_pct": -18.0},

    # Profile: sector HIGH strength (60.0) — positive modifier applied
    {"ticker": "SH10", "company": "SectorHigh Ltd", "sector": "HighSector",
     "price": 48.0, "change_pct": 1.2, "sma_50_pct": 6.0, "sma_200_pct": 7.0,
     "rsi_14": 58.0, "rel_volume": 1.2, "roe": 20.0, "eps_growth_this_yr": 18.0,
     "eps_growth_next_yr": 12.0, "short_interest_pct": 6.0, "analyst_recom": 2.1,
     "low_52w_pct": 28.0, "high_52w_pct": -10.0},

    # Profile: sector LOW strength (40.0) — negative modifier applied
    {"ticker": "SL11", "company": "SectorLow SA", "sector": "LowSector",
     "price": 42.0, "change_pct": 0.8, "sma_50_pct": 5.5, "sma_200_pct": 6.5,
     "rsi_14": 57.0, "rel_volume": 1.15, "roe": 19.0, "eps_growth_this_yr": 17.0,
     "eps_growth_next_yr": 11.0, "short_interest_pct": 6.5, "analyst_recom": 2.2,
     "low_52w_pct": 27.0, "high_52w_pct": -11.0},

    # Profile: sector NEUTRAL (50.0) — modifier is zero
    {"ticker": "SN12", "company": "SectorNeutral Corp", "sector": "NeutralSector",
     "price": 44.0, "change_pct": 0.9, "sma_50_pct": 5.8, "sma_200_pct": 6.8,
     "rsi_14": 57.5, "rel_volume": 1.18, "roe": 19.5, "eps_growth_this_yr": 17.5,
     "eps_growth_next_yr": 11.5, "short_interest_pct": 6.2, "analyst_recom": 2.15,
     "low_52w_pct": 27.5, "high_52w_pct": -10.5},

    # Profile: all-NULL momentum inputs — P5: each NULL contributes 50 neutral
    {"ticker": "NL13", "company": "Nulls Inc", "sector": "NeutralSector",
     "price": 30.0, "change_pct": None, "sma_50_pct": None, "sma_200_pct": None,
     "rsi_14": None, "rel_volume": None, "roe": None, "eps_growth_this_yr": None,
     "eps_growth_next_yr": None, "short_interest_pct": None, "analyst_recom": None,
     "low_52w_pct": None, "high_52w_pct": None},

    # Profile: WEAK_HOLD — moderate weakness, CFO sell drives insider_score to 34
    # (below the <= 35 threshold); composite ~32; reversion ~15 (far from 52w low,
    # RSI mid-range) so neither HOLD nor STRONG_SELL branch fires.
    {"ticker": "WH14", "company": "WeakHold Corp", "sector": "Industrials",
     "price": 28.0, "change_pct": -1.2, "sma_50_pct": -6.0, "sma_200_pct": -12.0,
     "rsi_14": 50.0, "rel_volume": 0.9, "roe": 1.0, "eps_growth_this_yr": -8.0,
     "eps_growth_next_yr": -5.0, "short_interest_pct": 12.0, "analyst_recom": 3.8,
     "low_52w_pct": 30.0, "high_52w_pct": -20.0},
]

_SYNTHETIC_INSIDERS = [
    # Strong buying for SB01
    {"ticker": "SB01", "transaction_date": "2026-04-25", "transaction_type": "Buy",  "insider_title": "CEO"},
    {"ticker": "SB01", "transaction_date": "2026-04-20", "transaction_type": "Buy",  "insider_title": "CFO"},
    {"ticker": "SB01", "transaction_date": "2026-04-15", "transaction_type": "Buy",  "insider_title": "Director"},
    # Heavy selling for SS07
    {"ticker": "SS07", "transaction_date": "2026-04-28", "transaction_type": "Sale", "insider_title": "CEO"},
    {"ticker": "SS07", "transaction_date": "2026-04-22", "transaction_type": "Sale", "insider_title": "CFO"},
    {"ticker": "SS07", "transaction_date": "2026-04-18", "transaction_type": "Sale", "insider_title": "Chairman"},
    # CFO selling for WH14 — drives insider_score to 34 (weight 8: net=-8, mapped=34)
    {"ticker": "WH14", "transaction_date": "2026-04-20", "transaction_type": "Sale", "insider_title": "CFO"},
]

_SYNTHETIC_LEGAL_MAP = {
    "LM08": {"penalty": -5, "risk_level": "MINOR", "risk_label": "Minor", "risk_color": "yellow"},
    "LN09": {"penalty":  0, "risk_level": "NONE",  "risk_label": "None",  "risk_color": "green"},
}

_SYNTHETIC_SECTOR_MAP = {
    "Technology":    50.0,
    "Healthcare":    50.0,
    "Energy":        50.0,
    "Financials":    50.0,
    "HighSector":    60.0,
    "LowSector":     40.0,
    "NeutralSector": 50.0,
}

# ── Phase 2b-ii enrichment maps ───────────────────────────────────────────────
# Only SS07 has enrichment data — all other tickers remain P5 neutral (50.0/0).
# SS07: 4 quarters of extreme misses (earnings → 0), Piotroski F=2 (→ 20),
#        analyst net=-4 (→ 20), Altman Z≈-0.25 (market_cap "240M") → penalty -60.
# Combined effect: raw composite ~19 before Altman, clamped to 0 after -60 → STRONG_SELL.

_SYNTHETIC_EARNINGS_MAP = {
    "SS07": [
        {"fiscal_quarter": "2025Q4", "eps_actual": -0.50, "eps_estimate": 0.10, "surprise_pct": -600.0, "reported_at": "2026-02-01"},
        {"fiscal_quarter": "2025Q3", "eps_actual": -0.30, "eps_estimate": 0.05, "surprise_pct": -700.0, "reported_at": "2025-11-01"},
        {"fiscal_quarter": "2025Q2", "eps_actual": -0.10, "eps_estimate": 0.08, "surprise_pct": -225.0, "reported_at": "2025-08-01"},
        {"fiscal_quarter": "2025Q1", "eps_actual": -0.05, "eps_estimate": 0.06, "surprise_pct": -183.3, "reported_at": "2025-05-01"},
    ],
}

_SYNTHETIC_FINANCIALS_MAP = {
    "SS07": {
        # 2 fiscal years required so Piotroski Lock 1 (< 2 years) does not trigger.
        # Y0=2024: F=2 (only F4 OCF>NI and F9 asset-turnover pass); → piotroski_score=20.
        # Altman (Y0=2024): Z≈-0.25 (distress zone) → altman_penalty=-60.
        "INCOME": {
            "2024": {"NetIncome": -50_000_000, "TotalRevenue": 200_000_000, "GrossProfit": 20_000_000, "EBIT": -45_000_000},
            "2023": {"NetIncome": -10_000_000, "TotalRevenue": 220_000_000, "GrossProfit": 35_000_000, "EBIT":  -5_000_000},
        },
        "BALANCE": {
            "2024": {
                "TotalAssets":                            100_000_000,
                "TotalLiabilitiesNetMinorityInterest":    180_000_000,
                "LongTermDebt":                           120_000_000,
                "CurrentAssets":                           15_000_000,
                "CurrentLiabilities":                      40_000_000,
                "WorkingCapital":                         -25_000_000,
                "RetainedEarnings":                       -90_000_000,
                "OrdinarySharesNumber":                    12_000_000,
            },
            "2023": {
                "TotalAssets":                            130_000_000,
                "TotalLiabilitiesNetMinorityInterest":    150_000_000,
                "LongTermDebt":                           100_000_000,
                "CurrentAssets":                           25_000_000,
                "CurrentLiabilities":                      35_000_000,
                "WorkingCapital":                         -10_000_000,
                "RetainedEarnings":                       -40_000_000,
                "OrdinarySharesNumber":                    10_000_000,
            },
        },
        "CASHFLOW": {
            "2024": {"OperatingCashFlow": -20_000_000},
            "2023": {"OperatingCashFlow":  -5_000_000},
        },
    },
}

_SYNTHETIC_INST_OWN_MAP = {}   # No tickers — all P5 neutral (inst_own_score=50.0)

_SYNTHETIC_ANALYST_MOM_MAP = {
    "SS07": {"upgrades_90d": 0, "downgrades_90d": 4, "net_momentum": -4},
}

# ── Snapshot — updated 2026-05-14 (Phase 2b-ii: composite rebalance + enrichment maps) ──
# v0.13.0: 4 new enrichment scorers (earnings, piotroski, inst_own, analyst_mom) wired in.
# Composite weights rebalanced 1.10 → 1.60-sum; Altman applied as additive penalty.
# SS07 now exercises all 4 enrichment paths: earnings=0, piotroski=20, altman_pen=-60,
#       analyst_mom=20 → composite clamped to 0.0 → STRONG_SELL ✓
# SN12/SL11 dropped from BUY → STRONG_HOLD (neutral pull from new 50.0 components;
#             they remain sector-modifier coverage tickers, not tier-specific).
# Re-generate with: python -c "from tests.test_scorer_snapshot import _SYNTHETIC_ROWS, ..."
# Do NOT modify this to match broken output — fix the refactor instead.
EXPECTED_SNAPSHOT = {
    "SB01": {"composite_score_raw": 74.7, "composite_score": 74.7, "rating": "STRONG_BUY",  "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "BU02": {"composite_score_raw": 64.2, "composite_score": 64.2, "rating": "BUY",         "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "SH10": {"composite_score_raw": 62.2, "composite_score": 63.2, "rating": "BUY",         "legal_penalty": 0,  "sector_modifier_applied":  0.93},
    "SN12": {"composite_score_raw": 61.6, "composite_score": 61.6, "rating": "STRONG_HOLD", "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "SL11": {"composite_score_raw": 61.6, "composite_score": 60.7, "rating": "STRONG_HOLD", "legal_penalty": 0,  "sector_modifier_applied": -0.92},
    "SH03": {"composite_score_raw": 57.2, "composite_score": 57.2, "rating": "STRONG_HOLD", "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "LN09": {"composite_score_raw": 56.1, "composite_score": 56.1, "rating": "STRONG_HOLD", "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "LM08": {"composite_score_raw": 53.8, "composite_score": 53.8, "rating": "STRONG_HOLD", "legal_penalty": -5, "sector_modifier_applied":  0.0},
    "NL13": {"composite_score_raw": 50.0, "composite_score": 50.0, "rating": "STRONG_HOLD", "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "HO04": {"composite_score_raw": 45.3, "composite_score": 45.3, "rating": "HOLD",        "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "WH05": {"composite_score_raw": 38.6, "composite_score": 38.6, "rating": "SELL",        "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "WH14": {"composite_score_raw": 37.6, "composite_score": 37.6, "rating": "WEAK_HOLD",   "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "SE06": {"composite_score_raw": 32.5, "composite_score": 32.5, "rating": "SELL",        "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "SS07": {"composite_score_raw":  0.0, "composite_score":  0.0, "rating": "STRONG_SELL", "legal_penalty": 0,  "sector_modifier_applied":  0.0},
}


# ── Test ──────────────────────────────────────────────────────────────────────

def test_score_all_tickers_snapshot():
    """
    Behaviour-preservation: score_all_tickers on SYNTHETIC_ROWS must produce
    composite_score_raw, composite_score, rating, legal_penalty, and
    sector_modifier_applied that exactly match EXPECTED_SNAPSHOT.

    Catches: compute_composite weight defaults changing, legal/Altman penalty applied
             twice or not at all, sector modifier formula drift, enrichment scorers
             producing wrong outputs (SS07 exercises all 4 new enrichment paths).

    Ignores: fields not in the snapshot (flags, sub-scores, company, sector),
             tickers not in the synthetic dataset (12 tickers use P5 neutral enrichment).
    """
    signals = score_all_tickers(
        _SYNTHETIC_ROWS, _SYNTHETIC_INSIDERS,
        legal_risk_map=_SYNTHETIC_LEGAL_MAP,
        sector_strength_map=_SYNTHETIC_SECTOR_MAP,
        earnings_map=_SYNTHETIC_EARNINGS_MAP,
        financials_map=_SYNTHETIC_FINANCIALS_MAP,
        inst_own_map=_SYNTHETIC_INST_OWN_MAP,
        analyst_mom_map=_SYNTHETIC_ANALYST_MOM_MAP,
    )
    actual = {sig.ticker: sig for sig in signals}

    missing = set(EXPECTED_SNAPSHOT) - set(actual)
    assert not missing, f"Tickers missing from scorer output: {missing}"

    mismatches = []
    for ticker, expected in EXPECTED_SNAPSHOT.items():
        sig = actual[ticker]
        for field, exp_val in expected.items():
            got = getattr(sig, field)
            if isinstance(exp_val, float):
                if round(got, 1) != round(exp_val, 1):
                    mismatches.append(
                        f"{ticker}.{field}: expected {exp_val}, got {got}"
                    )
            else:
                if got != exp_val:
                    mismatches.append(
                        f"{ticker}.{field}: expected {exp_val!r}, got {got!r}"
                    )

    assert not mismatches, (
        f"{len(mismatches)} snapshot mismatches — refactor is not behaviour-preserving:\n"
        + "\n".join(mismatches)
    )
