import math

from kol_matcher.schemas.creator import CreatorFeatures
from kol_matcher.schemas.scoring import WEIGHTS, Metric, Objective, ScoreBreakdown

# Fixed scales: value at the first anchor -> 0, at the second -> 1, linear in between, clipped.
# Judgment calls from the live Yuedpao sample and the BGE-M3 check; tune with the backtest (T7).
ANCHORS = {
    "relevance_top3": (0.40, 0.70),            # unrelated text <= ~0.42, related >= ~0.60
    "er_by_impression": (0.0, 10.0),           # percent; live pool ranged 0.3% to 11.3%
    "impressions_per_follower": (0.01, 1.0),   # log scale; live pool ranged 0.01 to 1.5
    "purchase_intent_rate": (0.0, 0.10),
    "days_since_post": (60, 7),                # 60 days = inactive limit -> 0; posted within a week -> 1
}


# Relevance gate. Below this, a creator ranks after every on-topic creator whatever their reach:
# without it, large off-topic accounts (comedy, travel) reached the top 10 on performance alone.
# 0.25 removed all of them in the Srichand backtest; one small sample, so tune with more brands.
MIN_RELEVANCE = 0.25


def scale(value: float, zero_at: float, one_at: float) -> float:
    """Linear map: zero_at -> 0, one_at -> 1, clipped to [0, 1]. Works for decreasing scales too."""
    return min(1.0, max(0.0, (value - zero_at) / (one_at - zero_at)))


def log_scale(value: float, zero_at: float, one_at: float) -> float:
    if value <= 0:
        return 0.0
    return scale(math.log10(value), math.log10(zero_at), math.log10(one_at))


def score(f: CreatorFeatures, objective: Objective = Objective.engagement) -> ScoreBreakdown:
    """Score one creator from their own features only. Same features -> same score, in any batch."""
    relevance = _mean(scale(f.relevance_top3, *ANCHORS["relevance_top3"]), f.on_theme_share)
    performance = _mean(
        scale(f.er_by_impression, *ANCHORS["er_by_impression"]),
        log_scale(f.impressions_per_follower, *ANCHORS["impressions_per_follower"]),
    )

    stats = f.comment_stats if f.comment_stats and f.comment_stats.n_comments > 0 else None
    audience = None
    credibility_parts = [
        f.view_consistency,
        scale(f.days_since_post, *ANCHORS["days_since_post"]),
        1 - f.sponsored_ratio,
    ]
    if stats:
        audience = _mean(stats.thai_share, scale(stats.purchase_intent_rate, *ANCHORS["purchase_intent_rate"]))
        credibility_parts.append(1 - _mean(stats.duplicate_rate, stats.emoji_only_rate))
    credibility = _mean(*credibility_parts)

    components = {"relevance": relevance, "performance": performance, "credibility": credibility}
    if audience is not None:
        components["audience"] = audience
    # Missing audience: drop it and rescale the remaining weights, rather than guess a value.
    weights = WEIGHTS[objective]
    total = sum(weights[k] * v for k, v in components.items()) / sum(weights[k] for k in components)

    return ScoreBreakdown(
        relevance=Metric(value=round(relevance, 4), basis="measured"),
        performance=Metric(value=round(performance, 4), basis="measured"),
        audience=Metric(value=round(audience, 4), basis="inferred") if audience is not None else None,
        credibility=Metric(value=round(credibility, 4), basis="measured"),
        total=round(total, 4),
    )


def rank(features: list[CreatorFeatures], objective: Objective = Objective.engagement
         ) -> list[tuple[CreatorFeatures, ScoreBreakdown]]:
    """On-topic creators first (relevance >= MIN_RELEVANCE), then highest total; ties broken by
    handle so the order is always the same. Totals are not changed by the gate."""
    scored = [(f, score(f, objective)) for f in features]
    return sorted(scored, key=lambda pair: (not is_relevant(pair[1]), -pair[1].total, pair[0].handle))


def is_relevant(s: ScoreBreakdown) -> bool:
    return s.relevance.value >= MIN_RELEVANCE


def _mean(*values: float) -> float:
    return sum(values) / len(values)
