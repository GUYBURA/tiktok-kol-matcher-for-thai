from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from kol_matcher.schemas import now_utc_naive

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
    scraped_at  TIMESTAMP NOT NULL,
    saves       BIGINT DEFAULT 0
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

# Tables that replay mode needs: every external response plus every embedding.
FIXTURE_TABLES = ["raw_responses", "embeddings"]


def connect(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute(SCHEMA)
    # Migration for caches created before `saves` existed.
    con.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS saves BIGINT DEFAULT 0")
    return con


def open_db(settings) -> duckdb.DuckDBPyConnection:
    """Live: the on-disk cache. Replay: a fresh in-memory DB loaded from committed fixtures,
    so a demo never writes to the fixture files."""
    if not settings.replay_mode:
        # DuckDB does not create parent directories; a fresh clone has no data/.
        Path(settings.cache_path).parent.mkdir(parents=True, exist_ok=True)
        return connect(settings.cache_path)
    con = connect(":memory:")
    load_fixtures(con, settings.fixtures_path)
    return con


def clock(con, settings) -> datetime:
    """The engine's "now". Replay mode uses the moment the fixtures were recorded, so recency
    scores and the inactive filter give the same result however old the fixtures are."""
    if settings.replay_mode:
        latest = con.execute("SELECT max(fetched_at) FROM raw_responses").fetchone()[0]
        if latest is not None:
            return latest.replace(tzinfo=UTC)
    return datetime.now(UTC)


def export_fixtures(con, directory: str) -> None:
    Path(directory).mkdir(parents=True, exist_ok=True)
    for table in FIXTURE_TABLES:
        target = (Path(directory) / f"{table}.parquet").as_posix()
        con.execute(f"COPY {table} TO '{target}' (FORMAT parquet)")


def load_fixtures(con, directory: str) -> None:
    for table in FIXTURE_TABLES:
        source = Path(directory) / f"{table}.parquet"
        if not source.exists():
            raise FileNotFoundError(f"Replay mode: missing fixture {source}")
        con.execute(f"INSERT INTO {table} SELECT * FROM read_parquet(?)", [source.as_posix()])

def delete_handle(con, handle: str) -> None:
    for table in HANDLE_TABLES:
        con.execute(f"DELETE FROM {table} WHERE handle = ?", [handle])
    con.execute("DELETE FROM raw_responses WHERE contains(payload::TEXT, ?)", [f'"{handle}"'])

def purge(con, older_than_days: int) -> None:
    cutoff = now_utc_naive() - timedelta(days=older_than_days)
    for table, column in TIMESTAMP_COLUMNS.items():
        con.execute(f"DELETE FROM {table} WHERE {column} < ?", [cutoff])