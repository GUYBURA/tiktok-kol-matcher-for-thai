from types import SimpleNamespace

import pytest

from kol_matcher.config import Settings
from kol_matcher.sources import apify_scraper
from kol_matcher.sources.apify_scraper import (
    COMMENT_FIELDS,
    COMMENTS_PER_VIDEO,
    FB_PAGE_ACTOR,
    FB_POSTS_ACTOR,
    MAX_CAPTION_CHARS,
    MAX_COMMENT_VIDEOS,
    MAX_POST_CHARS,
    MAX_POSTS,
    PAGE_FIELDS,
    POST_FIELDS,
    TIKTOK_ACTOR,
    TIKTOK_COMMENTS_ACTOR,
    TIKTOK_FIELDS,
    VIDEOS_PER_PROFILE,
    fetch_comments,
    fetch_facebook_page,
    fetch_facebook_posts,
    fetch_profile_videos,
    parse_creator,
    parse_video,
    run_actor,
    search_tiktok,
)
from kol_matcher.storage.db import connect

URL = "https://www.facebook.com/examplerunningco"
PAGE_ITEM = {
    "title": "Example Running Co.",
    "categories": ["Sportswear Store"],
    "intro": "รองเท้าวิ่งสำหรับมือใหม่",
    "info": ["Running shoes for beginners"],
    "likes": 12000,
    "followers": 15000,
    "website": "https://example-brand.co.th",
    "phone": "+66 2 000 0000",
    "email": "contact@example-brand.co.th",
}


def settings(ttl_hours=24, replay_mode=False, token="test-token"):
    return Settings(_env_file=None, cache_ttl_hours=ttl_hours, replay_mode=replay_mode, apify_token=token)


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def fake_apify(monkeypatch):
    """Replace ApifyClient; records (actor, run_input, max_items) for each actor run."""
    calls = []

    def install(items=(PAGE_ITEM,), run_fails=False):
        class FakeActor:
            def __init__(self, actor):
                self.actor = actor

            def call(self, run_input, max_items=None, **_kwargs):
                calls.append((self.actor, run_input, max_items))
                return None if run_fails else SimpleNamespace(default_dataset_id="ds1")

        class FakeClient:
            def __init__(self, _token):
                pass

            def actor(self, actor):
                return FakeActor(actor)

            def dataset(self, _dataset_id):
                return SimpleNamespace(list_items=lambda: SimpleNamespace(items=list(items)))

        monkeypatch.setattr(apify_scraper, "ApifyClient", FakeClient)
        return calls

    return install


def test_fetch_page_returns_whitelisted_fields(con, fake_apify):
    calls = fake_apify()
    page = fetch_facebook_page(con, settings(), URL)
    assert page["title"] == "Example Running Co."
    assert set(page) == set(PAGE_FIELDS)
    assert "phone" not in page and "email" not in page
    assert calls == [(FB_PAGE_ACTOR, {"startUrls": [{"url": URL}]}, 1)]


def test_second_call_served_from_cache(con, fake_apify):
    calls = fake_apify()
    first = fetch_facebook_page(con, settings(), URL)
    second = fetch_facebook_page(con, settings(), URL)
    assert first == second
    assert len(calls) == 1


def test_expired_cache_reruns_actor(con, fake_apify):
    calls = fake_apify()
    fetch_facebook_page(con, settings(), URL)
    fetch_facebook_page(con, settings(ttl_hours=0), URL)
    assert len(calls) == 2


def test_replay_serves_cache_without_apify(con, fake_apify):
    calls = fake_apify()
    expected = fetch_facebook_page(con, settings(), URL)
    assert fetch_facebook_page(con, settings(replay_mode=True), URL) == expected
    assert len(calls) == 1


def test_replay_miss_raises_without_apify(con, fake_apify):
    calls = fake_apify()
    with pytest.raises(RuntimeError, match="Replay mode"):
        fetch_facebook_page(con, settings(replay_mode=True), URL)
    assert calls == []


def test_missing_token_raises(con, fake_apify):
    calls = fake_apify()
    with pytest.raises(RuntimeError, match="APIFY_TOKEN"):
        run_actor(con, settings(token=None), FB_PAGE_ACTOR, {"startUrls": [{"url": URL}]})
    assert calls == []


def test_failed_run_raises_and_caches_nothing(con, fake_apify):
    fake_apify(run_fails=True)
    with pytest.raises(RuntimeError, match="Apify run failed"):
        fetch_facebook_page(con, settings(), URL)
    assert con.execute("SELECT count(*) FROM raw_responses").fetchone()[0] == 0


def test_empty_result_raises(con, fake_apify):
    fake_apify(items=())
    with pytest.raises(RuntimeError, match="No page data"):
        fetch_facebook_page(con, settings(), URL)


@pytest.mark.parametrize("url", ["https://example.com/page", "http://www.facebook.com/x", "facebook.com/x"])
def test_rejects_non_facebook_urls(con, fake_apify, url):
    calls = fake_apify()
    with pytest.raises(ValueError):
        fetch_facebook_page(con, settings(), url)
    assert calls == []


