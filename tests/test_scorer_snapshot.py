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

    # Profile: HOLD (via strong reversion) — heavy insider selling lifts reversion
    # score enough to trip the reversion >= 75 HOLD branch before STRONG_SELL
    {"ticker": "SS07", "company": "StrongSell Corp", "sector": "Financials",
     "price": 20.0, "change_pct": -3.5, "sma_50_pct": -18.0, "sma_200_pct": -22.0,
     "rsi_14": 28.0, "rel_volume": 2.2, "roe": -20.0, "eps_growth_this_yr": -40.0,
     "eps_growth_next_yr": -30.0, "short_interest_pct": 35.0, "analyst_recom": 4.8,
     "low_52w_pct": 5.0, "high_52w_pct": -50.0},

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

# ── Snapshot — captured 2026-05-14 before Phase 2b-i rename commit ────────────
# Re-generate with: python -c "from signals.scorer import score_all_tickers; ..."
# Do NOT modify this to match broken output — fix the refactor instead.
EXPECTED_SNAPSHOT = {
    "SB01": {"composite_score_raw": 88.6, "composite_score": 88.6, "rating": "STRONG_BUY",  "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "BU02": {"composite_score_raw": 70.6, "composite_score": 70.6, "rating": "BUY",         "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "SH10": {"composite_score_raw": 67.8, "composite_score": 68.8, "rating": "BUY",         "legal_penalty": 0,  "sector_modifier_applied":  1.02},
    "SN12": {"composite_score_raw": 66.9, "composite_score": 66.9, "rating": "BUY",         "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "SL11": {"composite_score_raw": 66.9, "composite_score": 65.9, "rating": "BUY",         "legal_penalty": 0,  "sector_modifier_applied": -1.0},
    "SH03": {"composite_score_raw": 60.5, "composite_score": 60.5, "rating": "STRONG_HOLD", "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "LN09": {"composite_score_raw": 58.9, "composite_score": 58.9, "rating": "STRONG_HOLD", "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "LM08": {"composite_score_raw": 57.8, "composite_score": 57.8, "rating": "STRONG_HOLD", "legal_penalty": -5, "sector_modifier_applied":  0.0},
    "NL13": {"composite_score_raw": 50.0, "composite_score": 50.0, "rating": "STRONG_HOLD", "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "HO04": {"composite_score_raw": 43.1, "composite_score": 43.1, "rating": "HOLD",        "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "WH05": {"composite_score_raw": 33.4, "composite_score": 33.4, "rating": "SELL",        "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "SE06": {"composite_score_raw": 24.5, "composite_score": 24.5, "rating": "SELL",        "legal_penalty": 0,  "sector_modifier_applied":  0.0},
    "SS07": {"composite_score_raw": 12.1, "composite_score": 12.1, "rating": "HOLD",        "legal_penalty": 0,  "sector_modifier_applied":  0.0},
}


# ── Test ──────────────────────────────────────────────────────────────────────

def test_score_all_tickers_snapshot():
    """
    Behaviour-preservation: score_all_tickers on SYNTHETIC_ROWS must produce
    composite_score_raw, composite_score, rating, legal_penalty, and
    sector_modifier_applied that exactly match EXPECTED_SNAPSHOT.

    Catches: screener_rows->ticker_data_rows rename breaking the iteration path,
             compute_composite weight defaults changing, legal penalty applied
             twice or not at all, sector modifier formula drift, the four new
             Phase 2b-ii no-op kwargs accidentally affecting output.

    Ignores: fields not in the snapshot (flags, sub-scores, company, sector),
             tickers not in the synthetic dataset, changes to Phase 2b-ii scorer
             functions (they are not yet wired in).
    """
    signals = score_all_tickers(
        _SYNTHETIC_ROWS, _SYNTHETIC_INSIDERS,
        legal_risk_map=_SYNTHETIC_LEGAL_MAP,
        sector_strength_map=_SYNTHETIC_SECTOR_MAP,
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
