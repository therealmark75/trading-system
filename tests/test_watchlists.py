"""
Tests for multi-watchlist CRUD — db layer and API endpoints.
"""
import pytest
import sqlite3
import tempfile
import os
from database.db import (
    initialise_user_schema, create_user, get_watchlists_meta,
    get_or_create_default_watchlist, create_watchlist, rename_watchlist,
    delete_watchlist, add_to_watchlist, remove_from_watchlist, get_watchlist,
)
def _hash(pw):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(pw, method="pbkdf2:sha256")


# ── In-memory DB fixture ─────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    initialise_user_schema(db_path)
    # Also need the main schema tables referenced by get_watchlist
    from database.db import initialise_schema
    initialise_schema(db_path)
    return db_path


@pytest.fixture
def user_id(tmp_db):
    uid = create_user(tmp_db, "testuser", "test@example.com", _hash("pw"))
    return uid


# ── DB layer tests ────────────────────────────────────────────────────

def test_get_or_create_default_watchlist_creates_on_first_call(tmp_db, user_id):
    wid = get_or_create_default_watchlist(tmp_db, user_id)
    assert isinstance(wid, int)
    wls = get_watchlists_meta(tmp_db, user_id)
    assert len(wls) == 1
    assert wls[0]['name'] == 'My Watchlist'


def test_get_or_create_default_watchlist_idempotent(tmp_db, user_id):
    wid1 = get_or_create_default_watchlist(tmp_db, user_id)
    wid2 = get_or_create_default_watchlist(tmp_db, user_id)
    assert wid1 == wid2
    assert len(get_watchlists_meta(tmp_db, user_id)) == 1


def test_create_watchlist_returns_id_and_name(tmp_db, user_id):
    result = create_watchlist(tmp_db, user_id, "Tech Picks")
    assert result["name"] == "Tech Picks"
    assert isinstance(result["id"], int)


def test_create_watchlist_duplicate_raises_value_error(tmp_db, user_id):
    create_watchlist(tmp_db, user_id, "Dupes")
    with pytest.raises(ValueError, match="already exists"):
        create_watchlist(tmp_db, user_id, "Dupes")


def test_rename_watchlist(tmp_db, user_id):
    wid = create_watchlist(tmp_db, user_id, "Old Name")["id"]
    ok = rename_watchlist(tmp_db, user_id, wid, "New Name")
    assert ok is True
    wls = get_watchlists_meta(tmp_db, user_id)
    names = [w["name"] for w in wls]
    assert "New Name" in names
    assert "Old Name" not in names


def test_rename_watchlist_wrong_user_returns_false(tmp_db, user_id):
    wid = create_watchlist(tmp_db, user_id, "Mine")["id"]
    ok = rename_watchlist(tmp_db, user_id + 99, wid, "Stolen")
    assert ok is False


def test_rename_to_existing_name_raises(tmp_db, user_id):
    create_watchlist(tmp_db, user_id, "A")
    wid_b = create_watchlist(tmp_db, user_id, "B")["id"]
    with pytest.raises(ValueError):
        rename_watchlist(tmp_db, user_id, wid_b, "A")


def test_delete_watchlist(tmp_db, user_id):
    wid = create_watchlist(tmp_db, user_id, "To Delete")["id"]
    ok = delete_watchlist(tmp_db, user_id, wid)
    assert ok is True
    wls = get_watchlists_meta(tmp_db, user_id)
    assert all(w["id"] != wid for w in wls)


def test_delete_watchlist_wrong_user_returns_false(tmp_db, user_id):
    wid = create_watchlist(tmp_db, user_id, "Mine")["id"]
    ok = delete_watchlist(tmp_db, user_id + 99, wid)
    assert ok is False


def test_add_and_remove_ticker(tmp_db, user_id):
    wid = get_or_create_default_watchlist(tmp_db, user_id)
    add_to_watchlist(tmp_db, user_id, "AAPL", watchlist_id=wid)
    items = get_watchlist(tmp_db, user_id, wid)
    assert any(r["ticker"] == "AAPL" for r in items)
    remove_from_watchlist(tmp_db, user_id, "AAPL", watchlist_id=wid)
    items = get_watchlist(tmp_db, user_id, wid)
    assert not any(r["ticker"] == "AAPL" for r in items)


