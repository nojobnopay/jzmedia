"""Device-scoped viewing protection, independent of the existing write API token.

Only HTTP requests install a context. Background catalogue work keeps its full
view; a management grant never changes the scope of ordinary browsing requests.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
import time

from fastapi import HTTPException

IDLE_SECONDS = 15 * 60
COOKIE = "jz_viewing"
HEADER = "x-viewing-session"


@dataclass(frozen=True)
class ContentAccessContext:
    mode: str = "normal"
    session_hash: str = ""
    generation: int = 0
    management: bool = False


current: ContextVar[ContentAccessContext | None] = ContextVar("content_access", default=None)


@contextmanager
def unrestricted():
    token = current.set(None)
    try:
        yield
    finally:
        current.reset(token)


def init_schema(c):
    for table in ("movies", "tv_shows"):
        existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        columns = {
            "is_private": "INTEGER NOT NULL DEFAULT 0",
            "children_override": "TEXT NOT NULL DEFAULT 'auto'",
            "content_evidence": "TEXT NOT NULL DEFAULT '{}'",
            "children_auto_status": "TEXT NOT NULL DEFAULT 'unknown'",
            "children_reason": "TEXT NOT NULL DEFAULT '资料不足，待家长确认'",
        }
        for name, definition in columns.items():
            if name not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        c.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_visibility ON {table}"
                  "(is_private, children_override, children_auto_status)")
    existing = {r[1] for r in c.execute("PRAGMA table_info(tv_episodes)")}
    for name, definition in {
        "content_evidence": "TEXT NOT NULL DEFAULT '{}'",
        "children_auto_status": "TEXT NOT NULL DEFAULT 'unknown'",
        "children_reason": "TEXT NOT NULL DEFAULT '资料不足，待家长确认'",
    }.items():
        if name not in existing:
            c.execute(f"ALTER TABLE tv_episodes ADD COLUMN {name} {definition}")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS protection_settings (
            id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL DEFAULT ''
        );
        INSERT OR IGNORE INTO protection_settings(id) VALUES(1);
        CREATE TABLE IF NOT EXISTS viewing_sessions (
            token_hash TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'normal',
            generation INTEGER NOT NULL DEFAULT 0, last_activity REAL NOT NULL DEFAULT 0,
            management_until REAL NOT NULL DEFAULT 0, playback_until REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS protection_attempts (
            subject TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
            window_start REAL NOT NULL DEFAULT 0
        );
    """)
    if "revision" not in {row[1] for row in c.execute("PRAGMA table_info(protection_settings)")}:
        c.execute("ALTER TABLE protection_settings ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    if "children_rule_version" not in {row[1] for row in c.execute("PRAGMA table_info(protection_settings)")}:
        c.execute("ALTER TABLE protection_settings ADD COLUMN children_rule_version INTEGER NOT NULL DEFAULT 0")
    _upgrade_children_rules(c)


def _upgrade_children_rules(c):
    """Reclassify saved evidence once per rule version, without fetching metadata.

    The checkpoint, decisions and revoked leases commit together. An application
    downgrade must never overwrite decisions made by a newer rule version.
    """
    from .content_rules import RULE_VERSION, evaluate_content

    if c.execute("SELECT children_rule_version FROM protection_settings WHERE id=1").fetchone()[0] >= RULE_VERSION:
        return
    c.execute("SAVEPOINT children_rule_upgrade")
    try:
        # Obtain the writer lock before reading evidence. Other starting workers
        # can then observe this checkpoint rather than repeat a partial upgrade.
        c.execute("UPDATE protection_settings SET children_rule_version=children_rule_version WHERE id=1")
        if c.execute("SELECT children_rule_version FROM protection_settings WHERE id=1").fetchone()[0] >= RULE_VERSION:
            c.execute("RELEASE SAVEPOINT children_rule_upgrade")
            return
        changed = False
        for table, kind in (("movies", "movie"), ("tv_episodes", "episode"), ("tv_shows", "tv")):
            rows = c.execute(f"SELECT * FROM main.{table}").fetchall()
            for row in rows:
                item = dict(row)
                evidence = item["content_evidence"]
                status, reason = evaluate_content(kind, evidence, item.get("genres"), item.get("genre_ids"))
                if kind == "tv":
                    status, reason = effective_show_classification(c, item["id"], status, reason)
                try:
                    source_evidence = json.loads(evidence) if isinstance(evidence, str) else evidence
                except (ValueError, TypeError):
                    source_evidence = None
                if isinstance(source_evidence, dict):
                    # Preserve provider evidence verbatim, including fields an
                    # older server does not understand; only advance its marker.
                    source_evidence = {**source_evidence, "version": RULE_VERSION}
                    evidence = json.dumps(source_evidence, ensure_ascii=False)
                if (status, reason, evidence) != (item["children_auto_status"], item["children_reason"],
                                                  item["content_evidence"]):
                    c.execute(f"UPDATE main.{table} SET content_evidence=?,children_auto_status=?,children_reason=?"
                              " WHERE id=?", (evidence, status, reason, item["id"]))
                    changed = True
        if changed:
            invalidate_content_access(c)
        c.execute("UPDATE protection_settings SET children_rule_version=? WHERE id=1", (RULE_VERSION,))
        c.execute("RELEASE SAVEPOINT children_rule_upgrade")
    except Exception:
        c.execute("ROLLBACK TO SAVEPOINT children_rule_upgrade")
        c.execute("RELEASE SAVEPOINT children_rule_upgrade")
        raise


def _connection():
    from .store._base import _conn
    with unrestricted():
        return _conn()


def enabled() -> bool:
    with _connection() as c:
        row = c.execute("SELECT password_hash FROM protection_settings WHERE id=1").fetchone()
        return bool(row and row[0])


def error(code: str, message: str, status: int = 403):
    raise HTTPException(status, detail={"code": code, "message": message})


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve(token: str = "", *, create: bool = False):
    """Return (session, newly issued secret, expired). Never store bearer secrets."""
    now = time.time()
    with _connection() as c:
        row = c.execute("SELECT * FROM viewing_sessions WHERE token_hash=?",
                        (token_hash(token),)).fetchone() if token else None
        secret = ""
        if row is None and create:
            secret = secrets.token_urlsafe(32)
            key = token_hash(secret)
            c.execute("INSERT INTO viewing_sessions(token_hash,last_activity) VALUES(?,?)", (key, now))
            row = c.execute("SELECT * FROM viewing_sessions WHERE token_hash=?", (key,)).fetchone()
        if row is None:
            return None, secret, False
        session = dict(row)
        expired = session["mode"] == "private" and max(
            session["last_activity"] + IDLE_SECONDS, session["playback_until"]) <= now
        if expired:
            c.execute("UPDATE viewing_sessions SET mode='normal', management_until=0,"
                      " playback_until=0,generation=generation+1 WHERE token_hash=?", (session["token_hash"],))
            session.update(mode="normal", management_until=0, playback_until=0,
                           generation=session["generation"] + 1)
        return session, secret, expired


def session_by_hash(key):
    with _connection() as c:
        row = c.execute("SELECT * FROM viewing_sessions WHERE token_hash=?", (key,)).fetchone()
        return dict(row) if row else None


def public_session(session):
    now = time.time()
    session = session or {}
    return {"mode": session.get("mode", "normal"), "enabled": enabled(),
            "management_unlocked": session.get("management_until", 0) > now,
            "generation": session.get("generation", 0),
            "expires_at": max(session.get("last_activity", 0) + IDLE_SECONDS,
                              session.get("playback_until", 0)) if session.get("mode") == "private" else 0}


def require_management(session=None):
    if not enabled():
        return
    ctx = current.get()
    if session is None and ctx:
        session = session_by_hash(ctx.session_hash)
    if not session or session["mode"] == "children" or session["management_until"] <= time.time():
        error("management_required", "请验证保护密码后管理全部内容（包括隐私内容）")


def _encode_password(password):
    if not isinstance(password, str) or not 6 <= len(password) <= 128:
        error("invalid_password", "保护密码需要 6–128 个字符", 422)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password, subject):
    now = time.time()
    subject = hashlib.sha256(subject.encode()).hexdigest()
    with _connection() as c:
        # Serialize attempt counters across workers as well as threads.
        c.execute("BEGIN IMMEDIATE")
        attempt = c.execute("SELECT * FROM protection_attempts WHERE subject=?", (subject,)).fetchone()
        if attempt and attempt["window_start"] + 300 > now and attempt["failures"] >= 5:
            error("protection_rate_limited", "密码尝试过多，请 5 分钟后重试", 429)
        protection = c.execute("SELECT password_hash,revision FROM protection_settings WHERE id=1").fetchone()
        stored = protection["password_hash"]
        if not stored:
            error("protection_setup_required", "请先设置保护密码")
        valid = False
        if isinstance(password, str) and len(password) <= 128:
            try:
                _, salt, expected = stored.split("$")
                actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
                valid = hmac.compare_digest(actual.hex(), expected)
            except (ValueError, TypeError):
                valid = False
        if not valid:
            count = attempt["failures"] + 1 if attempt and attempt["window_start"] + 300 > now else 1
            start = attempt["window_start"] if count > 1 else now
            c.execute("INSERT OR REPLACE INTO protection_attempts VALUES(?,?,?)", (subject, count, start))
            # Commit before raising: otherwise the context manager rolls back the rate limit.
            c.commit()
            error("protection_password_invalid", "保护密码不正确", 401)
        c.execute("DELETE FROM protection_attempts WHERE subject=?", (subject,))
        return protection["revision"]


