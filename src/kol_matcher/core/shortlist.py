import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from kol_matcher.core.enrich import INACTIVE_DAYS, normalize_handles
from kol_matcher.core.features import pool_features
from kol_matcher.core.flags import account_flags, content_flags
from kol_matcher.core.rationale import rationale
from kol_matcher.core.scoring import rank
from kol_matcher.schemas.brand import BrandProfile, profile_hash
from kol_matcher.schemas.creator import (
    CommentStats,
    Creator,
    EnrichedCreator,
    Shortlist,
    ShortlistRow,
    Tier,
    Video,
)
from kol_matcher.schemas.scoring import Flag, FlagType, Objective

MAX_SHORTLIST = 50
VIDEOS_PER_CREATOR = 30


def save_shortlist(con, settings, profile: BrandProfile, handles: list[str],
                   objective: Objective = Objective.engagement, notes: str | None = None,
                   now: datetime | None = None) -> Shortlist:
    """Rebuild the approved creators' numbers from the DuckDB cache (never from the AI client,
    never from Apify), write a CSV, and append the selection to shortlist_log.

    Every handle must have been evaluated before (so it is in the cache); otherwise this raises.
    """
    handles = normalize_handles(handles)
    if not handles:
        raise ValueError("No TikTok handles given")
    if len(handles) > MAX_SHORTLIST:
        raise ValueError(f"At most {MAX_SHORTLIST} creators per shortlist, got {len(handles)}")
    pool = [_load(con, h) for h in handles]
    missing = [h for h, e in zip(handles, pool) if e is None]
    if missing:
        raise ValueError(f"Not in cache, run evaluate_creators first: {', '.join(missing)}")

    now = now or datetime.now(UTC)
    by_handle = {e.creator.handle: e for e in pool}
    features = pool_features(con, settings, profile, pool, now=now)
    for f in features:
        f.comment_stats = _cached_comment_stats(con, f.handle)

    rows = []
    for i, (f, s) in enumerate(rank(features, objective), 1):
        e = by_handle[f.handle]
        flags = account_flags(e.creator) + content_flags(e, profile)
        if f.days_since_post > INACTIVE_DAYS:
            flags.append(Flag(type=FlagType.inactive, detail=f"last post {f.days_since_post} days ago"))
        rows.append(ShortlistRow(
            rank=i, handle=f.handle, profile_url=e.creator.profile_url, tier=f.tier,
            followers=e.creator.followers, total_score=s.total, relevance=s.relevance.value,
            performance=s.performance.value, audience=s.audience.value if s.audience else None,
            credibility=s.credibility.value, er_by_impression=round(f.er_by_impression, 2),
            er_by_follower=round(f.er_by_follower, 2),
            impressions_per_follower=round(f.impressions_per_follower, 3),
            median_impressions=f.median_impressions, median_engagement=f.median_engagement,
            on_theme_share=round(f.on_theme_share, 3), top_theme=f.top_theme,
            sponsored_ratio=round(f.sponsored_ratio, 3), days_since_post=f.days_since_post,
            flags="; ".join(f"{fl.type.value}: {fl.detail}" for fl in flags),
            rationale=rationale(f, flags),
        ))

    p_hash = profile_hash(profile)
    path = Path(settings.shortlist_dir) / f"shortlist_{p_hash}_{now:%Y%m%d_%H%M%S}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    shortlist = Shortlist(path=path.as_posix(), saved_at=now, brand_name=profile.brand_name,
                          profile_hash=p_hash, objective=objective.value, notes=notes, rows=rows)
    # utf-8-sig: Excel on Windows only shows Thai correctly with a BOM.
    path.write_text(to_csv(shortlist), encoding="utf-8-sig")

    log_time = now.astimezone(UTC).replace(tzinfo=None)
    con.executemany("INSERT INTO shortlist_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [[log_time, p_hash, objective.value, r.handle, r.rank, r.total_score, notes] for r in rows])
    return shortlist


def to_csv(shortlist: Shortlist) -> str:
    buf = io.StringIO()
    fields = list(ShortlistRow.model_fields) + ["brand", "objective", "profile_hash", "saved_at", "notes"]
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    meta = {"brand": shortlist.brand_name, "objective": shortlist.objective,
            "profile_hash": shortlist.profile_hash, "saved_at": shortlist.saved_at.isoformat(),
            "notes": shortlist.notes or ""}
    for row in shortlist.rows:
        writer.writerow({**row.model_dump(mode="json"), **meta})
    return buf.getvalue()


def _load(con, handle: str) -> EnrichedCreator | None:
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


def _cached_comment_stats(con, handle: str) -> CommentStats | None:
    row = con.execute(
        "SELECT n_comments, thai_share, purchase_intent_rate, duplicate_rate, emoji_only_rate "
        "FROM comment_stats WHERE handle = ? ORDER BY scraped_at DESC LIMIT 1", [handle]).fetchone()
    if row is None:
        return None
    return CommentStats(n_comments=row[0], thai_share=row[1], purchase_intent_rate=row[2],
                        duplicate_rate=row[3], emoji_only_rate=row[4])
