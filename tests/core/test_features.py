from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from kol_matcher.config import Settings
from kol_matcher.core import features as features_mod
from kol_matcher.core.features import comment_stats, engagement_of, pool_features
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.schemas.creator import Creator, EnrichedCreator, Tier, Video
from kol_matcher.storage.db import connect

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PROFILE = BrandProfile(
    brand_name="Yuedpao", category="clothing", products=["t-shirts"], tone="casual", price_tier="mid",
    target_audience="Thai adults", content_themes=["outfits", "running"],
    keywords_th=["เสื้อยืด"], keywords_en=["tee"],
)
# Fake embedding space: axis 0 = outfits, axis 1 = running, axis 2 = unrelated.
VECTORS = {
    "outfits": [1, 0, 0], "running": [0, 1, 0],
    "ootd today": [1, 0, 0], "office look": [0.9, 0.1, 0], "my 5k run": [0, 1, 0],
    "cooking": [0, 0, 1], "cat video": [0, 0.2, 1],
}


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    def embed(_con, _settings, texts):
        m = np.array([VECTORS[t] for t in texts], dtype=float)
        return m / np.linalg.norm(m, axis=1, keepdims=True)
    monkeypatch.setattr(features_mod.embeddings, "embed", embed)


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


def vid(i, caption, views=1000, likes=100, comments=10, shares=0, saves=0, days_ago=1, ad=False):
    return Video(id=str(i), url=f"u{i}", caption=caption, created_at=NOW - timedelta(days=days_ago),
                 views=views, likes=likes, comments=comments, shares=shares, saves=saves, is_ad=ad)


def enriched(handle, videos, followers=10_000):
    c = Creator(handle=handle, followers=followers, tier=Tier.micro, profile_url="u", scraped_at=NOW)
    return EnrichedCreator(creator=c, videos=videos)


def one(videos, comments=None, followers=10_000):
    [f] = pool_features(None, Settings(_env_file=None), PROFILE, [enriched("x", videos, followers)],
                        comments={"x": comments} if comments is not None else None, now=NOW)
    return f


# --- relevance -----------------------------------------------------------------------------

def test_relevance_on_theme_creator():
    f = one([vid(1, "ootd today"), vid(2, "office look"), vid(3, "ootd today"), vid(4, "cooking")])
    assert f.on_theme_share == pytest.approx(0.75)
    assert f.top_theme == "outfits"
    assert f.relevance_top3 > 0.9


def test_relevance_off_theme_creator():
    f = one([vid(1, "cooking"), vid(2, "cat video")])
    assert f.on_theme_share == 0
    assert f.top_theme is None
    assert f.relevance_top3 < 0.3


def test_top_theme_is_most_frequent_match():
    f = one([vid(1, "my 5k run"), vid(2, "my 5k run"), vid(3, "ootd today")])
    assert f.top_theme == "running"


def test_clips_without_caption_ignored_for_relevance():
    f = one([vid(1, "ootd today"), vid(2, ""), vid(3, "  ")])
    assert f.on_theme_share == 1.0


def test_no_captions_at_all():
    f = one([vid(1, ""), vid(2, "")])
    assert (f.relevance_top3, f.on_theme_share, f.top_theme) == (0.0, 0.0, None)


# --- performance and credibility ----------------------------------------------------------

def test_engagement_counts_likes_comments_shares_saves_not_plays():
    assert engagement_of(vid(1, "", views=99_999, likes=10, comments=2, shares=3, saves=5)) == 20


def test_er_by_impression_matches_wisesight_example():
    # Article: 1,200 engagements / 30,000 base x 100 = 4%. Here over three clips.
    f = one([vid(1, "", views=10_000, likes=300, comments=50, shares=30, saves=20),
             vid(2, "", views=15_000, likes=500, comments=60, shares=20, saves=20),
             vid(3, "", views=5_000, likes=150, comments=20, shares=20, saves=10)])
    assert f.er_by_impression == pytest.approx(4.0)