def set_password(new_password, *, expected_revision=None):
    encoded = _encode_password(new_password)
    with _connection() as c:
        updated = c.execute("UPDATE protection_settings SET password_hash=?,revision=revision+1 WHERE id=1"
                            + (" AND revision=?" if expected_revision is not None else ""),
                            (encoded, expected_revision) if expected_revision is not None else (encoded,))
        if updated.rowcount != 1:
            error("protection_password_invalid", "保护设置已更改，请重新验证密码", 401)
        c.execute("UPDATE viewing_sessions SET mode=CASE WHEN mode='children' THEN 'children' ELSE 'normal' END,"
                  " management_until=0,playback_until=0,generation=generation+1")


def policy_metadata(kind, old, fields):
    """Materialize automatic decisions; metadata may never replace local choices."""
    from .content_rules import evaluate_content, merge_content_evidence
    old = old or {}
    identity_key = "tmdb_episode_id" if kind == "episode" else "tmdb_id"
    changed_identity = identity_key in fields and fields[identity_key] != old.get(identity_key)
    previous = old.get("content_evidence") or {}
    if isinstance(previous, str):
        previous = json.loads(previous)
    incoming = fields.get("content_evidence")
    if incoming is not None or changed_identity or "genres" in fields or "genre_ids" in fields:
        evidence = (previous if incoming is None and not changed_identity else
                    merge_content_evidence({} if changed_identity else previous, incoming or {}))
        previous_genres = {} if changed_identity else old
        status, reason = evaluate_content(kind, evidence, fields.get("genres", previous_genres.get("genres")),
                                          fields.get("genre_ids", previous_genres.get("genre_ids")))
        return {"content_evidence": json.dumps(evidence, ensure_ascii=False),
                "children_auto_status": status, "children_reason": reason}
    return {}


