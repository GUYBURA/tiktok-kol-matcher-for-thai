from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from kol_matcher.config import Settings
from kol_matcher.core import backtest as bt
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.sources import apify_scraper, embeddings
from kol_matcher.storage.db import connect

PROFILE = BrandProfile(
    brand_name="Yuedpao", category="clothing", products=["t-shirts"], tone="casual", price_tier="mid",
    target_audience="Thai adults", content_themes=["outfits"], keywords_th=["เสื้อยืด", "เสื้อยืดเยิดเปา"],
    keywords_en=["basic tee", "yuedpao tee"], hashtags=["#ootd"],
)
NOW = datetime.now(UTC)
SETTINGS = Settings(_env_file=None)


def item(handle, video_id, caption="แต่งตัว", likes=100, ad=False, hashtags=(), fans=20_000):
    return {
        "id": video_id, "text": caption, "textLanguage": "th",
        "createTimeISO": (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "webVideoUrl": f"https://www.tiktok.com/@{handle}/video/{video_id}",
        "playCount": 10_000, "diggCount": likes, "commentCount": 5, "shareCount": 1, "isAd": ad,
        "hashtags": [{"name": h} for h in hashtags],
        "authorMeta": {"name": handle, "signature": "", "fans": fans},
    }


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


def test_mentions_ignores_case_spaces_and_hash():
    assert bt.mentions("ใส่ #YuedPao ทุกวัน", ["yuedpao"])
    assert bt.mentions("Yued Pao tee", ["#yuedpao"])
    assert not bt.mentions("basic tee", ["yuedpao"])
    assert not bt.mentions("anything", ["#"])


def test_brand_queries_drops_queries_naming_the_brand():
    kept, excluded = bt.brand_queries(["เสื้อยืด", "yuedpao tee", "#YuedPao", "basic tee"], ["Yuedpao"])
    assert kept == ["เสื้อยืด", "basic tee"]
    assert excluded == ["yuedpao tee", "#YuedPao"]


def test_recall_at_k():
    hits, recall = bt.recall_at_k(["A", "b", "c", "d"], ["a", "d", "z"], k=2)
    assert hits == ["A"]
    assert recall == pytest.approx(1 / 3)


def test_ground_truth_rows(con, monkeypatch, as_batch, tmp_path):
    results = {
        "yuedpao": [item("fan", "1", "ชอบ yuedpao"), item("fan", "2", "รีวิว yuedpao", ad=True),
                    item("yuedpao_official", "3", "yuedpao ใหม่"), item("other", "4", "เสื้อยืด")],
        "#yuedpao": [item("tagged", "5", "ใส่สบาย", hashtags=["yuedpao", "ad"]),
                     {**item("en", "6", "yuedpao in english"), "textLanguage": "en"}],
    }
    monkeypatch.setattr(apify_scraper, "search_tiktok", as_batch(lambda _c, _s, q: results[q]))
    rows, errors = bt.build_ground_truth(con, SETTINGS, ["yuedpao"], exclude_handles=["@yuedpao_official"])  # queries: term + hashtag
    assert not errors
    # "other" never names the brand, "en" is not Thai, the brand's own account is excluded.
    assert [(r.handle, r.sponsored) for r in rows] == [("fan", True), ("tagged", True)]
    assert rows[0].video_url.endswith("/video/2")

    path = tmp_path / "gt" / "yuedpao.csv"
    rows[1].keep = "n"
    bt.write_ground_truth(rows, path)
    assert bt.load_ground_truth(path) == ["fan"]


def test_load_ground_truth_needs_handle_column(tmp_path):
    path = tmp_path / "gt.csv"
    path.write_text("name\nx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="handle"):
        bt.load_ground_truth(path)


def test_run_backtest(con, monkeypatch, as_batch):
    # Search order: s1..s6. Profile scrapes give gt creators g1, g2 the most likes, so the ranker
    # lifts them above search order. g3 is never found by search.
    handles = ["s1", "s2", "s3", "g1", "s4", "g2"]
    searched = []

    def search(_c, _s, q):
        searched.append(q)
        return [item(h, f"{h}-{q}", fans=50_000 if h.startswith("s") else 20_000) for h in handles]
    likes = {"g1": 2000, "g2": 1500}

    def profile(_c, _s, h):
        apify_scraper.usage.append({"actor": apify_scraper.TIKTOK_ACTOR, "items": 3, "proxy": False})
        return [item(h, f"{h}{i}", likes=likes.get(h, 50)) for i in range(3)]
    monkeypatch.setattr(apify_scraper, "search_tiktok", as_batch(search))
    monkeypatch.setattr(apify_scraper, "fetch_profile_videos", as_batch(profile))
    monkeypatch.setattr(embeddings, "embed", lambda c, s, texts: np.array([[1.0, 0.0] for _ in texts]))

    result = bt.run_backtest(con, SETTINGS, PROFILE, ["g1", "G2", "g3"], k=2, pool=6)
    assert "yuedpao tee" not in searched and "เสื้อยืดเยิดเปา" in searched   # Thai spelling not given
    assert result.excluded_queries == ["yuedpao tee"]
    assert result.evaluated == 6
    assert result.gt_discovered == ["g1", "g2"]
    assert result.hits == ["g1", "g2"]
    assert result.recall_at_k == pytest.approx(0.6667)
    assert result.discovery_order_hits == 0       # bigger s-creators come first in search order
    assert result.random_hits == pytest.approx(2 * 2 / 6, abs=0.01)
    assert result.apify_runs == 6
    assert result.est_cost_usd == pytest.approx(6 * (bt.PRICE_START + 3 * bt.PRICE_ITEM), abs=0.001)


def test_run_backtest_extra_brand_terms_and_own_account(con, monkeypatch, as_batch):
    monkeypatch.setattr(apify_scraper, "search_tiktok",
                        as_batch(lambda _c, _s, q: [item("yuedpao_th", "1"), item("g1", "2")]))
    monkeypatch.setattr(apify_scraper, "fetch_profile_videos",
                        as_batch(lambda _c, _s, h: [item(h, f"{h}{i}") for i in range(3)]))
    monkeypatch.setattr(embeddings, "embed", lambda c, s, texts: np.array([[1.0, 0.0] for _ in texts]))
    result = bt.run_backtest(con, SETTINGS, PROFILE, ["g1"], k=5, brand_terms=["เยิดเปา"],
                             exclude_handles=["yuedpao_th"])
    assert "เสื้อยืดเยิดเปา" in result.excluded_queries
    assert result.top_k == ["g1"]


def test_run_backtest_rejects_only_brand_queries(con):
    with pytest.raises(ValueError, match="names the brand"):
        bt.run_backtest(con, SETTINGS, PROFILE, ["g1"], queries=["Yuedpao", "#yuedpao"])


def test_max_cost_upper_bound():
    s = Settings(_env_file=None, max_results_per_query=30)
    cost = bt.max_cost_usd(s, n_queries=5, pool=10)
    assert cost == pytest.approx(5 * 30 * 0.005 + 10 * 30 * 0.0037 + 2 * 0.001, abs=0.01)


def test_run_backtest_seed_adds_missed_ground_truth(con, monkeypatch, as_batch):
    monkeypatch.setattr(apify_scraper, "search_tiktok",
                        as_batch(lambda _c, _s, q: [item("s1", "1"), item("g1", "2")]))
    monkeypatch.setattr(apify_scraper, "fetch_profile_videos", as_batch(
        lambda _c, _s, h: [item(h, f"{h}{i}", likes=2000 if h.startswith("g") else 50) for i in range(3)]))
    monkeypatch.setattr(embeddings, "embed", lambda c, s, texts: np.array([[1.0, 0.0] for _ in texts]))
    result = bt.run_backtest(con, SETTINGS, PROFILE, ["g1", "g2", "g3"], k=2, pool=2, seed=1)
    assert result.seeded == ["g2"]                  # g1 was already found; seed only adds missed ones
    assert result.evaluated == 3
    assert result.gt_ranks == [1, 2]
