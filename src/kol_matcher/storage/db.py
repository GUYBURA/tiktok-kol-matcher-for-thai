from datetime import datetime, timedelta

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_responses (
    actor       TEXT NOT NULL,       -- Apify actor ID or 'website'
    input_hash  TEXT NOT NULL,
    fetched_at  TIMESTAMP NOT NULL,
    payload     JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS creators (
    handle      TEXT PRIMARY KEY,
    nickname    TEXT,
    bio         TEXT,
    profile_url TEXT,
    region      TEXT
);

CREATE TABLE IF NOT EXISTS creator_snapshots (
    handle      TEXT NOT NULL,
    scraped_at  TIMESTAMP NOT NULL,
    followers   BIGINT NOT NULL,
    tier        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id          TEXT PRIMARY KEY,
    handle      TEXT NOT NULL,
    url         TEXT,
    caption     TEXT,
    created_at  TIMESTAMP,
    views       BIGINT,
    likes       BIGINT,
    comments    BIGINT,
    shares      BIGINT,
    is_ad       BOOLEAN,
    scraped_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS comment_stats (
    handle               TEXT NOT NULL,
    scraped_at           TIMESTAMP NOT NULL,
    n_comments           INTEGER,
    thai_share           DOUBLE,
    purchase_intent_rate DOUBLE,
    duplicate_rate       DOUBLE,
    emoji_only_rate      DOUBLE
);

CREATE TABLE IF NOT EXISTS embeddings (
    text_hash   TEXT NOT NULL,
    model       TEXT NOT NULL,
    vector      FLOAT[] NOT NULL,
    PRIMARY KEY (text_hash, model)
);

CREATE TABLE IF NOT EXISTS shortlist_log (
    saved_at     TIMESTAMP NOT NULL,
    profile_hash TEXT NOT NULL,
    objective    TEXT NOT NULL,
    handle       TEXT NOT NULL,
    rank         INTEGER,
    total_score  DOUBLE,
    notes        TEXT
);
"""

HANDLE_TABLES = ["creators", "creator_snapshots", "videos", "comment_stats", "shortlist_log"]

TIMESTAMP_COLUMNS = {
    "raw_responses": "fetched_at",
    "creator_snapshots": "scraped_at",
    "videos": "scraped_at",
    "comment_stats": "scraped_at",
}

def connect(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute(SCHEMA)
    return con

def delete_handle(con, handle: str) -> None:
    for table in HANDLE_TABLES:
        con.execute(f"DELETE FROM {table} WHERE handle = ?", [handle])
    con.execute("DELETE FROM raw_responses WHERE contains(payload::TEXT, ?)", [f'"{handle}"'])

def purge(con, older_than_days: int) -> None:
    cutoff = datetime.now() - timedelta(days=older_than_days)
    for table, column in TIMESTAMP_COLUMNS.items():
        con.execute(f"DELETE FROM {table} WHERE {column} < ?", [cutoff])