def inherit_movie_policy(c, movie_id):
    row = c.execute("SELECT * FROM main.movies WHERE id=?", (movie_id,)).fetchone()
    if not row or not row["tmdb_id"]:
        return False
    group = c.execute("SELECT id,is_private,children_override FROM main.movies WHERE library_id=? AND tmdb_id=?",
                      (row["library_id"], row["tmdb_id"])).fetchall()
    private = max(r["is_private"] for r in group)
    choices = {r["children_override"] for r in group}
    override = "block" if "block" in choices else "allow" if "allow" in choices else "auto"
    c.execute("UPDATE main.movies SET is_private=?,children_override=? WHERE library_id=? AND tmdb_id=?",
              (private, override, row["library_id"], row["tmdb_id"]))
    return any(r["is_private"] != private or r["children_override"] != override for r in group)


def invalidate_content_access(c):
    c.execute("UPDATE protection_settings SET revision=revision+1 WHERE id=1")
    c.execute("UPDATE viewing_sessions SET generation=generation+1")


def effective_show_classification(c, show_id, status, reason, *, show_fields=None):
    """Use the parent work's genre context without replacing episode evidence."""
    from .content_rules import evaluate_content, is_family_animation

    row = c.execute("SELECT * FROM main.tv_shows WHERE id=?", (show_id,)).fetchone()
    parent = {**(dict(row) if row else {}), **(show_fields or {})}
    family_animation = is_family_animation(parent.get("content_evidence"), parent.get("genres"),
                                           parent.get("genre_ids"))
    blocked = False
    for episode in c.execute("SELECT id,content_evidence,children_auto_status,children_reason"
                             " FROM main.tv_episodes WHERE show_id=?", (show_id,)).fetchall():
        episode_status, episode_reason = evaluate_content("episode", episode["content_evidence"],
                                                         family_animation_context=family_animation)
        if (episode_status, episode_reason) != (episode["children_auto_status"], episode["children_reason"]):
            c.execute("UPDATE main.tv_episodes SET children_auto_status=?,children_reason=? WHERE id=?",
                      (episode_status, episode_reason, episode["id"]))
        blocked |= episode_status == "block"
    if blocked:
        return "block", "部分分集包含不适合儿童的分级或风险提示"
    return status, reason


