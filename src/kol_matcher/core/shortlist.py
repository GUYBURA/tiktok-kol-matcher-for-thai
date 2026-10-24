import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from kol_matcher.core.enrich import INACTIVE_DAYS, normalize_handles
from kol_matcher.core.features import pool_features
from kol_matcher.core.flags import account_flags, content_flags
from kol_matcher.core.rationale import rationale
from kol_matcher.core.scoring import rank
from kol_matcher.core.stored import cached_comment_stats, load_creator
from kol_matcher.schemas.brand import BrandProfile, profile_hash
from kol_matcher.schemas.creator import Shortlist, ShortlistRow
from kol_matcher.schemas.scoring import Flag, FlagType, Objective
from kol_matcher.storage.db import clock

MAX_SHORTLIST = 50


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
    pool = [load_creator(con, h) for h in handles]
    missing = [h for h, e in zip(handles, pool) if e is None]
    if missing:
        raise ValueError(f"Not in cache, run evaluate_creators first: {', '.join(missing)}")

    scored_at = now or clock(con, settings)   # replay: the fixtures' time, so scores match evaluate
    now = now or datetime.now(UTC)            # when the file is saved
    by_handle = {e.creator.handle: e for e in pool}
    features = pool_features(con, settings, profile, pool, now=scored_at)
    for f in features:
        f.comment_stats = cached_comment_stats(con, f.handle)

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