def cached_payloads(con):
    return [row[0] for row in con.execute("SELECT payload::TEXT FROM raw_responses").fetchall()]


def test_page_cache_holds_only_whitelisted_fields(con, fake_apify):
    fake_apify()
    fetch_facebook_page(con, settings(), URL)
    [payload] = cached_payloads(con)
    assert "phone" not in payload and "email" not in payload


POST_ITEM = {
    "text": "รองเท้าวิ่งรุ่นใหม่ " * 200,
    "time": "2026-10-13T15:34:09.000Z",
    "url": "https://www.facebook.com/examplerunningco/posts/1",
    "likes": 120,
    "comments": 8,
    "shares": 3,
    "paidPartnership": True,
    "collaborators": [{"name": "runner_th"}],
    "topComments": [{"text": "สวยมาก", "author": {"name": "Some Commenter", "gender": "FEMALE"}}],
    "media": [{"drm_info": "x" * 1000}],
}
PHOTO_ONLY_POST = {**POST_ITEM, "text": None, "url": "https://www.facebook.com/examplerunningco/posts/2"}


def test_fetch_posts_calls_actor_with_limits(con, fake_apify):
    calls = fake_apify(items=(POST_ITEM,))
    fetch_facebook_posts(con, settings(), URL)
    assert calls == [(FB_POSTS_ACTOR, {"startUrls": [{"url": URL}], "resultsLimit": MAX_POSTS}, MAX_POSTS)]


def test_posts_cache_drops_commenters_and_media(con, fake_apify):
    fake_apify(items=(POST_ITEM,))
    fetch_facebook_posts(con, settings(), URL)
    [payload] = cached_payloads(con)
    assert "Some Commenter" not in payload
    assert "topComments" not in payload and "media" not in payload


def test_posts_return_whitelisted_fields(con, fake_apify):
    fake_apify(items=(POST_ITEM,))
    [post] = fetch_facebook_posts(con, settings(), URL)
    assert set(post) == set(POST_FIELDS)
    assert post["paidPartnership"] is True


def test_post_text_truncated(con, fake_apify):
    fake_apify(items=(POST_ITEM,))
    [post] = fetch_facebook_posts(con, settings(), URL)
    assert len(post["text"]) == MAX_POST_CHARS


def test_posts_without_text_skipped(con, fake_apify):
    fake_apify(items=(POST_ITEM, PHOTO_ONLY_POST))
    posts = fetch_facebook_posts(con, settings(), URL)
    assert [p["url"] for p in posts] == [POST_ITEM["url"]]


TIKTOK_ITEM = {
    "id": "7699656964078341407",
    "text": "ซ้อมวิ่ง 5 กม. แรก #วิ่งมือใหม่ " + "viral " * 200,
    "textLanguage": "th",
    "createTimeISO": "2026-10-23T00:41:40.000Z",
    "webVideoUrl": "https://www.tiktok.com/@runner_th/video/7699656964078341407",
    "playCount": 748500,
    "diggCount": 152700,
    "commentCount": 202,
    "shareCount": 1071,
    "collectCount": 3575,
    "isAd": False,
    "isSponsored": True,
    "hasTikTokShopProduct": False,
    "isPinned": False,
    "hashtags": [{"name": "วิ่งมือใหม่"}],
    "authorMeta": {
        "name": "runner_th",
        "nickName": "Runner TH",
        "signature": "โค้ชวิ่งมือใหม่",
        "fans": 45000,
        "profileUrl": "https://www.tiktok.com/@runner_th",
    },
    "musicMeta": {"playUrl": "https://example.invalid/audio.mp3"},
    "videoMeta": {"subtitleLinks": [{"downloadLink": "https://example.invalid/sub"}]},
}


def tiktok_settings(**kw):
    s = settings(**kw)
    s.max_results_per_query = 5
    return s


def test_search_tiktok_calls_actor_with_query_and_limits(con, fake_apify):
    calls = fake_apify(items=(TIKTOK_ITEM,))
    search_tiktok(con, tiktok_settings(), "วิ่งมือใหม่")
    [(actor, run_input, max_items)] = calls
    assert actor == TIKTOK_ACTOR
    assert run_input["searchQueries"] == ["วิ่งมือใหม่"]
    assert run_input["resultsPerPage"] == 5 and max_items == 5
    assert run_input["shouldDownloadVideos"] is False
    assert run_input["proxyCountryCode"] == "TH"
    assert run_input["commentsPerPost"] == 0


def test_profile_handle_normalized_so_cache_is_shared(con, fake_apify):
    calls = fake_apify(items=(TIKTOK_ITEM,))
    fetch_profile_videos(con, settings(), "@runner_th")
    fetch_profile_videos(con, settings(), "runner_th")
    assert len(calls) == 1
    assert calls[0][1]["profiles"] == ["runner_th"]
    assert calls[0][2] == VIDEOS_PER_PROFILE
    assert calls[0][1]["excludePinnedPosts"] is True