def test_er_by_impression_is_total_over_total_not_mean_of_rates():
    # Rates per clip: 10% and 1%. Total/total weights by plays: 1,010 / 100,100 x 100 = 1.01%.
    f = one([vid(1, "", views=100, likes=10, comments=0), vid(2, "", views=100_000, likes=1_000, comments=0)])
    assert f.er_by_impression == pytest.approx(1_010 / 100_100 * 100)


def test_er_by_follower_uses_engagement_per_clip():
    f = one([vid(1, "", likes=300, comments=0), vid(2, "", likes=100, comments=0)], followers=10_000)
    assert f.er_by_follower == pytest.approx(2.0)   # mean 200 per clip / 10,000 x 100


def test_impression_and_engagement_medians():
    f = one([vid(1, "", views=1000, likes=90, comments=10), vid(2, "", views=3000, likes=150, comments=0),
             vid(3, "", views=2000, likes=100, comments=0, shares=100)])
    assert f.median_impressions == 2000
    assert f.median_engagement == 150


def test_impressions_per_follower():
    f = one([vid(1, "", views=1_000), vid(2, "", views=6_000), vid(3, "", views=2_000)], followers=4_000)
    assert f.impressions_per_follower == pytest.approx(0.5)   # median 2,000 / 4,000


def test_er_by_follower_is_roughly_er_by_impression_times_reach_proxy():
    # Why er_by_follower is display-only: with steady views it equals the product of the two scored metrics.
    f = one([vid(i, "", views=2_000, likes=100, comments=20) for i in range(3)], followers=5_000)
    assert f.er_by_follower == pytest.approx(f.er_by_impression * f.impressions_per_follower)


def test_zero_views_and_followers_do_not_crash():
    f = one([vid(1, "", views=0)], followers=0)
    assert f.er_by_impression == 0 and f.er_by_follower == 0 and f.view_consistency == 0
    assert f.impressions_per_follower == 0


def test_view_consistency():
    steady = one([vid(i, "", views=1000) for i in range(5)])
    spiky = one([vid(1, "", views=100), vid(2, "", views=100), vid(3, "", views=50_000)])
    assert steady.view_consistency == 1.0
    assert spiky.view_consistency < 0.5


def test_recency_and_sponsored_ratio():
    f = one([vid(1, "", days_ago=3, ad=True), vid(2, "", days_ago=9), vid(3, "", days_ago=20, ad=True)])
    assert f.days_since_post == 3
    assert f.sponsored_ratio == pytest.approx(2 / 3)


# --- comments --------------------------------------------------------------------------------

def test_comment_stats_from_real_comments():
    stats = comment_stats([
        "เวลาวิ่งลงเต็มเท้ามั้ยคะ",
        "พิกัดรองเท้าคู่นี้ค่ะ",
        "ซื้อที่ไหนคะ",
        "nice!!",
        "🔥🔥🔥",
        None,                      # sticker-only reply
        "ซื้อที่ไหนคะ",            # duplicate
    ])
    assert stats.n_comments == 7
    assert stats.thai_share == pytest.approx(4 / 7)
    assert stats.purchase_intent_rate == pytest.approx(3 / 7)
    assert stats.emoji_only_rate == pytest.approx(2 / 7)
    assert stats.duplicate_rate == pytest.approx(1 - 4 / 5)


def test_comment_stats_empty():
    stats = comment_stats([])
    assert stats.n_comments == 0 and stats.thai_share == 0


def test_comments_optional():
    assert one([vid(1, "ootd today")]).comment_stats is None
    assert one([vid(1, "ootd today")], comments=["พิกัด"]).comment_stats.purchase_intent_rate == 1.0


def test_pool_embeds_once(monkeypatch):
    calls = []
    original = features_mod.embeddings.embed
    monkeypatch.setattr(features_mod.embeddings, "embed", lambda c, s, t: calls.append(t) or original(c, s, t))
    pool = [enriched("a", [vid(1, "ootd today")]), enriched("b", [vid(2, "ootd today"), vid(3, "cooking")])]
    out = pool_features(None, Settings(_env_file=None), PROFILE, pool, now=NOW)
    assert [f.handle for f in out] == ["a", "b"]
    assert len(calls) == 1
    assert calls[0] == ["outfits", "running", "cooking", "ootd today"]  # themes, then unique captions