def test_same_ticker_in_multiple_watchlists(tmp_db, user_id):
    """Ticker can appear in more than one watchlist for the same user."""
    wid1 = create_watchlist(tmp_db, user_id, "WL1")["id"]
    wid2 = create_watchlist(tmp_db, user_id, "WL2")["id"]
    add_to_watchlist(tmp_db, user_id, "TSLA", watchlist_id=wid1)
    add_to_watchlist(tmp_db, user_id, "TSLA", watchlist_id=wid2)
    items1 = get_watchlist(tmp_db, user_id, wid1)
    items2 = get_watchlist(tmp_db, user_id, wid2)
    assert any(r["ticker"] == "TSLA" for r in items1)
    assert any(r["ticker"] == "TSLA" for r in items2)


def test_delete_watchlist_cascades_tickers(tmp_db, user_id):
    wid = create_watchlist(tmp_db, user_id, "Ephemeral")["id"]
    add_to_watchlist(tmp_db, user_id, "GOOG", watchlist_id=wid)
    delete_watchlist(tmp_db, user_id, wid)
    from database.db import get_connection
    conn = get_connection(tmp_db)
    rows = conn.execute("SELECT * FROM watchlists WHERE watchlist_id=?", (wid,)).fetchall()
    conn.close()
    assert rows == []


def test_ticker_count_in_meta(tmp_db, user_id):
    wid = create_watchlist(tmp_db, user_id, "Counted")["id"]
    add_to_watchlist(tmp_db, user_id, "MSFT", watchlist_id=wid)
    add_to_watchlist(tmp_db, user_id, "NVDA", watchlist_id=wid)
    meta = get_watchlists_meta(tmp_db, user_id)
    counted = next(w for w in meta if w["id"] == wid)
    assert counted["ticker_count"] == 2


# ── API endpoint tests ────────────────────────────────────────────────

@pytest.fixture
def api_user(tmp_db):
    """Create a user in the tmp_db and return (db_path, user_id)."""
    uid = create_user(tmp_db, "apiuser", "api@example.com", _hash("pw"))
    return tmp_db, uid


@pytest.fixture
def wl_client(api_user):
    """Flask test client with auth session pointed at tmp_db."""
    db_path, uid = api_user
    import web.app as app_module
    # Temporarily redirect DATABASE_PATH
    original = app_module.DATABASE_PATH
    app_module.DATABASE_PATH = db_path
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = uid
        yield c
    app_module.DATABASE_PATH = original


def test_api_watchlists_list_empty(wl_client):
    r = wl_client.get('/api/watchlists')
    assert r.status_code == 200
    data = r.get_json()
    assert "watchlists" in data
    assert isinstance(data["watchlists"], list)


def test_api_watchlists_create(wl_client):
    r = wl_client.post('/api/watchlists',
                       json={"name": "My Picks"},
                       content_type='application/json')
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert "id" in data


def test_api_watchlists_create_duplicate_returns_409(wl_client):
    wl_client.post('/api/watchlists', json={"name": "Dupe"},
                   content_type='application/json')
    r = wl_client.post('/api/watchlists', json={"name": "Dupe"},
                       content_type='application/json')
    assert r.status_code == 409


def test_api_watchlists_rename(wl_client):
    r = wl_client.post('/api/watchlists', json={"name": "Original"},
                       content_type='application/json')
    wid = r.get_json()["id"]
    r2 = wl_client.patch(f'/api/watchlists/{wid}',
                         json={"name": "Renamed"},
                         content_type='application/json')
    assert r2.status_code == 200
    assert r2.get_json()["ok"] is True


def test_api_watchlists_delete_only_watchlist_blocked(wl_client):
    r = wl_client.post('/api/watchlists', json={"name": "Solo"},
                       content_type='application/json')
    wid = r.get_json()["id"]
    r2 = wl_client.delete(f'/api/watchlists/{wid}?confirm=true')
    assert r2.status_code == 400
    assert "only watchlist" in r2.get_json()["error"].lower()


def test_api_watchlists_delete_with_two_succeeds(wl_client):
    r1 = wl_client.post('/api/watchlists', json={"name": "First"},
                        content_type='application/json')
    wl_client.post('/api/watchlists', json={"name": "Second"},
                   content_type='application/json')
    wid = r1.get_json()["id"]
    r2 = wl_client.delete(f'/api/watchlists/{wid}?confirm=true')
    assert r2.status_code == 200
    assert r2.get_json()["ok"] is True


