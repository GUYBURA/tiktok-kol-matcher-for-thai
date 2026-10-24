
from pydantic import ValidationError

from kol_matcher.core.thai import has_thai
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.schemas.creator import Candidate, Discovery
from kol_matcher.sources import apify_scraper

MAX_QUERIES = 15


def default_queries(profile: BrandProfile) -> list[str]:
    return [*profile.keywords_th, *profile.keywords_en, *profile.hashtags]


def discover(con, settings, profile: BrandProfile, queries: list[str] | None = None) -> Discovery:
    """Search TikTok for each query, keep Thai creators, and group clips by creator.

    Explicit queries over MAX_QUERIES raise (the AI client should narrow them); the profile's
    default queries are cut to MAX_QUERIES in order: Thai keywords, English keywords, hashtags.
    """
    if queries is None:
        queries = default_queries(profile)[:MAX_QUERIES]
    queries = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))  # dedupe, keep order
    if not queries:
        raise ValueError("No search queries: pass queries or fill keywords/hashtags in the profile")
    if len(queries) > MAX_QUERIES:
        raise ValueError(f"At most {MAX_QUERIES} queries per search, got {len(queries)}")

    # Batched: a few queries per Apify run; one failed run does not lose the others.
    results, errors = apify_scraper.search_tiktok(con, settings, queries)
    if len(errors) == len(queries):
        raise RuntimeError(f"All searches failed: {errors}")

    candidates: dict[str, Candidate] = {}
    non_thai: set[str] = set()
    videos_seen = 0
    for q in queries:  # input order, so ties resolve the same way every run
        for item in results.get(q, []):
            try:
                creator = apify_scraper.parse_creator(item)
                video = apify_scraper.parse_video(item)
            except (KeyError, TypeError, ValidationError):
                continue  # malformed item from the scraper
            videos_seen += 1
            if not is_thai_item(item, creator.bio, video.caption):
                non_thai.add(creator.handle)
                continue
            cand = candidates.setdefault(creator.handle, Candidate(creator=creator, found_by=[], videos=[]))
            if q not in cand.found_by:
                cand.found_by.append(q)
            if all(v.id != video.id for v in cand.videos):
                cand.videos.append(video)

    # Pre-order only (real ranking happens in evaluate): breadth of match, then reach, then clips.
    ranked = sorted(candidates.values(), key=lambda c: (
        -len(c.found_by), -c.creator.followers, -len(c.videos), c.creator.handle))
    return Discovery(
        candidates=ranked,
        queries=queries,
        errors=errors,
        videos_seen=videos_seen,
        non_thai_filtered=len(non_thai - candidates.keys()),
    )


def is_thai_item(item: dict, bio: str, caption: str) -> bool:
    # ponytail: script-based, a non-Thai creator writing Thai captions passes; add region data if one appears
    return item.get("textLanguage") == "th" or has_thai(bio) or has_thai(caption)
