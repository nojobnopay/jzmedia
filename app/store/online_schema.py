"""Independent online catalogue DDL, shared by fresh databases and migration 32."""


ONLINE_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS online_sources (
      id TEXT PRIMARY KEY,
      provider TEXT NOT NULL CHECK(provider IN ('maccms','open_catalog')),
      name TEXT NOT NULL,
      api_base_url TEXT NOT NULL,
      allowed_hosts TEXT NOT NULL CHECK(json_valid(allowed_hosts) AND json_type(allowed_hosts)='array'),
      enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
      priority INTEGER NOT NULL DEFAULT 100 CHECK(priority BETWEEN 0 AND 1000),
      provider_options TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(provider_options) AND json_type(provider_options)='object'),
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS online_items (
      id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL REFERENCES online_sources(id) ON DELETE RESTRICT,
      external_id TEXT NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('movie','tv')),
      title TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL,
      UNIQUE(source_id,kind,external_id)
    )""",
    """CREATE TABLE IF NOT EXISTS online_episodes (
      id TEXT PRIMARY KEY,
      item_id TEXT NOT NULL REFERENCES online_items(id) ON DELETE RESTRICT,
      external_id TEXT NOT NULL,
      title TEXT NOT NULL DEFAULT '',
      season INTEGER CHECK(season IS NULL OR season>=0),
      episode INTEGER CHECK(episode IS NULL OR episode>=0),
      sort_order INTEGER NOT NULL DEFAULT 0 CHECK(sort_order>=0),
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL,
      UNIQUE(item_id,external_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_online_episodes_order ON online_episodes(item_id,sort_order,id)",
)
