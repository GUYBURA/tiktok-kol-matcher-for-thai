from datetime import UTC

from kol_matcher.schemas.creator import (
    CommentStats,
    Creator,
    EnrichedCreator,
    Tier,
    Video,
)

# Read creators back from the DuckDB cache written by enrich. No network calls.

VIDEOS_PER_CREATOR = 30


def load_creator(con, handle: str) -> EnrichedCreator | None:
    profile = con.execute(
        "SELECT handle, nickname, bio, profile_url, region FROM creators WHERE handle = ?", [handle]).fetchone()
    snap = con.execute(
        "SELECT followers, tier, scraped_at FROM creator_snapshots WHERE handle = ? "
        "ORDER BY scraped_at DESC LIMIT 1", [handle]).fetchone()
    rows = con.execute(
        "SELECT id, url, caption, created_at, views, likes, comments, shares, saves, is_ad FROM videos "
        "WHERE handle = ? ORDER BY created_at DESC LIMIT ?", [handle, VIDEOS_PER_CREATOR]).fetchall()
    if profile is None or snap is None or not rows:
        return None
    creator = Creator(handle=profile[0], nickname=profile[1] or "", bio=profile[2] or "",
                      profile_url=profile[3], region=profile[4], followers=snap[0], tier=Tier(snap[1]),
                      scraped_at=snap[2])
    videos = [Video(id=r[0], url=r[1], caption=r[2] or "", created_at=r[3].replace(tzinfo=UTC),
                    views=r[4], likes=r[5], comments=r[6], shares=r[7], saves=r[8] or 0, is_ad=r[9])
              for r in rows]  # stored as naive UTC
    return EnrichedCreator(creator=creator, videos=videos)


def cached_comment_stats(con, handle: str) -> CommentStats | None:
    row = con.execute(
        "SELECT n_comments, thai_share, purchase_intent_rate, duplicate_rate, emoji_only_rate "
        "FROM comment_stats WHERE handle = ? ORDER BY scraped_at DESC LIMIT 1", [handle]).fetchone()
    if row is None:
        return None
    return CommentStats(n_comments=row[0], thai_share=row[1], purchase_intent_rate=row[2],
                        duplicate_rate=row[3], emoji_only_rate=row[4])