def merge_show_restrictions(c, show_id, sources=()):
    """Ownership changes retain the strongest current or historical user choice."""
    target = c.execute("SELECT * FROM main.tv_shows WHERE id=?", (show_id,)).fetchone()
    if not target:
        return False
    rows = [dict(target), *(dict(row) for row in sources if row is not None)]
    private = int(any(row.get("is_private") for row in rows))
    # Allow is permission for one work, so a different source cannot grant it.
    override = "block" if any(row.get("children_override") == "block" for row in rows) else target["children_override"]
    changed = target["is_private"] != private or target["children_override"] != override
    if changed:
        c.execute("UPDATE main.tv_shows SET is_private=?,children_override=? WHERE id=?",
                  (private, override, show_id))
    return changed


def recompute_show_classification(c, show_id):
    """Re-evaluate a show's own evidence and its current episodes in a transaction."""
    row = c.execute("SELECT * FROM main.tv_shows WHERE id=?", (show_id,)).fetchone()
    if not row:
        return
    fields = policy_metadata("tv", dict(row), {"content_evidence": row["content_evidence"]})
    status, reason = effective_show_classification(c, show_id, fields["children_auto_status"], fields["children_reason"])
    c.execute("UPDATE main.tv_shows SET children_auto_status=?,children_reason=? WHERE id=?",
              (status, reason, show_id))


def merge_movie_restrictions(c, movie_id, sources=()):
    """Carry an extra's previous restrictions to every version of its new film."""
    target = c.execute("SELECT * FROM main.movies WHERE id=?", (movie_id,)).fetchone()
    if not target:
        return []
    rows = [dict(target), *(dict(row) for row in sources if row is not None)]
    private = int(any(row.get("is_private") for row in rows))
    override = "block" if any(row.get("children_override") == "block" for row in rows) else target["children_override"]
    changed = target["is_private"] != private or target["children_override"] != override
    if changed:
        c.execute("UPDATE main.movies SET is_private=?,children_override=? WHERE id=?",
                  (private, override, movie_id))
    inherited = inherit_movie_policy(c, movie_id)
    if not (changed or inherited):
        return []
    if not target['tmdb_id']:
        return [movie_id]
    return [row[0] for row in c.execute("SELECT id FROM main.movies WHERE library_id=? AND tmdb_id=?",
                                       (target['library_id'], target['tmdb_id']))]


def visible_item(kind, item_id):
    from . import store
    row = store.get_playable(kind, item_id)
    if not row:
        raise HTTPException(404, "内容不存在")
    return row


