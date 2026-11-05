"""Persistent, service-wide online downloads; separate from disposable caches."""

ONLINE_CACHE_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS online_cache_snapshots (
      id TEXT PRIMARY KEY,
      payload TEXT NOT NULL CHECK(json_valid(payload))
    )""",
    """CREATE TABLE IF NOT EXISTS online_cache_tasks (
      id TEXT PRIMARY KEY,
      identity_key TEXT NOT NULL UNIQUE,
      item_id TEXT NOT NULL,
      source_id TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('queued','downloading','paused','failed','ready','deleting')),
      payload TEXT NOT NULL CHECK(json_valid(payload)),
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_online_cache_queue ON online_cache_tasks(state,created_at,id)",
    "CREATE INDEX IF NOT EXISTS idx_online_cache_item ON online_cache_tasks(item_id)",
)
