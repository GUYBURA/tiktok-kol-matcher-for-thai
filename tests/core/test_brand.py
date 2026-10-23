import httpx
import pytest

from kol_matcher.config import Settings
from kol_matcher.core import brand
from kol_matcher.core.brand import analyze_brand
from kol_matcher.storage.db import connect

FB_URL = "https://www.facebook.com/examplerunningco"
WEB_URL = "https://example-brand.co.th"
PAGE = {"title": "Example Running Co.", "category": "Sportswear Store"}
POSTS = [{"text": "รองเท้าวิ่งรุ่นใหม่", "likes": 120}]
WEBSITE = "รองเท้าวิ่งสำหรับมือใหม่ น้ำหนักเบา"


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def sources(monkeypatch):
    """Fake the three fetchers. `fail` names sources that raise; returns the list of calls made."""
    calls = []

    def install(fail=()):
        def fake(name, result):
            def fetch(_con, _settings, url):
                calls.append((name, url))
                if name in fail:
                    raise httpx.ConnectError(f"{name} unreachable")
                return result
            return fetch

        monkeypatch.setattr(brand.apify_scraper, "fetch_facebook_page", fake("facebook_page", PAGE))
        monkeypatch.setattr(brand.apify_scraper, "fetch_facebook_posts", fake("facebook_posts", POSTS))
        monkeypatch.setattr(brand.web_scraper, "fetch_website", fake("website_text", WEBSITE))
        return calls

    return install


def settings():
    return Settings(_env_file=None)


def test_all_sources_succeed(con, sources):
    sources()
    data = analyze_brand(con, settings(), FB_URL, WEB_URL)
    assert data.facebook_page == PAGE
    assert data.facebook_posts == POSTS
    assert data.website_text == WEBSITE
    assert data.errors == {}


def test_one_source_fails_others_still_returned(con, sources):
    sources(fail={"website_text"})
    data = analyze_brand(con, settings(), FB_URL, WEB_URL)
    assert data.facebook_page == PAGE and data.facebook_posts == POSTS
    assert data.website_text is None
    assert "ConnectError" in data.errors["website_text"]


def test_all_sources_fail_raises(con, sources):
    sources(fail={"facebook_page", "facebook_posts", "website_text"})
    with pytest.raises(RuntimeError, match="All brand sources failed"):
        analyze_brand(con, settings(), FB_URL, WEB_URL)


def test_only_requested_source_fails_raises(con, sources):
    sources(fail={"website_text"})
    with pytest.raises(RuntimeError):
        analyze_brand(con, settings(), website_url=WEB_URL)


def test_no_urls_raises(con, sources):
    calls = sources()
    with pytest.raises(ValueError):
        analyze_brand(con, settings())
    assert calls == []


def test_website_only_skips_facebook(con, sources):
    calls = sources()
    data = analyze_brand(con, settings(), website_url=WEB_URL)
    assert calls == [("website_text", WEB_URL)]
    assert data.facebook_page is None and data.facebook_posts == []


def test_invalid_url_is_caller_error_not_source_failure(con):
    # Real fetchers: the URL check runs before any network call.
    with pytest.raises(ValueError, match="Not a Facebook page URL"):
        analyze_brand(con, settings(), facebook_url="https://example.com/not-facebook")


def test_profile_schema_included(con, sources):
    sources()
    data = analyze_brand(con, settings(), website_url=WEB_URL)
    assert "content_themes" in data.profile_schema["properties"]


def test_sources_run_in_parallel(con, monkeypatch):
    import time

    def slow(result):
        def fetch(_con, _settings, _url):
            time.sleep(0.3)
            return result
        return fetch

    monkeypatch.setattr(brand.apify_scraper, "fetch_facebook_page", slow(PAGE))
    monkeypatch.setattr(brand.apify_scraper, "fetch_facebook_posts", slow(POSTS))
    monkeypatch.setattr(brand.web_scraper, "fetch_website", slow(WEBSITE))
    start = time.perf_counter()
    data = analyze_brand(con, settings(), FB_URL, WEB_URL)
    assert time.perf_counter() - start < 0.7  # sequential would take ~0.9s
    assert data.errors == {}


def test_parallel_sources_share_cache_safely(con, monkeypatch):
    """Real cache writes from three threads at once land in the same database."""
    from kol_matcher.storage import cache

    def writer(name):
        def fetch(cur, _settings, url):
            cache.put(cur, name, {"url": url}, [name])
            return [name] if name != "website" else name
        return fetch

    monkeypatch.setattr(brand.apify_scraper, "fetch_facebook_page", writer("page"))
    monkeypatch.setattr(brand.apify_scraper, "fetch_facebook_posts", writer("posts"))
    monkeypatch.setattr(brand.web_scraper, "fetch_website", writer("website"))
    analyze_brand(con, settings(), FB_URL, WEB_URL)
    assert con.execute("SELECT count(*) FROM raw_responses").fetchone()[0] == 3
