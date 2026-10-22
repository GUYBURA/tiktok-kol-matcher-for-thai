import httpx
import pytest

from kol_matcher.config import Settings
from kol_matcher.sources import web_scraper
from kol_matcher.sources.web_scraper import MAX_CHARS, fetch_website
from kol_matcher.storage.db import connect

URL = "https://example-brand.co.th/about"

ARTICLE = " ".join(
    ["รองเท้าวิ่งสำหรับมือใหม่ ออกแบบมาเพื่อนักวิ่งที่เพิ่งเริ่มต้นซ้อม น้ำหนักเบาและรองรับแรงกระแทกได้ดี"] * 10
)
HTML = f"""<html><head><title>Example Running Co.</title></head><body>
<nav>Home | Shop | Contact</nav>
<article><h1>About us</h1><p>{ARTICLE}</p><p>{ARTICLE}</p></article>
<footer>Copyright 2026</footer>
</body></html>"""


def settings(ttl_hours=24, replay_mode=False):
    return Settings(_env_file=None, cache_ttl_hours=ttl_hours, replay_mode=replay_mode)


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def fake_get(monkeypatch):
    """Replace httpx.get; records each requested URL."""
    calls = []

    def install(status=200, text=HTML):
        def get(url, **kwargs):
            calls.append(url)
            return httpx.Response(status, text=text, request=httpx.Request("GET", url))

        monkeypatch.setattr(web_scraper.httpx, "get", get)
        return calls

    return install


def test_extracts_main_text(con, fake_get):
    fake_get()
    text = fetch_website(con, URL, settings())
    assert "รองเท้าวิ่งสำหรับมือใหม่" in text
    assert "Home | Shop | Contact" not in text


def test_second_call_served_from_cache(con, fake_get):
    calls = fake_get()
    first = fetch_website(con, URL, settings())
    second = fetch_website(con, URL, settings())
    assert first == second
    assert len(calls) == 1


def test_expired_cache_refetches(con, fake_get):
    calls = fake_get()
    fetch_website(con, URL, settings())
    fetch_website(con, URL, settings(ttl_hours=0))
    assert len(calls) == 2


def test_truncates_long_text(con, fake_get, monkeypatch):
    fake_get()
    monkeypatch.setattr(web_scraper.trafilatura, "extract", lambda html: "ก" * (MAX_CHARS + 500))
    assert len(fetch_website(con, URL, settings())) == MAX_CHARS


def test_http_error_raises_and_caches_nothing(con, fake_get):
    fake_get(status=404)
    with pytest.raises(httpx.HTTPStatusError):
        fetch_website(con, URL, settings())
    assert con.execute("SELECT count(*) FROM raw_responses").fetchone()[0] == 0


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com", "example.com"])
def test_rejects_non_http_urls(con, fake_get, url):
    calls = fake_get()
    with pytest.raises(ValueError):
        fetch_website(con, url, settings())
    assert calls == []


def test_replay_serves_cache_without_network(con, fake_get):
    calls = fake_get()
    expected = fetch_website(con, URL, settings())
    assert fetch_website(con, URL, settings(replay_mode=True)) == expected
    assert len(calls) == 1


def test_replay_miss_raises_without_network(con, fake_get):
    calls = fake_get()
    with pytest.raises(RuntimeError, match="Replay mode"):
        fetch_website(con, URL, settings(replay_mode=True))
    assert calls == []