def test_api_watchlists_delete_without_confirm_rejected(wl_client):
    r = wl_client.post('/api/watchlists', json={"name": "Safe"},
                       content_type='application/json')
    wid = r.get_json()["id"]
    r2 = wl_client.delete(f'/api/watchlists/{wid}')
    assert r2.status_code == 400


def test_api_add_and_remove_ticker(wl_client):
    r = wl_client.post('/api/watchlists', json={"name": "Picks"},
                       content_type='application/json')
    wid = r.get_json()["id"]
    # Add
    r2 = wl_client.post(f'/api/watchlists/{wid}/tickers',
                         json={"ticker": "AAPL"}, content_type='application/json')
    assert r2.get_json()["ok"] is True
    # Remove
    r3 = wl_client.delete(f'/api/watchlists/{wid}/tickers/AAPL')
    assert r3.get_json()["ok"] is True


# ── Default watchlist feature tests ───────────────────────────────────


@pytest.fixture
def signup_client(tmp_db):
    """Flask test client pointed at tmp_db with NO pre-created user/session.
    Use this to exercise the signup route end-to-end."""
    import web.app as app_module
    original = app_module.DATABASE_PATH
    app_module.DATABASE_PATH = tmp_db
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c, tmp_db
    app_module.DATABASE_PATH = original


def test_signup_creates_default_watchlist(signup_client):
    """
    A successful POST to /register must result in exactly one watchlist for
    the new user, named 'My Watchlist', with is_default=1 and alerts_enabled=1.

    Catches: signup that forgets to call create_default_watchlist (regression
             of this feature), or a signup that creates the watchlist with
             wrong defaults (e.g. is_default=0 or alerts off).
    Ignores: post-signup redirect target, session cookie shape, watchlist
             sort_order or created_at timestamp.

    Function effects exercised: register() reads users (uniqueness checks),
    writes users (create_user), writes watchlists_meta (create_default_watchlist),
    writes session.
    """
    client, db_path = signup_client
    resp = client.post('/register', data={
        "username": "newbie",
        "email":    "newbie@example.com",
        "password": "password1",
        "confirm":  "password1",
    }, follow_redirects=False)
    # 302 redirect to index on success
    assert resp.status_code == 302, f"signup returned {resp.status_code}, body={resp.data!r}"

    from database.db import get_user_by_username, get_watchlists_meta
    user = get_user_by_username(db_path, "newbie")
    assert user is not None
    wls = get_watchlists_meta(db_path, user["id"])
    assert len(wls) == 1, f"expected exactly 1 watchlist, got {len(wls)}: {wls}"
    wl = wls[0]
    assert wl["name"] == "My Watchlist"
    assert wl["is_default"] == 1
    assert wl["alerts_enabled"] == 1


def test_default_watchlist_cannot_be_deleted(wl_client, api_user):
    """
    DELETE on the default watchlist must return 400 with a message that
    mentions the default cannot be deleted, and the row must remain in the DB.

    Catches: a fix that omits the is_default check on DELETE, or one that
             returns a generic 'cannot delete only watchlist' message instead
             of the specific default-watchlist message.
    Ignores: exact wording beyond the substring 'cannot be deleted', HTTP
             headers, response body shape beyond the 'error' field.

    Function effects exercised: api_watchlists_delete reads watchlists_meta
    (is_default check + len check), does NOT write to DB on rejection path.
    """
    db_path, uid = api_user
    # Create the default by direct DB write (signup path is exercised separately).
    from database.db import create_default_watchlist
    wid = create_default_watchlist(db_path, uid)
    # Add a second non-default watchlist so the 'only watchlist' check would
    # NOT fire — proves the rejection comes specifically from the is_default guard.
    wl_client.post('/api/watchlists', json={"name": "Other"},
                   content_type='application/json')

    resp = wl_client.delete(f'/api/watchlists/{wid}?confirm=true')
    assert resp.status_code == 400
    err = resp.get_json().get("error", "")
    assert "cannot be deleted" in err.lower(), f"error message {err!r} should mention 'cannot be deleted'"

    # Row must still exist
    from database.db import get_connection
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT id, is_default FROM watchlists_meta WHERE id=?", (wid,)
    ).fetchone()
    conn.close()
    assert row is not None, "default watchlist was deleted despite rejection"
    assert row[1] == 1, "is_default flag was mutated during rejection"


