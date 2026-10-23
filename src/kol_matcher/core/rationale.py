from kol_matcher.core.scoring import ANCHORS, log_scale, scale
from kol_matcher.schemas.creator import CreatorFeatures
from kol_matcher.schemas.scoring import Flag

# Template sentences over computed features only (no LLM): deterministic, and every claim
# maps to a number in the result.


def rationale(f: CreatorFeatures, flags: list[Flag] = ()) -> str:
    parts = [_relevance(f), _performance(f), _audience(f)]
    if caveat := _credibility(f):
        parts.append(caveat)
    if flags:
        parts.append("review flags: " + ", ".join(dict.fromkeys(fl.type.value for fl in flags)))
    return "; ".join(parts) + "."


def _relevance(f: CreatorFeatures) -> str:
    if f.top_theme is None:
        return "few clips match the brand themes"
    return f"{f.on_theme_share:.0%} of clips match '{f.top_theme}'"


def _performance(f: CreatorFeatures) -> str:
    # Each metric gets its own label from its own scale, so a good rate is never called weak
    # because reach is low.
    er_level = _level(scale(f.er_by_impression, *ANCHORS["er_by_impression"]))
    reach_level = _level(log_scale(f.impressions_per_follower, *ANCHORS["impressions_per_follower"]))
    ipf = f.impressions_per_follower
    if ipf >= 1:
        reach = f"clips reach {ipf:.1f}x their follower count"
    elif ipf < 0.01:
        reach = "a typical clip is seen by under 1% of followers"
    else:
        reach = f"a typical clip is seen by {ipf:.0%} of followers"
    return (f"engagement rate {f.er_by_impression:.1f}% by impression ({er_level}), "
            f"{reach} ({reach_level} reach)")


def _audience(f: CreatorFeatures) -> str:
    c = f.comment_stats
    if c is None or c.n_comments == 0:
        return "audience not measured (no comments fetched)"
    return (f"audience (inferred from {c.n_comments} comments): {c.thai_share:.0%} Thai, "
            f"{c.purchase_intent_rate:.0%} purchase intent")


def _credibility(f: CreatorFeatures) -> str | None:
    """Only the caveats worth a human's attention; nothing when credibility looks normal."""
    notes = []
    if f.sponsored_ratio >= 0.5:
        notes.append(f"{f.sponsored_ratio:.0%} of recent clips are ads")
    if f.days_since_post > 14:
        notes.append(f"last post {f.days_since_post} days ago")
    if f.view_consistency < 0.3:
        notes.append("views vary a lot between clips")
    return ", ".join(notes) or None


def _level(value: float) -> str:
    return "high" if value >= 0.7 else "average" if value >= 0.4 else "low"
