"""Private presentation snapshots; public identities and progress contain no URLs."""


ONLINE_ARTWORK_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS online_item_artwork (
      item_id TEXT PRIMARY KEY REFERENCES online_items(id) ON DELETE CASCADE,
      poster_url TEXT NOT NULL DEFAULT '' CHECK(length(poster_url)<=8192),
      configuration_fingerprint TEXT NOT NULL CHECK(length(configuration_fingerprint)=64),
      updated_at REAL NOT NULL
    )""",
)
