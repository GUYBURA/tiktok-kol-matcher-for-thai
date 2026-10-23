import numpy as np
import pytest

from kol_matcher.config import Settings
from kol_matcher.sources import embeddings
from kol_matcher.sources.apify_scraper import search_tiktok
from kol_matcher.sources.embeddings import embed
from kol_matcher.storage import cache
from kol_matcher.storage.db import connect, export_fixtures, open_db

PAYLOAD = [{"id": "1", "text": "วิ่งมือใหม่", "authorMeta": {"name": "runner_th"}}]


def settings(tmp_path, replay_mode):
    return Settings(_env_file=None, replay_mode=replay_mode,
                    cache_path=str(tmp_path / "cache.duckdb"), fixtures_path=str(tmp_path / "fixtures"),
                    max_results_per_query=5)


def record(tmp_path, monkeypatch):
    """Simulate a live run: one cached search response plus one embedding, then export."""
    live = settings(tmp_path, replay_mode=False)
    con = open_db(live)
    run_input = {"searchQueries": ["วิ่งมือใหม่"]}
    cache.put(con, "some-actor", run_input, PAYLOAD)
    monkeypatch.setattr(embeddings.httpx, "post", _fake_ollama)
    vector = embed(con, live, ["วิ่งมือใหม่"])
    export_fixtures(con, live.fixtures_path)
    con.close()
    return run_input, vector


def _fake_ollama(url, json, **_kwargs):
    import httpx
    return httpx.Response(200, json={"embeddings": [[3.0, 4.0]] * len(json["input"])},
                          request=httpx.Request("POST", url))


def test_replay_serves_recorded_responses_and_embeddings(tmp_path, monkeypatch):
    run_input, vector = record(tmp_path, monkeypatch)
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: pytest.fail("network call in replay"))

    replay = settings(tmp_path, replay_mode=True)
    con = open_db(replay)
    assert cache.get(con, "some-actor", run_input, max_age_hours=None) == PAYLOAD
    np.testing.assert_allclose(embed(con, replay, ["วิ่งมือใหม่"]), vector)


def test_replay_never_writes_to_fixture_files(tmp_path, monkeypatch):
    record(tmp_path, monkeypatch)
    replay = settings(tmp_path, replay_mode=True)
    before = {p.name: p.stat().st_mtime_ns for p in (tmp_path / "fixtures").iterdir()}
    con = open_db(replay)
    cache.put(con, "another-actor", {"q": 1}, [{"x": 1}])  # write during the demo
    con.close()
    after = {p.name: p.stat().st_mtime_ns for p in (tmp_path / "fixtures").iterdir()}
    assert before == after
    assert open_db(replay).execute("SELECT count(*) FROM raw_responses").fetchone()[0] == 1


def test_replay_miss_raises_instead_of_calling_apify(tmp_path, monkeypatch):
    record(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="Replay mode"):
        search_tiktok(open_db(settings(tmp_path, replay_mode=True)), settings(tmp_path, True), "ไม่มีใน fixture")


def test_missing_fixtures_give_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing fixture"):
        open_db(settings(tmp_path, replay_mode=True))


def test_live_mode_uses_on_disk_cache(tmp_path):
    live = settings(tmp_path, replay_mode=False)
    open_db(live).close()
    assert (tmp_path / "cache.duckdb").exists()
    assert connect(live.cache_path).execute("SELECT count(*) FROM raw_responses").fetchone()[0] == 0


def test_old_cache_gets_saves_column(tmp_path):
    """A cache created before `saves` existed is migrated on connect, keeping its rows."""
    import duckdb
    path = str(tmp_path / "old.duckdb")
    old = duckdb.connect(path)
    old.execute("""CREATE TABLE videos (id TEXT PRIMARY KEY, handle TEXT NOT NULL, url TEXT, caption TEXT,
                   created_at TIMESTAMP, views BIGINT, likes BIGINT, comments BIGINT, shares BIGINT,
                   is_ad BOOLEAN, scraped_at TIMESTAMP NOT NULL)""")
    old.execute("INSERT INTO videos VALUES ('1', 'mint', 'u', 'c', NULL, 10, 1, 0, 0, false, now())")
    old.close()
    con = connect(path)
    assert con.execute("SELECT id, saves FROM videos").fetchall() == [("1", 0)]