def test_empty_handle_rejected(con, fake_apify):
    calls = fake_apify()
    with pytest.raises(ValueError):
        fetch_profile_videos(con, settings(), " @ ")
    assert calls == []


def test_tiktok_cache_holds_only_whitelisted_fields(con, fake_apify):
    fake_apify(items=(TIKTOK_ITEM,))
    [item] = search_tiktok(con, settings(), "วิ่งมือใหม่")
    assert set(item) == set(TIKTOK_FIELDS)
    [payload] = cached_payloads(con)
    assert "musicMeta" not in payload and "subtitleLinks" not in payload


def test_parse_video():
    video = parse_video(TIKTOK_ITEM)
    assert video.id == TIKTOK_ITEM["id"]
    assert video.views == 748500 and video.likes == 152700
    assert video.is_ad is True  # isSponsored counts as an ad
    assert len(video.caption) == MAX_CAPTION_CHARS
    assert video.created_at.year == 2026


def test_parse_video_missing_counts_default_to_zero():
    item = {**TIKTOK_ITEM, "playCount": None, "commentCount": None}
    video = parse_video(item)
    assert video.views == 0 and video.comments == 0


def test_parse_creator():
    creator = parse_creator(TIKTOK_ITEM)
    assert creator.handle == "runner_th"
    assert creator.bio == "โค้ชวิ่งมือใหม่"
    assert creator.followers == 45000
    assert creator.tier == "micro"


def test_parse_creator_builds_profile_url_when_missing():
    item = {**TIKTOK_ITEM, "authorMeta": {"name": "runner_th", "fans": None, "signature": None}}
    creator = parse_creator(item)
    assert creator.profile_url == "https://www.tiktok.com/@runner_th"
    assert creator.followers == 0 and creator.bio == ""


COMMENT_ITEM = {
    "text": "พิกัดรองเท้าคู่นี้ค่ะ",
    "videoWebUrl": "https://www.tiktok.com/@runner_th/video/1",
    "uniqueId": "private_commenter_99",
    "uid": "6852392378307003397",
    "avatarThumbnail": "https://example.invalid/avatar.jpg",
    "diggCount": 3,
}
VIDEO_URLS = [f"https://www.tiktok.com/@runner_th/video/{i}" for i in range(3)]


def test_comments_cache_holds_no_commenter_identity(con, fake_apify):
    fake_apify(items=(COMMENT_ITEM,))
    [comment] = fetch_comments(con, settings(), VIDEO_URLS)
    assert set(comment) == set(COMMENT_FIELDS)
    [payload] = cached_payloads(con)
    assert "private_commenter_99" not in payload
    assert "6852392378307003397" not in payload and "avatar" not in payload


def test_comments_actor_input_and_limits(con, fake_apify):
    calls = fake_apify(items=(COMMENT_ITEM,))
    fetch_comments(con, settings(), VIDEO_URLS)
    [(actor, run_input, max_items)] = calls
    assert actor == TIKTOK_COMMENTS_ACTOR
    assert run_input["commentsPerPost"] == COMMENTS_PER_VIDEO
    assert run_input["maxRepliesPerComment"] == 0
    assert max_items == len(VIDEO_URLS) * COMMENTS_PER_VIDEO


def test_comments_cache_key_ignores_url_order_and_duplicates(con, fake_apify):
    calls = fake_apify(items=(COMMENT_ITEM,))
    fetch_comments(con, settings(), VIDEO_URLS)
    fetch_comments(con, settings(), list(reversed(VIDEO_URLS)) + [VIDEO_URLS[0]])
    assert len(calls) == 1


def test_comments_video_cap(con, fake_apify):
    calls = fake_apify()
    too_many = [f"https://www.tiktok.com/@x/video/{i}" for i in range(MAX_COMMENT_VIDEOS + 1)]
    with pytest.raises(ValueError):
        fetch_comments(con, settings(), too_many)
    assert fetch_comments(con, settings(), []) == []
    assert calls == []


def test_concurrent_actor_runs_capped(con, monkeypatch):
    """Many threads, limit 2: never more than 2 Apify runs active at once; the rest wait, none fail."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    active, peak, lock = 0, 0, threading.Lock()

    class SlowActor:
        def call(self, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return SimpleNamespace(default_dataset_id="ds")

    class Client:
        def __init__(self, _token):
            pass

        def actor(self, _actor):
            return SlowActor()

        def dataset(self, _id):
            return SimpleNamespace(list_items=lambda: SimpleNamespace(items=[{"x": 1}]))

    monkeypatch.setattr(apify_scraper, "ApifyClient", Client)
    s = settings()
    s.apify_max_concurrent_runs = 2

    def one(i):
        with con.cursor() as cur:
            return run_actor(cur, s, "actor", {"i": i})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, range(8)))
    assert peak == 2
    assert results == [[{"x": 1}]] * 8
