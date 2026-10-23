from kol_matcher.core.enrich import normalize_handles
from kol_matcher.core.stored import cached_comment_stats, load_creator
from kol_matcher.schemas.tools import CreatorDetail, FollowerPoint


def creator_detail(con, handle: str) -> CreatorDetail:
    """Everything cached about one creator. Cache only: no Apify call."""
    [handle] = normalize_handles([handle]) or [""]
    if not handle:
        raise ValueError("No TikTok handle given")
    e = load_creator(con, handle)
    if e is None:
        raise ValueError(f"@{handle} is not in the cache; run evaluate_creators first")
    history = con.execute("SELECT scraped_at, followers FROM creator_snapshots WHERE handle = ? "
                          "ORDER BY scraped_at", [handle]).fetchall()
    return CreatorDetail(
        creator=e.creator,
        follower_history=[FollowerPoint(scraped_at=t, followers=n) for t, n in history],
        videos=e.videos,
        comment_stats=cached_comment_stats(con, handle),
    )
