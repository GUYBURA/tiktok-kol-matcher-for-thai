import httpx
import pytest

from kol_matcher.config import Settings
from kol_matcher.core import discover as discover_mod
from kol_matcher.core.discover import MAX_QUERIES, default_queries, discover
from kol_matcher.core.thai import has_thai
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.storage.db import connect

PROFILE = BrandProfile(
    brand_name="Yuedpao",
    category="clothing",
    products=["t-shirts"],
    tone="casual, friendly",
    price_tier="mid",
    target_audience="Thai adults 18-35",
    content_themes=["everyday outfits"],
    keywords_th=["เสื้อยืด", "แต่งตัว"],
    keywords_en=["basic tee"],
    hashtags=["#ootd"],
)


def item(handle, video_id, caption="", bio="", lang="un", fans=20_000):
    return {
        "id": video_id,
        "text": caption,
        "textLanguage": lang,
        "createTimeISO": "2026-10-20T10:00:00.000Z",
        "webVideoUrl": f"https://www.tiktok.com/@{handle}/video/{video_id}",
        "playCount": 1000, "diggCount": 100, "commentCount": 5, "shareCount": 1,
        "authorMeta": {"name": handle, "signature": bio, "fans": fans},
    }


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def fake_search(monkeypatch):
    """Map query -> items (or an Exception to raise). Records the queries searched."""
    calls = []

    def install(results):
        def search(_cur, _settings, query):
            calls.append(query)
            r = results.get(query, [])
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(discover_mod.apify_scraper, "search_tiktok", search)
        return calls

    return install


def settings():
    return Settings(_env_file=None)


def test_has_thai():
    assert has_thai("เสื้อยืด basic")
    assert not has_thai("basic tee")
    assert not has_thai(None) and not has_thai("")


def test_groups_clips_by_creator_and_records_queries(con, fake_search):
    fake_search({
        "เสื้อยืด": [item("mint", "1", "เสื้อยืดตัวโปรด"), item("bank", "2", "ลุคง่ายๆ")],
        "แต่งตัว": [item("mint", "3", "แต่งตัวไปทำงาน")],
    })
    result = discover(con, settings(), PROFILE, ["เสื้อยืด", "แต่งตัว"])
    mint = result.candidates[0]
    assert mint.creator.handle == "mint"
    assert mint.found_by == ["เสื้อยืด", "แต่งตัว"]
    assert [v.id for v in mint.videos] == ["1", "3"]
    assert [c.creator.handle for c in result.candidates] == ["mint", "bank"]
    assert result.videos_seen == 3


def test_same_video_from_two_queries_counted_once_per_creator(con, fake_search):
    fake_search({"a": [item("mint", "1", "เสื้อ")], "b": [item("mint", "1", "เสื้อ")]})
    [mint] = discover(con, settings(), PROFILE, ["a", "b"]).candidates
    assert mint.found_by == ["a", "b"]
    assert len(mint.videos) == 1


def test_thai_filter(con, fake_search):
    fake_search({"q": [
        item("thai_caption", "1", caption="ใส่สบายมาก"),
        item("thai_bio", "2", caption="ootd", bio="สายแฟชั่น"),
        item("thai_lang", "3", caption="ootd", lang="th"),
        item("foreign", "4", caption="ootd #fyp", bio="NYC", lang="en"),
    ]})
    result = discover(con, settings(), PROFILE, ["q"])
    assert {c.creator.handle for c in result.candidates} == {"thai_caption", "thai_bio", "thai_lang"}
    assert result.non_thai_filtered == 1


def test_default_queries_come_from_profile(con, fake_search):
    calls = fake_search({})
    result = discover(con, settings(), PROFILE)
    assert result.queries == ["เสื้อยืด", "แต่งตัว", "basic tee", "#ootd"]
    assert sorted(calls) == sorted(result.queries)
    assert default_queries(PROFILE) == result.queries


def test_queries_deduplicated_and_blank_dropped(con, fake_search):
    calls = fake_search({})
    discover(con, settings(), PROFILE, ["a", " a ", "", "b"])
    assert sorted(calls) == ["a", "b"]


def test_too_many_explicit_queries_rejected(con, fake_search):
    calls = fake_search({})
    with pytest.raises(ValueError, match="At most"):
        discover(con, settings(), PROFILE, [f"q{i}" for i in range(MAX_QUERIES + 1)])
    assert calls == []


def test_no_queries_rejected(con, fake_search):
    fake_search({})
    with pytest.raises(ValueError):
        discover(con, settings(), PROFILE, ["", "  "])


def test_failed_query_reported_others_kept(con, fake_search):
    fake_search({"ok": [item("mint", "1", "เสื้อ")], "bad": httpx.ConnectError("down")})
    result = discover(con, settings(), PROFILE, ["ok", "bad"])
    assert [c.creator.handle for c in result.candidates] == ["mint"]
    assert "ConnectError" in result.errors["bad"]


def test_all_queries_failing_raises(con, fake_search):
    fake_search({"a": httpx.ConnectError("down"), "b": RuntimeError("Apify run failed")})
    with pytest.raises(RuntimeError, match="All searches failed"):
        discover(con, settings(), PROFILE, ["a", "b"])


def test_malformed_items_skipped(con, fake_search):
    broken = {"id": "9", "text": "เสื้อ"}  # no authorMeta / url
    fake_search({"q": [broken, item("mint", "1", "เสื้อ")]})
    result = discover(con, settings(), PROFILE, ["q"])
    assert [c.creator.handle for c in result.candidates] == ["mint"]
    assert result.videos_seen == 1


def test_ranking_is_deterministic(con, fake_search):
    results = {
        "a": [item("zed", "1", "เสื้อ"), item("amy", "2", "เสื้อ")],
        "b": [item("bob", "3", "เสื้อ"), item("bob", "4", "เสื้อ")],
    }
    fake_search(results)
    first = [c.creator.handle for c in discover(con, settings(), PROFILE, ["a", "b"]).candidates]
    second = [c.creator.handle for c in discover(con, settings(), PROFILE, ["a", "b"]).candidates]
    assert first == second == ["bob", "amy", "zed"]  # same queries and followers: more clips, then handle


def test_ranking_order_queries_then_followers(con, fake_search):
    fake_search({
        "a": [item("nano_both", "1", "เสื้อ", fans=4_000), item("big", "2", "เสื้อ", fans=150_000),
              item("small", "3", "เสื้อ", fans=12_000)],
        "b": [item("nano_both", "4", "เสื้อ", fans=4_000)],
    })
    ranked = [c.creator.handle for c in discover(con, settings(), PROFILE, ["a", "b"]).candidates]
    assert ranked == ["nano_both", "big", "small"]
