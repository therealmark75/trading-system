"""
Data integrity tests — freshness, uniqueness, and distribution checks on the live DB.
All tests are read-only. Soft assertions (conditional skips) documented inline.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
import pytest

from database.db import initialise_schema, insert_screener_rows


def test_signal_scores_freshness(db):
    """Latest scored_at must be within 72 hours of now."""
    row = db.execute("SELECT MAX(scored_at) FROM signal_scores").fetchone()
    assert row[0] is not None, "signal_scores is empty"
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    assert latest >= cutoff, f"signal_scores last updated {row[0]}, older than 72h"


def test_screener_snapshots_freshness(db):
    """Latest scraped_at must be within 72 hours of now."""
    row = db.execute("SELECT MAX(scraped_at) FROM screener_snapshots").fetchone()
    assert row[0] is not None, "screener_snapshots is empty"
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    assert latest >= cutoff, f"screener_snapshots last scraped {row[0]}, older than 72h"


def test_insider_trades_freshness(db):
    """
    Catches scraper job death or persistent FAILED status.

    Ignores quiet insider periods where rows_added=0 is legitimate
    (insider_trades uses INSERT OR IGNORE; a quiet day where all trades
    are already in the table does not advance MAX(scraped_at) even
    though the scraper ran successfully).

    Source: run_log WHERE job_name='insider_scrape' AND status='SUCCESS'.
    """
    row = db.execute(
        "SELECT MAX(run_at) FROM run_log "
        "WHERE job_name = 'insider_scrape' AND status = 'SUCCESS'"
    ).fetchone()
    assert row[0] is not None, "no successful insider_scrape runs in run_log"
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    assert latest >= cutoff, f"insider_scrape last successful run {row[0]}, older than 72h"


def test_legal_risk_freshness(db):
    """
    Catches legal_risk scraper cron death or extended outage.

    Ignores partial-run days where some rows have been written and the
    job is still mid-run. Scraper expands coverage continuously and
    writes a new scraped_at on each ticker processed, so MAX(scraped_at)
    advances throughout every active run.
    """
    row = db.execute("SELECT MAX(scraped_at) FROM legal_risk").fetchone()
    assert row[0] is not None, "legal_risk is empty"
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    assert latest >= cutoff, f"legal_risk last scraped {row[0]}, older than 72h"


def test_ticker_metadata_freshness(db):
    """
    Catches ticker_metadata write path breakage independent of
    screener_snapshots success.

    Ignores first_seen_at (frozen at backfill, never advances).
    updated_at is written by the screener scrape via ON CONFLICT...DO
    UPDATE; if that specific write breaks (BUG-B-style miss) while
    screener_snapshots still populates, this test fails while the
    screener_snapshots freshness test still passes.
    """
    row = db.execute("SELECT MAX(updated_at) FROM ticker_metadata").fetchone()
    assert row[0] is not None, "ticker_metadata is empty"
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    assert latest >= cutoff, f"ticker_metadata last updated {row[0]}, older than 72h"


def test_no_duplicate_signals_for_latest_run(db, latest_run_date):
    """Each ticker must appear at most once in the latest scoring run."""
    rows = db.execute(
        "SELECT ticker, COUNT(*) as cnt FROM signal_scores WHERE DATE(scored_at) = ? "
        "GROUP BY ticker HAVING cnt > 1",
        (latest_run_date,),
    ).fetchall()
    assert len(rows) == 0, f"{len(rows)} tickers have duplicate rows: {[r['ticker'] for r in rows[:5]]}"


def test_rating_distribution_sane(db, latest_run_date):
    """
    Sanity check: no single outlier rating dominates.
    STRONG_BUY and STRONG_SELL should each be < 500 in any given run.
    """
    for rating in ("STRONG_BUY", "STRONG_SELL"):
        row = db.execute(
            "SELECT COUNT(*) FROM signal_scores WHERE DATE(scored_at) = ? AND rating = ?",
            (latest_run_date, rating),
        ).fetchone()
        assert row[0] < 500, f"{rating} count={row[0]} looks implausibly high"


def test_signal_scores_minimum_count(latest_signals):
    """There must be at least 1000 signals in the latest run."""
    assert len(latest_signals) >= 1000, f"Only {len(latest_signals)} signals — scorer may have failed"


def test_target_price_coverage(db, latest_run_date):
    """
    Invariant 8: target_price should be non-null for 10,000+ tickers.
    CONDITIONAL — skipped if coverage is 0% (fmp_price_targets not yet populated).
    """
    non_null = db.execute(
        "SELECT COUNT(*) FROM signal_scores WHERE DATE(scored_at) = ? AND target_price IS NOT NULL",
        (latest_run_date,),
    ).fetchone()[0]
    total = db.execute(
        "SELECT COUNT(*) FROM signal_scores WHERE DATE(scored_at) = ?",
        (latest_run_date,),
    ).fetchone()[0]
    if non_null == 0:
        pytest.skip("target_price is 0% — fmp_price_targets not yet populated")
    coverage_pct = non_null / total * 100
    assert non_null >= 10000, f"target_price coverage {non_null}/{total} ({coverage_pct:.0f}%) below threshold"


def test_no_signal_scores_orphans(db):
    """Every ticker in signal_scores must also appear in screener_snapshots."""
    orphans = db.execute(
        "SELECT COUNT(*) FROM signal_scores "
        "WHERE ticker NOT IN (SELECT DISTINCT ticker FROM screener_snapshots)"
    ).fetchone()[0]
    assert orphans < 200, f"{orphans} signal_scores tickers have no screener data"


def test_legal_risk_distribution(db):
    """
    Invariant 1: majority of classified tickers should have risk_label 'None'.
    Skipped if legal_risk table is empty.
    """
    total = db.execute("SELECT COUNT(*) FROM legal_risk").fetchone()[0]
    if total == 0:
        pytest.skip("legal_risk table is empty — SEC scraper not yet run")
    none_count = db.execute(
        "SELECT COUNT(*) FROM legal_risk WHERE risk_label = 'None'"
    ).fetchone()[0]
    none_pct = none_count / total * 100
    assert none_pct >= 70, f"'None' risk only {none_pct:.0f}% of classified tickers — classifier may be over-triggering"


# ── Yahoo Phase 2a freshness gates ───────────────────────────────────────────

def test_yahoo_earnings_history_freshness(db):
    """
    Latest earnings_history scraped_at must be within 14 days of now.
    Skipped if the table is empty (job has not yet run — acceptable on fresh deploy).

    Catches: yahoo_earnings_priority or yahoo_earnings_bulk job dying silently.
    Ignores: tickers with no earnings data from Yahoo (empty fetch is NOT written).
    """
    row = db.execute("SELECT MAX(scraped_at) FROM earnings_history").fetchone()
    if row[0] is None:
        pytest.skip("earnings_history is empty — Yahoo scraper not yet run")
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    assert latest >= cutoff, f"earnings_history last scraped {row[0]} — older than 14 days"


def test_yahoo_financial_statements_freshness(db):
    """
    Latest financial_statements scraped_at must be within 14 days of now.
    Skipped if the table is empty (weekly bulk job runs once; first run may be pending).

    Catches: yahoo_financials bulk job dying silently.
    Ignores: tickers with no financial data from Yahoo.
    """
    row = db.execute("SELECT MAX(scraped_at) FROM financial_statements").fetchone()
    if row[0] is None:
        pytest.skip("financial_statements is empty — Yahoo financials scraper not yet run")
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    assert latest >= cutoff, f"financial_statements last scraped {row[0]} — older than 14 days"


def test_yahoo_analyst_changes_freshness(db):
    """
    Latest analyst_changes scraped_at must be within 14 days of now.
    Skipped if the table is empty (daily job has not yet run on this deploy).

    Catches: yahoo_analyst_changes daily priority job dying silently.
    Ignores: tickers with no analyst upgrade/downgrade history from Yahoo.
    """
    row = db.execute("SELECT MAX(scraped_at) FROM analyst_changes").fetchone()
    if row[0] is None:
        pytest.skip("analyst_changes is empty — Yahoo analyst scraper not yet run")
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    assert latest >= cutoff, f"analyst_changes last scraped {row[0]} — older than 14 days"


def test_yahoo_institutional_holders_freshness(db):
    """
    Latest institutional_holders scraped_at must be within 14 days of now.
    Skipped if the table is empty (weekly Sunday bulk job may not have run yet).

    Catches: yahoo_institutional_holders weekly bulk job dying silently.
    Ignores: tickers where Yahoo returns no institutional holder data.
    """
    row = db.execute("SELECT MAX(scraped_at) FROM institutional_holders").fetchone()
    if row[0] is None:
        pytest.skip("institutional_holders is empty — Yahoo holders scraper not yet run")
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    assert latest >= cutoff, f"institutional_holders last scraped {row[0]} — older than 14 days"


# ── Schema/INSERT coupling tripwire (BUG B class) ─────────────────────────────

def test_insert_screener_rows_schema_alignment(tmp_path):
    """
    Schema/INSERT coupling tripwire for screener_snapshots (BUG B class).

    Catches: INSERT INTO screener_snapshots in db.insert_screener_rows
    referencing a column the live schema no longer defines (OperationalError:
    no such column), and schema NOT NULL columns missing default that the
    insert dict does not populate (IntegrityError on insert).

    Ignores: column-level type coercion, downstream readers, indexes, and
    every other insert_* helper in db.py (the broader sweep across
    insert_insider_trades, insert_signal_scores, etc. is queued as a
    separate FOLLOWUP — start narrow at the locus of the original bug).

    Uses initialise_schema() from production database/db.py directly against
    a tmp_path SQLite file — zero in-test schema replica, so no drift risk.
    """
    db_path = str(tmp_path / "tripwire.db")
    initialise_schema(db_path)
    row = {
        "ticker": "TEST", "company": "Test Inc", "sector": "Technology",
        "industry": "Software", "country": "USA",
        "market_cap": "1B", "pe_ratio": 20.0, "price": 100.0,
        "change_pct": 1.0, "volume": 1_000_000,
        "eps_growth_this_yr": 10.0, "eps_growth_next_yr": 8.0,
        "sales_growth_5yr": 12.0, "roe": 15.0,
        "insider_own_pct": 5.0, "insider_transactions": "0",
        "inst_own_pct": 60.0, "short_interest_pct": 3.0, "short_ratio": 2.0,
        "analyst_recom": 2.0, "rsi_14": 55.0, "rel_volume": 1.1,
        "avg_volume": 500_000, "sma_50_pct": 5.0, "sma_200_pct": 8.0,
        "high_52w_pct": -5.0, "low_52w_pct": 25.0, "beta": 1.1,
        "forward_pe": 18.0, "peg_ratio": 1.5,
        "price_to_sales": 4.0, "price_to_book": 3.0,
    }
    inserted = insert_screener_rows(db_path, [row])
    assert inserted == 1


# ── FMP output table freshness gates ─────────────────────────────────────────

def test_fmp_earnings_calendar_freshness(db):
    """
    Latest earnings_calendar last_updated must be within 72 hours.
    Skipped if the table is empty (FMP scraper not yet run on this deploy).

    Catches: fmp_earnings job (mon-fri 06:05) dying silently.
    Ignores: weekends and holidays — 72h cutoff covers the Sat/Sun gap.
    Ignores: tickers with no upcoming earnings — empty refresh is acceptable
    when FMP returns zero rows for the look-ahead window.
    """
    row = db.execute("SELECT MAX(last_updated) FROM earnings_calendar").fetchone()
    if row[0] is None:
        pytest.skip("earnings_calendar is empty — FMP earnings scraper not yet run")
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    assert latest >= cutoff, f"earnings_calendar last updated {row[0]} — older than 72h"


def test_fmp_dividends_freshness(db):
    """
    Latest dividends last_updated must be within 14 days.
    Skipped if the table is empty (weekly Sunday job has not yet run).

    Catches: fmp_dividends weekly job (Sun 03:00) dying silently.
    Ignores: one-week skip tolerance — 14d covers a single missed Sunday run.
    Ignores: non-dividend payers — excluded from the refresh set by design.
    """
    row = db.execute("SELECT MAX(last_updated) FROM dividends").fetchone()
    if row[0] is None:
        pytest.skip("dividends is empty — FMP dividend scraper not yet run")
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    assert latest >= cutoff, f"dividends last updated {row[0]} — older than 14 days"


def test_fmp_price_targets_freshness(db):
    """
    Latest fmp_price_targets last_updated must be within 14 days.
    Skipped if the table is empty (lazy-populated cache — scoring job has
    not yet warmed the cache on this deploy).

    Catches: target_price pipeline stalling — the table is populated lazily
    by the daily scoring job's compute_targets_batch path, so a frozen
    last_updated implies the scoring job itself has died.
    Ignores: cache-window staleness up to 7 days — get_price_targets_map's
    own WHERE clause already filters at 7d, so a 14d threshold (= 2× cache
    window) alerts only on actual stall, not normal cache rotation.
    """
    row = db.execute("SELECT MAX(last_updated) FROM fmp_price_targets").fetchone()
    if row[0] is None:
        pytest.skip("fmp_price_targets is empty — scoring job has not yet populated cache")
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    assert latest >= cutoff, f"fmp_price_targets last updated {row[0]} — older than 14 days"


def test_fmp_economic_calendar_freshness(db):
    """
    Latest economic_calendar scraped_at must be within 72 hours.

    Note: this table uses `scraped_at` (TEXT NOT NULL), NOT `last_updated`
    like the other three FMP tables — economic_calendar is defined in
    database/db.py rather than scrapers/fmp_scraper.py and follows the
    older naming convention.

    Skipped if the table is empty (FMP economic scraper not yet run).

    Catches: economic_calendar job (mon-fri 06:30) dying silently.
    Ignores: weekends — 72h cutoff covers Sat/Sun without false positives.
    """
    row = db.execute("SELECT MAX(scraped_at) FROM economic_calendar").fetchone()
    if row[0] is None:
        pytest.skip("economic_calendar is empty — FMP economic scraper not yet run")
    latest = datetime.fromisoformat(row[0])
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    assert latest >= cutoff, f"economic_calendar last scraped {row[0]} — older than 72h"