def context_for(session):
    if not session:
        with _connection() as c:
            revision = c.execute("SELECT revision FROM protection_settings WHERE id=1").fetchone()[0]
        return ContentAccessContext(generation=revision)
    return ContentAccessContext(session.get("mode", "normal"), session.get("token_hash", ""),
                                session.get("generation", 0),
                                session.get("management_until", 0) > time.time())


def check_lease(key, generation, *, management=False):
    """Used by long-lived byte streams and cast URLs, not just new requests."""
    if not key:
        if management:
            return False
        with _connection() as c:
            return c.execute("SELECT revision FROM protection_settings WHERE id=1").fetchone()[0] == generation
    session = session_by_hash(key)
    return bool(session and session["generation"] == generation and
                (not management or (session["mode"] != "children" and session["management_until"] > time.time())) and
                (session["mode"] != "private" or max(session["last_activity"] + IDLE_SECONDS,
                                                       session["playback_until"]) > time.time()))


def management_path(path, method):
    """Explicit browsing/playback allowlist; new management APIs default protected."""
    if path.startswith("/api/viewing/"):
        return False
    if path in {"/api/online/catalog/sources", "/api/online/catalog/search", "/api/online/catalog/detail"}:
        return method != "GET"
    if path.startswith("/api/online/state"):
        item = r"itm_[0-9a-f]{32}"
        if method == "GET" and (path == "/api/online/state/library"
                                or re.fullmatch(rf"/api/online/state/items/{item}", path)):
            return False
        if method == "PUT" and re.fullmatch(rf"/api/online/state/items/{item}/favorite", path):
            return False
        if method == "DELETE" and re.fullmatch(rf"/api/online/state/items/{item}/history", path):
            return False
        if method == "POST" and path in {"/api/online/state/progress", "/api/online/state/progress/claim"}:
            return False
        return True
    if path.startswith("/api/online/sessions"):
        opaque = r"[A-Za-z0-9_-]{32,128}"
        if method == "POST" and path == "/api/online/sessions":
            return False
        if method == "DELETE" and re.fullmatch(rf"/api/online/sessions/{opaque}", path):
            return False
        if method in {"GET", "HEAD"} and re.fullmatch(
                rf"/api/online/sessions/{opaque}/(?:media|resources/{opaque})", path):
            return False
        return True
    if method in {"GET", "HEAD"}:
        if path in {"/api/health", "/api/libraries", "/api/media-libraries"}:
            return False
        if path.startswith(("/api/stream/previews/jobs", "/api/stream/previews/queue",
                            "/api/stream/previews/auto", "/api/stream/previews/completed", "/api/stream/previews/failed", "/api/stream/prewarm/",
                            "/api/collections/suggest/backfill")):
            return True
        if path.startswith(("/api/movies", "/api/tv/shows", "/api/tv/episodes", "/api/persons/",
                            "/api/collections", "/api/search", "/api/facets", "/api/tv-client/",
                            "/api/tv/facets", "/api/tv/suggest", "/api/tv/stats", "/api/tv/recent-played", "/api/tv/updates",
                            "/api/tv/cast-avatar", "/api/tv/extras/", "/api/stream/", "/api/cast/")):
            return bool(re.search(r"/(files|organize-hint|posters|poster-thumb|debug)$", path))
        return path.startswith("/api/")
    if path.startswith("/api/stream/"):
        return not (re.fullmatch(r"/api/stream/\d+/(sessions|decide|previews)", path) or
                    path in {"/api/stream/client-check", "/api/stream/versions", "/api/stream/progress"} or
                    re.fullmatch(r"/api/stream/sessions/[^/]+(?:/ping)?", path))
    if path.startswith("/api/cast/"):
        return False
    if path == "/api/ai/search":
        return False
    if path == "/api/persons/ensure" or re.fullmatch(r"/api/persons/\d+/refresh", path):
        return False
    if re.fullmatch(r"/api/tv/(shows|episodes)/\d+/(?:seasons/\d+/)?watched", path):
        return False
    return path.startswith("/api/")
