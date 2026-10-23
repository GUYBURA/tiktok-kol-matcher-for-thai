from kol_matcher.core.rationale import rationale
from kol_matcher.schemas.creator import CommentStats, CreatorFeatures, Tier
from kol_matcher.schemas.scoring import Flag, FlagType


def feats(top_theme="everyday outfits", on_theme=0.72, er=11.3, ipf=0.17, consistency=0.6, days=3,
          ads=0.2, comments=None):
    return CreatorFeatures(
        handle="x", tier=Tier.mid, relevance_top3=0.6, on_theme_share=on_theme, top_theme=top_theme,
        median_impressions=24_850, median_engagement=2_934, er_by_impression=er, er_by_follower=er * ipf,
        impressions_per_follower=ipf, view_consistency=consistency, days_since_post=days,
        sponsored_ratio=ads, comment_stats=comments,
    )


def text(f, flags=()):
    return rationale(f, list(flags))


def test_full_sentence():
    assert text(feats()) == (
        "72% of clips match 'everyday outfits'; "
        "engagement rate 11.3% by impression (high), a typical clip is seen by 17% of followers (average reach); "
        "audience not measured (no comments fetched)."
    )


def test_reach_beyond_followers():
    assert "clips reach 1.5x their follower count" in text(feats(ipf=1.53))


def test_no_matching_theme():
    assert text(feats(top_theme=None, on_theme=0.0)).startswith("few clips match the brand themes;")


def test_each_metric_labelled_on_its_own_scale():
    # Decent engagement with low reach: the rate must not be called low.
    t = text(feats(er=4.5, ipf=0.02))
    assert "engagement rate 4.5% by impression (average)" in t
    assert "seen by 2% of followers (low reach)" in t
    assert "engagement rate 0.3% by impression (low)" in text(feats(er=0.34, ipf=0.03))


def test_tiny_reach_not_shown_as_zero():
    t = text(feats(ipf=0.005))
    assert "under 1% of followers" in t and "0% of followers" not in t


def test_audience_when_comments_exist():
    c = CommentStats(n_comments=40, thai_share=0.9, purchase_intent_rate=0.15, duplicate_rate=0, emoji_only_rate=0)
    assert "audience (inferred from 40 comments): 90% Thai, 15% purchase intent" in text(feats(comments=c))


def test_credibility_caveats_only_when_relevant():
    assert "ads" not in text(feats(ads=0.2))
    t = text(feats(ads=1.0, days=30, consistency=0.2))
    assert "100% of recent clips are ads, last post 30 days ago, views vary a lot between clips" in t


def test_flags_listed_once_each():
    flags = [Flag(type=FlagType.shop_account, detail="a"), Flag(type=FlagType.shop_account, detail="b"),
             Flag(type=FlagType.high_sponsored_ratio, detail="c")]
    assert text(feats(), flags).endswith("review flags: shop_account, high_sponsored_ratio.")


def test_deterministic():
    assert text(feats()) == text(feats())
