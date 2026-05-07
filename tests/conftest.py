"""
Shared pytest fixtures for SignalIntel test suite.

Auth: all routes require session['user_id']. The `client` fixture injects
user_id=2 (markn) into the Flask test session before each test.

DB: connects to data/trading_system.db — the live SQLite file. All queries
are read-only. Tests never write to the database.
"""
import sys
import os
import sqlite3
import pytest

# Ensure project root is on path so web.app and config.settings are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.constants import DATABASE_PATH


@pytest.fixture(scope="session")
def db():
    """Read-only SQLite connection to the live trading_system.db."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def flask_app():
    """Flask app instance with TESTING=True."""
    from web.app import app
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(flask_app):
    """
    Flask test client with auth session pre-seeded.
    Creates a fresh client per test; injects session['user_id'] = 2 (markn).
    """
    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = 2
        yield c


@pytest.fixture(scope="session")
def latest_run_date(db):
    """The most recent DATE(scored_at) in signal_scores."""
    row = db.execute(
        "SELECT MAX(DATE(scored_at)) FROM signal_scores"
    ).fetchone()
    date = row[0]
    assert date is not None, "signal_scores is empty — run the scorer first"
    return date


@pytest.fixture(scope="session")
def latest_signals(db, latest_run_date):
    """All signal_scores rows for the most recent scoring run."""
    rows = db.execute(
        "SELECT * FROM signal_scores WHERE DATE(scored_at) = ?",
        (latest_run_date,),
    ).fetchall()
    assert len(rows) > 0, f"No signals found for {latest_run_date}"
    return rows
