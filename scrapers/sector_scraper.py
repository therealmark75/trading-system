"""
Sector Relative Strength scraper.

Fetches 30 days of OHLC for each S&P 500 sector ETF via yfinance,
computes 7-day and 30-day returns, ranks sectors, and writes a
sector_strength_score (0-100) to the sector_performance table.

Runs daily at 06:00 ET, before signal generation, so fresh sector
data is available when composite scores are computed.
"""
import logging
import math
import sqlite3
import time
from datetime import datetime

import yfinance as yf

from config.constants import DATABASE_PATH

logger = logging.getLogger(__name__)

# FinViz sector name → sector ETF symbol
SECTOR_ETF_MAP = {
    "Technology":             "XLK",
    "Financial":              "XLF",
    "Healthcare":             "XLV",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Industrials":            "XLI",
    "Energy":                 "XLE",
    "Utilities":              "XLU",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Communication Services": "XLC",
}

# Rank (1=best) → sector_strength_score (0-100)
_RANK_TO_SCORE = {
    1: 100, 2: 90, 3: 80,
    4: 70, 5: 60, 6: 50, 7: 40,
    8: 30, 9: 20, 10: 10, 11: 0,
}


def ensure_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_performance (
            sector                TEXT NOT NULL,
            date                  TEXT NOT NULL,
            etf_symbol            TEXT,
            return_7d             REAL,
            return_30d            REAL,
            rank_7d               INTEGER,
            sector_strength_score REAL,
            calculated_at         TEXT,
            PRIMARY KEY (sector, date)
        )
    """)
    conn.commit()
    conn.close()


def _safe_float(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _fetch_etf_returns(etf: str, days: int = 30):
    """Fetch recent OHLC and return (return_7d, return_30d) or (None, None)."""
    try:
        ticker = yf.Ticker(etf)
        df = ticker.history(period=f"{days + 10}d", interval="1d")
        if df.empty or len(df) < 6:
            logger.warning(f"[Sector] {etf}: insufficient data ({len(df)} rows)")
            return None, None

        closes = df["Close"].dropna().tolist()
        latest = closes[-1]

        ret_7d  = _safe_float((latest - closes[-6]) / closes[-6] * 100) if len(closes) >= 6 else None
        ret_30d = _safe_float((latest - closes[0])  / closes[0]  * 100) if len(closes) >= 2 else None

        return ret_7d, ret_30d
    except Exception as e:
        logger.error(f"[Sector] {etf}: fetch failed — {e}")
        return None, None


def scrape_sector_performance(db_path: str = DATABASE_PATH) -> list:
    """
    Fetch sector ETF returns, rank, score, and upsert into sector_performance.
    Returns list of result dicts (one per sector).
    """
    ensure_table(db_path)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    now   = datetime.utcnow().isoformat()

    # Fetch returns for all sector ETFs
    raw = {}
    for sector, etf in SECTOR_ETF_MAP.items():
        logger.info(f"[Sector] Fetching {etf} for {sector}")
        ret_7d, ret_30d = _fetch_etf_returns(etf)
        raw[sector] = {"etf": etf, "return_7d": ret_7d, "return_30d": ret_30d}
        time.sleep(0.4)

    # Rank by 7-day return (best = rank 1). Sectors with no data go to end.
    scored = [(s, d) for s, d in raw.items() if d["return_7d"] is not None]
    missing = [(s, d) for s, d in raw.items() if d["return_7d"] is None]

    scored.sort(key=lambda x: x[1]["return_7d"], reverse=True)

    results = []
    rank = 1
    for sector, data in scored:
        ss = _RANK_TO_SCORE.get(rank, 50)
        results.append({
            "sector":                sector,
            "date":                  today,
            "etf_symbol":            data["etf"],
            "return_7d":             data["return_7d"],
            "return_30d":            data["return_30d"],
            "rank_7d":               rank,
            "sector_strength_score": ss,
            "calculated_at":         now,
        })
        logger.info(
            f"[Sector] {sector:24} ETF={data['etf']:4}  "
            f"7d={data['return_7d']:+.2f}%  "
            f"30d={data['return_30d']:+.2f}%  "
            f"rank={rank}  score={ss}"
        )
        rank += 1

    # Neutral score for any sectors that had no data
    for sector, data in missing:
        results.append({
            "sector":                sector,
            "date":                  today,
            "etf_symbol":            data["etf"],
            "return_7d":             None,
            "return_30d":            None,
            "rank_7d":               None,
            "sector_strength_score": 50.0,  # neutral
            "calculated_at":         now,
        })
        logger.warning(f"[Sector] {sector}: no data — assigned neutral score 50")

    # Upsert into DB
    conn = sqlite3.connect(db_path)
    conn.executemany("""
        INSERT OR REPLACE INTO sector_performance
            (sector, date, etf_symbol, return_7d, return_30d,
             rank_7d, sector_strength_score, calculated_at)
        VALUES (:sector, :date, :etf_symbol, :return_7d, :return_30d,
                :rank_7d, :sector_strength_score, :calculated_at)
    """, results)
    conn.commit()
    conn.close()

    logger.info(f"[Sector] Done — {len(results)} sectors written for {today}")
    return results


def get_sector_strength_map(db_path: str = DATABASE_PATH) -> dict:
    """
    Return {sector_name: sector_strength_score} for the latest available date.
    Defaults to 50 (neutral) for any sector not in the table.
    """
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT sector, sector_strength_score
        FROM sector_performance
        WHERE date = (SELECT MAX(date) FROM sector_performance)
    """).fetchall()
    conn.close()
    result = {r[0]: r[1] for r in rows}
    # Fill any missing sectors with neutral
    for sector in SECTOR_ETF_MAP:
        result.setdefault(sector, 50.0)
    return result
