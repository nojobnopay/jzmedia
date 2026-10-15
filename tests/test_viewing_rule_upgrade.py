"""Rule upgrades reclassify isolated saved evidence without losing local choices."""
import json
import sqlite3

import pytest

from app import content_rules, viewing


@pytest.fixture()
def database():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE movies (id INTEGER PRIMARY KEY, genres TEXT DEFAULT '[]', genre_ids TEXT DEFAULT '[]');
        CREATE TABLE tv_shows (id INTEGER PRIMARY KEY, genres TEXT DEFAULT '[]', genre_ids TEXT DEFAULT '[]');
        CREATE TABLE tv_episodes (id INTEGER PRIMARY KEY, show_id INTEGER NOT NULL);
    """)
    viewing.init_schema(connection)
    connection.execute("UPDATE protection_settings SET children_rule_version=1,revision=9 WHERE id=1")
    connection.execute("INSERT INTO viewing_sessions(token_hash,mode,generation,management_until)"
                       " VALUES('child','children',4,0),('private','private',12,1000)")
    yield connection
    connection.close()


def evidence(rating, keywords=()):
    result = content_rules.normalize_nfo_evidence(rating, list(keywords), "synthetic-source")
    return json.dumps({**result, "version": 1, "provider_detail": {"preserve": True}})


def insert_movie(connection, identifier, rating="US:PG", *, genres=("动画", "家庭"),
                 genre_ids=(16, 10751), private=0, choice="auto", keywords=()):
    source = evidence(rating, keywords)
    connection.execute("INSERT INTO movies(id,genres,genre_ids,is_private,children_override,content_evidence,"
                       "children_auto_status,children_reason) VALUES(?,?,?,?,?,?,'block','旧版规则')",
                       (identifier, json.dumps(genres), json.dumps(genre_ids), private, choice, source))
    return source


def snapshot(connection):
    return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("movies", "tv_episodes", "tv_shows", "protection_settings", "viewing_sessions")}


def test_startup_reclassifies_old_pg_animation_and_keeps_local_choices(database):
    old = insert_movie(database, 1)
    insert_movie(database, 2, private=1, choice="block")
    insert_movie(database, 3, rating="US:R", choice="allow")
    insert_movie(database, 4, rating="", genres=(), genre_ids=())
    partial = json.loads(insert_movie(database, 5))
    partial.update(source="tmdb", adult=False, available=["adult", "certifications"])
    database.execute("UPDATE movies SET content_evidence=? WHERE id=5", (json.dumps(partial),))
    viewing.init_schema(database)
    rows = {row["id"]: dict(row) for row in database.execute("SELECT * FROM movies")}
    assert rows[1]["children_auto_status"] == "allow"
    assert rows[2]["children_auto_status"] == "allow"
    assert rows[2]["is_private"] == 1 and rows[2]["children_override"] == "block"
    assert rows[3]["children_auto_status"] == "block" and rows[3]["children_override"] == "allow"
    assert rows[4]["children_auto_status"] == "unknown"
    assert rows[5]["children_auto_status"] == "unknown"
    assert json.loads(rows[5]["content_evidence"]) == {**partial, "version": content_rules.RULE_VERSION}
    updated_evidence = json.loads(rows[1]["content_evidence"])
    assert updated_evidence == {**json.loads(old), "version": content_rules.RULE_VERSION}
    assert database.execute("SELECT children_rule_version,revision FROM protection_settings").fetchone()[:] == (
        content_rules.RULE_VERSION, 10)
    sessions = {row["token_hash"]: dict(row) for row in database.execute("SELECT * FROM viewing_sessions")}
    assert sessions["child"]["mode"] == "children" and sessions["child"]["generation"] == 5
    assert sessions["private"]["mode"] == "private" and sessions["private"]["generation"] == 13


def test_episode_decisions_are_updated_before_the_parent_show(database):
    database.execute("INSERT INTO tv_shows(id,content_evidence,children_auto_status,children_reason,is_private,"
                     "children_override) VALUES(1,?,'block','旧分集阻止',1,'allow'),"
                     "(2,?,'allow','旧版准入',0,'auto')", (evidence("US:TV-G"), evidence("US:TV-G")))
    database.execute("INSERT INTO tv_episodes(id,show_id,content_evidence,children_auto_status,children_reason)"
                     " VALUES(1,1,?,'block','旧版阻止'),(2,2,?,'allow','旧版准入')",
                     (evidence("US:TV-Y"), evidence("US:TV-G", ["murder"])))
    viewing.init_schema(database)
    episodes = {row["id"]: dict(row) for row in database.execute("SELECT * FROM tv_episodes")}
    shows = {row["id"]: dict(row) for row in database.execute("SELECT * FROM tv_shows")}
    assert episodes[1]["children_auto_status"] == shows[1]["children_auto_status"] == "allow"
    assert shows[1]["is_private"] == 1 and shows[1]["children_override"] == "allow"
    assert episodes[2]["children_auto_status"] == shows[2]["children_auto_status"] == "block"
    assert "分集" in shows[2]["children_reason"]


def test_upgrade_episode_cartoon_conflict_inherits_the_parent_genre_context(database):
    database.execute("INSERT INTO tv_shows(id,genres,genre_ids,content_evidence,children_auto_status,children_reason)"
                     " VALUES(1,'[\"动画\",\"儿童\"]','[16,10762]',?,'block','旧版阻止')",
                     (evidence("US:TV-Y7-FV"),))
    source = evidence("US:TV-G", ["cartoon violence"])
    database.execute("INSERT INTO tv_episodes(id,show_id,content_evidence,children_auto_status,children_reason)"
                     " VALUES(1,1,?,'block','旧版阻止')", (source,))
    viewing.init_schema(database)
    episode = database.execute("SELECT * FROM tv_episodes").fetchone()
    assert episode["children_auto_status"] == "allow"
    assert json.loads(episode["content_evidence"]) == {**json.loads(source), "version": content_rules.RULE_VERSION}
    assert database.execute("SELECT children_auto_status FROM tv_shows").fetchone()[0] == "allow"


@pytest.mark.parametrize("offset", [0, 1])
def test_current_or_newer_rule_checkpoint_skips_evaluation(database, monkeypatch, offset):
    insert_movie(database, 1)
    database.execute("UPDATE protection_settings SET children_rule_version=?",
                     (content_rules.RULE_VERSION + offset,))
    before = snapshot(database)

    def unexpected(*_args):
        raise AssertionError("Already classified rule versions must not run again")

    monkeypatch.setattr(content_rules, "evaluate_content", unexpected)
    viewing.init_schema(database)
    assert snapshot(database) == before


def test_completed_upgrade_is_idempotent(database, monkeypatch):
    insert_movie(database, 1)
    viewing.init_schema(database)
    before = snapshot(database)

    def unexpected(*_args):
        raise AssertionError("A repeated startup must not reclassify or revoke leases")

    monkeypatch.setattr(content_rules, "evaluate_content", unexpected)
    viewing.init_schema(database)
    assert snapshot(database) == before


def test_failed_upgrade_rolls_back_decisions_checkpoint_and_revocation(database, monkeypatch):
    insert_movie(database, 1)
    insert_movie(database, 2)
    before = snapshot(database)
    original = content_rules.evaluate_content
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic evaluator failure")
        return original(*args)

    monkeypatch.setattr(content_rules, "evaluate_content", fail_second)
    with pytest.raises(RuntimeError, match="synthetic evaluator failure"):
        viewing.init_schema(database)
    assert snapshot(database) == before
    monkeypatch.setattr(content_rules, "evaluate_content", original)
    viewing.init_schema(database)
    assert database.execute("SELECT children_rule_version FROM protection_settings").fetchone()[0] == (
        content_rules.RULE_VERSION)
    assert database.execute("SELECT children_auto_status FROM movies WHERE id=1").fetchone()[0] == "allow"


def test_empty_database_advances_checkpoint_without_revoking_devices(database):
    before = [tuple(row) for row in database.execute("SELECT * FROM viewing_sessions ORDER BY token_hash")]
    viewing.init_schema(database)
    assert database.execute("SELECT children_rule_version,revision FROM protection_settings").fetchone()[:] == (
        content_rules.RULE_VERSION, 9)
    assert [tuple(row) for row in database.execute("SELECT * FROM viewing_sessions ORDER BY token_hash")] == before
