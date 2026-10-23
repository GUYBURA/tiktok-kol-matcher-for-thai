import pytest

from kol_matcher.core.scoring import ANCHORS, log_scale, rank, scale, score
from kol_matcher.schemas.creator import CommentStats, CreatorFeatures, Tier
from kol_matcher.schemas.scoring import WEIGHTS, Objective


def feats(handle="x", top3=0.55, on_theme=0.5, er=5.0, ipf=0.1, consistency=0.5, days=7, ads=0.0,
          comments=None):
    return CreatorFeatures(
        handle=handle, tier=Tier.micro, relevance_top3=top3, on_theme_share=on_theme, top_theme=None,
        median_impressions=1000, median_engagement=50, er_by_impression=er, er_by_follower=er * ipf,
        impressions_per_follower=ipf, view_consistency=consistency, days_since_post=days,
        sponsored_ratio=ads, comment_stats=comments,
    )


def stats(thai=1.0, intent=0.05, dup=0.0, emoji=0.0, n=50):
    return CommentStats(n_comments=n, thai_share=thai, purchase_intent_rate=intent,
                        duplicate_rate=dup, emoji_only_rate=emoji)


# --- scales ----------------------------------------------------------------------------------

def test_scale_linear_and_clipped():
    assert scale(0.55, 0.40, 0.70) == pytest.approx(0.5)
    assert scale(0.30, 0.40, 0.70) == 0.0
    assert scale(0.90, 0.40, 0.70) == 1.0


def test_scale_decreasing_for_recency():
    assert scale(7, 60, 7) == 1.0
    assert scale(60, 60, 7) == 0.0
    assert scale(1, 60, 7) == 1.0       # posted yesterday: clipped at 1
    assert scale(90, 60, 7) == 0.0


def test_log_scale():
    assert log_scale(0.1, 0.01, 1.0) == pytest.approx(0.5)   # one decade of two
    assert log_scale(0.01, 0.01, 1.0) == 0.0
    assert log_scale(1.5, 0.01, 1.0) == 1.0
    assert log_scale(0, 0.01, 1.0) == 0.0


# --- components -------------------------------------------------------------------------------

def test_component_values():
    s = score(feats(top3=0.55, on_theme=0.5, er=5.0, ipf=0.1, consistency=0.5, days=7, ads=0.0,
                    comments=stats(thai=1.0, intent=0.05, dup=0.0, emoji=0.0)))
    assert s.relevance.value == pytest.approx(0.5)          # mean(0.5, 0.5)
    assert s.performance.value == pytest.approx(0.5)        # mean(5/10, log 0.5)
    assert s.audience.value == pytest.approx(0.75)          # mean(1.0, 0.05/0.10)
    assert s.credibility.value == pytest.approx(0.875)      # mean(0.5, 1, 1, 1)


def test_basis_labels():
    s = score(feats(comments=stats()))
    assert (s.relevance.basis, s.performance.basis, s.credibility.basis) == ("measured",) * 3
    assert s.audience.basis == "inferred"


def test_total_uses_objective_weights():
    s = score(feats(comments=stats()), Objective.conversion)
    w = WEIGHTS[Objective.conversion]
    expected = (w["relevance"] * s.relevance.value + w["performance"] * s.performance.value
                + w["audience"] * s.audience.value + w["credibility"] * s.credibility.value)
    assert s.total == pytest.approx(expected, abs=1e-4)


def test_missing_comments_drop_audience_and_reweight():
    s = score(feats(comments=None), Objective.engagement)
    assert s.audience is None
    w = WEIGHTS[Objective.engagement]
    kept = w["relevance"] + w["performance"] + w["credibility"]
    expected = (w["relevance"] * s.relevance.value + w["performance"] * s.performance.value
                + w["credibility"] * s.credibility.value) / kept
    assert s.total == pytest.approx(expected, abs=1e-4)


def test_zero_comments_treated_as_missing():
    assert score(feats(comments=stats(n=0))).audience is None


def test_comment_quality_lowers_credibility():
    clean = score(feats(comments=stats(dup=0.0, emoji=0.0))).credibility.value
    botty = score(feats(comments=stats(dup=0.6, emoji=0.8))).credibility.value
    assert botty < clean


def test_all_ads_account_scores_low():
    # The live all-ads account: top relevance but 0.3% ER, 0.03 impressions/follower, 100% ads.
    spam = score(feats(top3=0.66, on_theme=1.0, er=0.34, ipf=0.03, consistency=0.64, days=4, ads=1.0))
    solid = score(feats(top3=0.54, on_theme=0.46, er=5.1, ipf=1.53, consistency=0.51, days=12, ads=0.0))
    assert solid.total > spam.total


# --- individual scoring and ranking -----------------------------------------------------------

def test_score_independent_of_other_creators():
    a = feats("a", er=6.0)
    alone = rank([a])[0][1]
    in_strong_batch = {f.handle: s for f, s in rank([a, feats("b", er=10), feats("c", er=12)])}["a"]
    assert alone == in_strong_batch


def test_same_input_same_output():
    pool = [feats("a", er=3), feats("b", er=7), feats("c", er=7)]
    assert rank(pool) == rank(list(reversed(pool)))


def test_rank_order_and_tie_break():
    ranked = [f.handle for f, _ in rank([feats("zed", er=7), feats("amy", er=7), feats("low", er=1)])]
    assert ranked == ["amy", "zed", "low"]


def test_objective_changes_order():
    # reach:  relevance mean(0.17, 0.2) = 0.18, performance mean(0.4, 1.0) = 0.70
    # brand:  relevance mean(0.73, 0.7) = 0.72, performance mean(0.4, 0.15) = 0.28
    reach_star = feats("reach", top3=0.45, on_theme=0.2, er=4.0, ipf=1.5)
    on_brand = feats("brand", top3=0.62, on_theme=0.7, er=4.0, ipf=0.02)
    assert rank([reach_star, on_brand], Objective.awareness)[0][0].handle == "reach"
    assert rank([reach_star, on_brand], Objective.engagement)[0][0].handle == "brand"


def test_anchors_documented():
    assert set(ANCHORS) == {"relevance_top3", "er_by_impression", "impressions_per_follower",
                            "purchase_intent_rate", "days_since_post"}
