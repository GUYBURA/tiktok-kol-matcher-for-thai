from datetime import UTC, datetime, timedelta

import httpx
import pytest

from kol_matcher.config import Settings
from kol_matcher.core import enrich as enrich_mod
from kol_matcher.core.enrich import INACTIVE_DAYS, enrich, normalize_handles
from kol_matcher.schemas.scoring import FlagType
from kol_matcher.storage.db import connect

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def video(handle, video_id, days_ago, bio="", fans=20_000, views=1000):
    created = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "id": video_id, "text": "แต่งตัวไปทำงาน", "createTimeISO": created,
        "webVideoUrl": f"https://www.tiktok.com/@{handle}/video/{video_id}",
        "playCount": views, "diggCount": 50, "commentCount": 5, "shareCount": 1,
        "authorMeta": {"name": handle, "signature": bio, "fans": fans},
    }


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def profiles(monkeypatch, as_batch):
    """Map handle -> items (or an Exception). Records the handles fetched."""
    calls = []

    def install(by_handle):
        def fetch(_cur, _settings, handle):
            calls.append(handle)
            r = by_handle.get(handle, [])
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(enrich_mod.apify_scraper, "fetch_profile_videos", as_batch(fetch))
        return calls

    return install


def settings(limit=20):
    return Settings(_env_file=None, max_creators_per_evaluate=limit)


def test_normalize_handles():
    assert normalize_handles(["@mint", "mint", " bank ", "", "@"]) == ["mint", "bank"]


def test_active_creator_enriched_newest_first(con, profiles):
    profiles({"mint": [video("mint", "1", 10), video("mint", "2", 1), video("mint", "3", 5)]})
    result = enrich(con, settings(), ["@mint"], now=NOW)
    [mint] = result.creators
    assert mint.creator.handle == "mint"
    assert [v.id for v in mint.videos] == ["2", "3", "1"]
    assert result.inactive == [] and result.errors == {}


def test_inactive_creator_filtered_and_flagged(con, profiles):
    profiles({
        "old": [video("old", "1", INACTIVE_DAYS + 5)],
        "edge": [video("edge", "2", INACTIVE_DAYS)],  # exactly 60 days: still active
    })
    result = enrich(con, settings(), ["old", "edge"], now=NOW)
    assert [c.creator.handle for c in result.creators] == ["edge"]
    [old] = result.inactive
    assert old.flags[-1].type == FlagType.inactive
    assert f"{INACTIVE_DAYS + 5} days" in old.flags[-1].detail


def test_account_flags_attached(con, profiles):
    profiles({"shop": [video("shop", "1", 1, bio="ขอบคุณที่อุดหนุนร้านของเรา")]})
    [shop] = enrich(con, settings(), ["shop"], now=NOW).creators
    assert [f.type for f in shop.flags] == [FlagType.shop_account]


def test_failed_and_empty_profiles_reported(con, profiles):
    profiles({"ok": [video("ok", "1", 1)], "down": httpx.ConnectError("down"), "ghost": []})
    result = enrich(con, settings(), ["ok", "down", "ghost"], now=NOW)
    assert [c.creator.handle for c in result.creators] == ["ok"]
    assert "ConnectError" in result.errors["down"]
    assert "No videos" in result.errors["ghost"]


def test_limit_enforced_before_any_fetch(con, profiles):
    calls = profiles({})
    with pytest.raises(ValueError, match="At most 2"):
        enrich(con, settings(limit=2), ["a", "b", "c"], now=NOW)
    assert calls == []


def test_no_handles_rejected(con, profiles):
    profiles({})
    with pytest.raises(ValueError):
        enrich(con, settings(), ["", "@"], now=NOW)


def test_results_stored_in_duckdb(con, profiles):
    profiles({"mint": [video("mint", "1", 1, bio="สายแฟชั่น", fans=45_000), video("mint", "2", 3)]})
    enrich(con, settings(), ["mint"], now=NOW)
    assert con.execute("SELECT handle, bio FROM creators").fetchall() == [("mint", "สายแฟชั่น")]
    assert con.execute("SELECT handle, followers, tier FROM creator_snapshots").fetchall() == [("mint", 45_000, "micro")]
    assert con.execute("SELECT count(*) FROM videos WHERE handle = 'mint'").fetchone()[0] == 2


def test_repeat_enrich_does_not_duplicate_rows(con, profiles):
    profiles({"mint": [video("mint", "1", 1), video("mint", "2", 3)]})
    enrich(con, settings(), ["mint"], now=NOW)
    enrich(con, settings(), ["mint"], now=NOW)
    assert con.execute("SELECT count(*) FROM creator_snapshots").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM videos").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM creators").fetchone()[0] == 1


def test_follower_change_appends_snapshot(con, profiles, monkeypatch):
    profiles({"mint": [video("mint", "1", 1, fans=45_000)]})
    enrich(con, settings(), ["mint"], now=NOW)
    profiles({"mint": [video("mint", "1", 1, fans=52_000)]})
    enrich(con, settings(), ["mint"], now=NOW)
    rows = con.execute("SELECT followers FROM creator_snapshots ORDER BY followers").fetchall()
    assert rows == [(45_000,), (52_000,)]


def test_video_timestamps_stored_as_utc(con, profiles):
    profiles({"mint": [video("mint", "1", 2)]})
    enrich(con, settings(), ["mint"], now=NOW)
    stored = con.execute("SELECT created_at FROM videos").fetchone()[0]
    assert stored == (NOW - timedelta(days=2)).replace(tzinfo=None)


def test_malformed_items_skipped(con, profiles):
    profiles({"mint": [{"id": "x"}, video("mint", "1", 1)]})
    [mint] = enrich(con, settings(), ["mint"], now=NOW).creators
    assert [v.id for v in mint.videos] == ["1"]