def test_default_watchlist_can_be_renamed(wl_client, api_user):
    """
    PATCH on the default watchlist must succeed and preserve is_default=1.

    Catches: a future change that strips the is_default flag on rename, or
             a rename guard that erroneously rejects the default.
    Ignores: updated_at timestamp value, any other column unrelated to
             is_default and name.

    Function effects exercised: rename_watchlist writes watchlists_meta
    (UPDATE name + updated_at). is_default column is not touched by the
    UPDATE statement.
    """
    db_path, uid = api_user
    from database.db import create_default_watchlist, get_connection
    wid = create_default_watchlist(db_path, uid)

    resp = wl_client.patch(f'/api/watchlists/{wid}',
                           json={"name": "Renamed Default"},
                           content_type='application/json')
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT name, is_default FROM watchlists_meta WHERE id=?", (wid,)
    ).fetchone()
    conn.close()
    assert row[0] == "Renamed Default"
    assert row[1] == 1, "is_default flag was lost on rename"


def test_backfill_is_idempotent(tmp_db):
    """
    Running scripts/backfill_default_watchlists.py twice must not change
    state on the second run: no new rows created, no flags flipped.

    Catches: a backfill that re-evaluates branch B/C even when a default
             already exists, or one that creates duplicates on re-run.
    Ignores: ordering of stdout output, exact wording of summary line,
             timing or runtime of the script.

    Function effects exercised: backfill() reads users + watchlists_meta,
    writes UPDATE/INSERT only on first run. Second run reads only.
    """
    from scripts.backfill_default_watchlists import backfill
    from database.db import create_user, create_watchlist, get_connection

    # Seed three users in three different states.
    uid_a = create_user(tmp_db, "alice", "a@example.com", _hash("pw"))
    uid_b = create_user(tmp_db, "bob",   "b@example.com", _hash("pw"))
    uid_c = create_user(tmp_db, "carol", "c@example.com", _hash("pw"))
    # alice already has a default
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO watchlists_meta(user_id,name,sort_order,alerts_enabled,is_default) "
                 "VALUES(?,?,0,1,1)", (uid_a, "Alice WL"))
    conn.commit()
    conn.close()
    # bob has a watchlist but it's not flagged default
    create_watchlist(tmp_db, uid_b, "Bob WL")
    # carol has zero watchlists

    counts1 = backfill(tmp_db)
    counts2 = backfill(tmp_db)

    assert counts1 == {"users_processed": 3, "created": 1, "flagged": 1, "skipped": 1}
    assert counts2 == {"users_processed": 3, "created": 0, "flagged": 0, "skipped": 3}

    # And the per-user state is correct after both runs
    conn = get_connection(tmp_db)
    rows_per_user = {
        uid: conn.execute(
            "SELECT COUNT(*), SUM(is_default) FROM watchlists_meta WHERE user_id=?", (uid,)
        ).fetchone()
        for uid in (uid_a, uid_b, uid_c)
    }
    conn.close()
    for uid, (count, sum_default) in rows_per_user.items():
        assert count == 1, f"user {uid} has {count} watchlists, expected 1"
        assert sum_default == 1, f"user {uid} has {sum_default} default watchlists, expected 1"


def test_only_one_default_per_user(tmp_db, user_id):
    """
    The partial UNIQUE INDEX on watchlists_meta(user_id) WHERE is_default=1
    must reject a second is_default=1 INSERT for the same user.

    Catches: removal/rename of the partial unique index, or accidental
             creation of duplicate defaults via a code path that bypasses
             create_default_watchlist().
    Ignores: error message wording (sqlite3 changes IntegrityError text
             across versions); only that an IntegrityError is raised at all.

    Function effects exercised: direct DB INSERT, hits the partial UNIQUE
    INDEX. create_default_watchlist() inherits this protection because
    it issues the same INSERT shape.
    """
    from database.db import create_default_watchlist, get_connection
    create_default_watchlist(tmp_db, user_id)
    # Second attempt must raise IntegrityError from the partial UNIQUE INDEX.
    conn = get_connection(tmp_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO watchlists_meta(user_id,name,sort_order,alerts_enabled,is_default) "
            "VALUES(?,?,0,1,1)", (user_id, "Duplicate Default"))
        conn.commit()
    conn.close()
