import re
from datetime import UTC, datetime

from kol_matcher.core.enrich import enrich
from kol_matcher.core.features import comment_stats, evidence_clips, pool_features
from kol_matcher.core.flags import content_flags
from kol_matcher.core.rationale import rationale
from kol_matcher.core.scoring import MIN_RELEVANCE, is_relevant, rank
from kol_matcher.core.stored import cached_comment_stats
from kol_matcher.schemas.brand import BrandProfile, profile_hash
from kol_matcher.schemas.creator import (
    CommentStats,
    EnrichedCreator,
    EvaluatedCreator,
    Evaluation,
    EvidenceClip,
    InactiveCreator,
)
from kol_matcher.schemas.scoring import Flag, FlagType, Objective
from kol_matcher.sources import apify_scraper

# Comments are the most expensive actor, so they are fetched only for conversion, and only for the
# top creators shown to the user: 3 creators x 2 videos = 6 videos = one comments run.
COMMENT_CREATORS = 3
COMMENT_VIDEOS_PER_CREATOR = 2


def evaluate(con, settings, profile: BrandProfile, handles: list[str],
             objective: Objective = Objective.engagement, now: datetime | None = None) -> Evaluation:
    """Enrich, score, flag, and explain creators for one brand and objective. Ranked best first."""
    now = now or datetime.now(UTC)
    enriched = enrich(con, settings, handles, now=now)
    errors = dict(enriched.errors)
    pool = enriched.creators
    inactive = [InactiveCreator(handle=e.creator.handle, profile_url=e.creator.profile_url, flags=e.flags)
                for e in enriched.inactive]
    result = Evaluation(profile_hash=profile_hash(profile), objective=objective.value, creators=[],
                        inactive=inactive, errors=errors)
    if not pool:
        return result

    features = pool_features(con, settings, profile, pool, now=now)
    for f in features:
        f.comment_stats = cached_comment_stats(con, f.handle)
    if objective == Objective.conversion:
        fresh = _fetch_comment_stats(con, settings, pool, [f for f, _ in rank(features, objective)],
                                     errors, now)
        for f in features:
            if f.handle in fresh:
                f.comment_stats = fresh[f.handle]
        result.comments_fetched_for = list(fresh)

    by_handle = {e.creator.handle: e for e in pool}
    for i, (f, s) in enumerate(rank(features, objective), 1):
        e = by_handle[f.handle]
        flags = e.flags + content_flags(e, profile)
        if not is_relevant(s):
            flags.append(Flag(type=FlagType.low_relevance,
                              detail=f"relevance {s.relevance.value:.2f} is below {MIN_RELEVANCE}: few clips "
                                     f"match the brand themes, ranked after on-topic creators"))
        result.creators.append(EvaluatedCreator(
            rank=i, handle=f.handle, nickname=e.creator.nickname, bio=e.creator.bio,
            profile_url=e.creator.profile_url, tier=f.tier, followers=e.creator.followers,
            score=s, metrics=f, flags=flags, rationale=rationale(f, flags),
            evidence=[EvidenceClip(url=v.url, caption=v.caption, views=v.views, similarity=sim)
                      for v, sim in evidence_clips(con, settings, profile, e)],
        ))
    return result


def _fetch_comment_stats(con, settings, pool: list[EnrichedCreator], ranked, errors: dict,
                         now: datetime) -> dict[str, CommentStats]:
    """One comments run for the top creators that have no stored comment stats yet."""
    by_handle = {e.creator.handle: e for e in pool}
    targets = [f.handle for f in ranked if f.comment_stats is None][:COMMENT_CREATORS]
    video_owner = {}
    for handle in targets:
        commented = sorted((v for v in by_handle[handle].videos if v.comments > 0),
                           key=lambda v: (-v.comments, v.id))[:COMMENT_VIDEOS_PER_CREATOR]
        video_owner.update({_video_id(v.url): handle for v in commented})
    if not video_owner:
        return {}
    urls = [v.url for h in targets for v in by_handle[h].videos if _video_id(v.url) in video_owner]
    try:
        comments = apify_scraper.fetch_comments(con, settings, urls)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 - scoring continues without comments; audience shows "not measured"
        errors["comments"] = f"{type(e).__name__}: {e}"
        return {}

    texts: dict[str, list[str | None]] = {}
    for c in comments:
        handle = video_owner.get(_video_id(c.get("videoWebUrl") or ""))
        if handle:
            texts.setdefault(handle, []).append(c.get("text"))
    stats = {h: comment_stats(t) for h, t in texts.items()}
    scraped_at = now.astimezone(UTC).replace(tzinfo=None)
    con.executemany("INSERT INTO comment_stats VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [[h, scraped_at, s.n_comments, s.thai_share, s.purchase_intent_rate, s.duplicate_rate,
                      s.emoji_only_rate] for h, s in stats.items()])
    return stats


def _video_id(url: str) -> str:
    # Match comments to videos by numeric id; the two actors may format URLs differently.
    m = re.search(r"/video/(\d+)", url)
    return m.group(1) if m else url
