"""Network configuration DDL shared by fresh databases and migration 33."""


NETWORK_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS network_profiles (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      mode TEXT NOT NULL CHECK(mode IN ('direct','proxy')),
      proxy_url TEXT NOT NULL DEFAULT '',
      credentials_encrypted TEXT NOT NULL DEFAULT '',
      connect_timeout_seconds REAL NOT NULL DEFAULT 5 CHECK(connect_timeout_seconds BETWEEN 1 AND 30),
      read_timeout_seconds REAL NOT NULL DEFAULT 15 CHECK(read_timeout_seconds BETWEEN 1 AND 120),
      max_connections INTEGER NOT NULL DEFAULT 4 CHECK(max_connections BETWEEN 1 AND 16),
      ca_file TEXT NOT NULL DEFAULT '',
      ca_dir TEXT NOT NULL DEFAULT '',
      revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS online_network_policies (
      source_id TEXT PRIMARY KEY REFERENCES online_sources(id) ON DELETE RESTRICT,
      rules_json TEXT NOT NULL CHECK(json_valid(rules_json) AND json_type(rules_json)='object'),
      revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
      updated_at INTEGER NOT NULL
    )""",
)
