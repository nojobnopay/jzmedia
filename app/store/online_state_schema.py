"""P3 device/mode state, separate from NAS playback_progress and media rows."""


ONLINE_STATE_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS online_state_scopes (
      owner_hash TEXT NOT NULL,
      scope TEXT NOT NULL CHECK(scope IN ('normal','private')),
      revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
      PRIMARY KEY(owner_hash,scope)
    )""",
    """CREATE TABLE IF NOT EXISTS online_favorites (
      owner_hash TEXT NOT NULL,
      scope TEXT NOT NULL CHECK(scope IN ('normal','private')),
      item_id TEXT NOT NULL REFERENCES online_items(id) ON DELETE CASCADE,
      created_at REAL NOT NULL,
      PRIMARY KEY(owner_hash,scope,item_id)
    )""",
    """CREATE TABLE IF NOT EXISTS online_progress (
      owner_hash TEXT NOT NULL,
      scope TEXT NOT NULL CHECK(scope IN ('normal','private')),
      identity_key TEXT NOT NULL,
      source_id TEXT NOT NULL REFERENCES online_sources(id) ON DELETE RESTRICT,
      item_id TEXT NOT NULL REFERENCES online_items(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('movie','episode')),
      episode_id TEXT REFERENCES online_episodes(id) ON DELETE CASCADE,
      line_id TEXT,
      position REAL NOT NULL DEFAULT 0 CHECK(position>=0),
      duration REAL CHECK(duration IS NULL OR duration>0),
      paused INTEGER NOT NULL DEFAULT 1 CHECK(paused IN (0,1)),
      completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
      updated_at REAL NOT NULL DEFAULT 0,
      writer_id TEXT NOT NULL,
      writer_session_id TEXT NOT NULL,
      writer_revision INTEGER NOT NULL CHECK(writer_revision>=1),
      writer_generation INTEGER NOT NULL,
      writer_protection_revision INTEGER NOT NULL,
      writer_scope_revision INTEGER NOT NULL,
      writer_seq INTEGER NOT NULL DEFAULT 0 CHECK(writer_seq>=0),
      writer_expires_at REAL NOT NULL,
      writer_digest TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(owner_hash,scope,identity_key),
      UNIQUE(writer_id),
      CHECK((kind='movie' AND episode_id IS NULL) OR (kind='episode' AND episode_id IS NOT NULL))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_online_favorites_owner ON online_favorites(owner_hash,scope,created_at DESC,item_id)",
    "CREATE INDEX IF NOT EXISTS idx_online_progress_owner ON online_progress(owner_hash,scope,updated_at DESC,item_id)",
    "CREATE INDEX IF NOT EXISTS idx_online_progress_item ON online_progress(owner_hash,scope,item_id,updated_at DESC,identity_key)",
)
