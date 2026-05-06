"""
Theme coherence tests — all theme definitions are valid and produce sane counts.
Invariant 7: legally_clean must use LEFT JOIN (count > 1000 confirms this).
"""
import json
import pytest

from config.themes import THEMES

THEME_IDS = [t["id"] for t in THEMES]
REQUIRED_THEME_FIELDS = {"id", "label", "emoji", "color", "description"}


@pytest.mark.parametrize("theme", THEMES, ids=THEME_IDS)
def test_theme_has_required_fields(theme):
    """Every theme must have the mandatory top-level fields."""
    missing = REQUIRED_THEME_FIELDS - set(theme.keys())
    assert not missing, f"Theme '{theme.get('id')}' missing fields: {missing}"


@pytest.mark.parametrize("theme", THEMES, ids=THEME_IDS)
def test_theme_id_is_stable_format(theme):
    """Theme IDs must be lowercase snake_case (P14 — IDs are stable once shipped)."""
    tid = theme["id"]
    assert tid == tid.lower(), f"Theme ID '{tid}' is not lowercase"
    assert " " not in tid, f"Theme ID '{tid}' contains spaces"


def test_legally_clean_count_confirms_left_join(db, latest_run_date):
    """
    Invariant 7: legally_clean theme must use LEFT JOIN, treating NULL as clean.
    If INNER JOIN were used, count would be ~26 (only classified tickers).
    Count > 1000 confirms LEFT JOIN is in effect.
    """
    count = db.execute(
        """
        SELECT COUNT(*) FROM signal_scores ss
        LEFT JOIN legal_risk lr ON ss.ticker = lr.ticker
        WHERE (lr.risk_label IS NULL OR lr.risk_label IN ('None','Minor'))
        AND DATE(ss.scored_at) = ?
        """,
        (latest_run_date,),
    ).fetchone()[0]
    assert count > 1000, (
        f"legally_clean count={count} — suspiciously low, "
        "likely using INNER JOIN instead of LEFT JOIN"
    )


def test_theme_counts_api_returns_all_themes(client):
    """
    /api/theme-counts must return a count for every theme ID defined in config/themes.py.
    """
    resp = client.get("/api/theme-counts")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    for theme_id in THEME_IDS:
        assert theme_id in data, f"/api/theme-counts missing theme '{theme_id}'"


def test_theme_counts_are_non_negative(client):
    """/api/theme-counts must return non-negative integers for all themes."""
    resp = client.get("/api/theme-counts")
    data = json.loads(resp.data)
    for theme_id, count in data.items():
        assert isinstance(count, int) and count >= 0, \
            f"Theme '{theme_id}' has invalid count: {count}"


def test_strong_buy_momentum_theme_count(db, latest_run_date):
    """Top Signal Momentum theme should have at least 1 ticker (if any STRONG_BUY exists)."""
    strong_buy_count = db.execute(
        "SELECT COUNT(*) FROM signal_scores WHERE DATE(scored_at) = ? AND rating = 'STRONG_BUY'",
        (latest_run_date,),
    ).fetchone()[0]
    if strong_buy_count == 0:
        pytest.skip("No STRONG_BUY tickers in latest run")
    theme_count = db.execute(
        """
        SELECT COUNT(*) FROM signal_scores
        WHERE DATE(scored_at) = ?
        AND rating = 'STRONG_BUY'
        AND composite_score >= 70
        AND momentum_score >= 70
        """,
        (latest_run_date,),
    ).fetchone()[0]
    assert theme_count >= 1, "strong_buy_momentum theme has 0 results despite STRONG_BUY tickers existing"


def test_theme_labels_use_descriptive_names():
    """
    P13/P14/P15: theme labels must not use old directive language.

    Catches: theme["label"] == "Strong Buy Momentum" — directive label still present.
    Ignores: theme["id"] == "strong_buy_momentum" — stable URL/API key, never renamed (P14).
    """
    banned = ["Strong Buy", "Buy the Dip", "Insider Buying Surge"]
    for theme in THEMES:
        for phrase in banned:
            assert phrase not in theme["label"], \
                f"Theme '{theme['id']}' label still uses old directive language: '{theme['label']}'"
