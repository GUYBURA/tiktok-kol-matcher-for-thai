from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from kol_matcher.core.flags import account_flags
from kol_matcher.schemas.creator import EnrichedCreator, Enrichment
from kol_matcher.schemas.scoring import Flag, FlagType
from kol_matcher.sources import apify_scraper

INACTIVE_DAYS = 60
MAX_PARALLEL_PROFILES = 10


def normalize_handles(handles: list[str]) -> list[str]:
    return list(dict.fromkeys(h.strip().lstrip("@") for h in handles if h and h.strip().lstrip("@")))


def enrich(con, settings, handles: list[str], now: datetime | None = None) -> Enrichment:
    """Fetch each creator's recent videos, drop inactive creators, and store results in DuckDB.

    At most settings.max_creators_per_evaluate handles per call (each one is an Apify run).
    A failed or empty profile is reported in `errors`; the rest still return.
    """
    handles = normalize_handles(handles)
    if not handles:
        raise ValueError("No TikTok handles given")
    limit = settings.max_creators_per_evaluate
    if len(handles) > limit:
        raise ValueError(f"At most {limit} creators per call, got {len(handles)}")
    now = now or datetime.now(UTC)

    def run(handle):
        # A DuckDB connection must not be shared across threads; each thread gets its own cursor.
        with con.cursor() as cur:
            return apify_scraper.fetch_profile_videos(cur, settings, handle)

    raw, errors = {}, {}
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_PROFILES, len(handles))) as pool:
        futures = {h: pool.submit(run, h) for h in handles}
        for h, future in futures.items():
            try:
                raw[h] = future.result()
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001 - one failed profile should not lose the others
                errors[h] = f"{type(e).__name__}: {e}"

    active, inactive = [], []
    for h in handles:  # input order
        if h not in raw:
            continue
        enriched = _build(raw[h])
        if enriched is None:
            errors[h] = "No videos returned (private, banned, or nonexistent account)"
            continue
        _store(con, settings, enriched)
        last_post = enriched.videos[0].created_at
        days = (now - last_post).days
        if days > INACTIVE_DAYS:
            enriched.flags.append(Flag(type=FlagType.inactive, detail=f"last post {days} days ago"))
            inactive.append(enriched)
        else:
            active.append(enriched)
    return Enrichment(creators=active, inactive=inactive, errors=errors)


def _build(items: list[dict]) -> EnrichedCreator | None:
    creator, videos = None, {}
    for item in items:
        try:
            creator = creator or apify_scraper.parse_creator(item)
            video = apify_scraper.parse_video(item)
        except (KeyError, TypeError, ValidationError):
            continue  # malformed item from the scraper
        videos[video.id] = video
    if creator is None or not videos:
        return None
    ordered = sorted(videos.values(), key=lambda v: v.created_at, reverse=True)
    return EnrichedCreator(creator=creator, videos=ordered, flags=account_flags(creator))


def _store(con, settings, enriched: EnrichedCreator) -> None:
    c = enriched.creator
    con.execute("INSERT OR REPLACE INTO creators VALUES (?, ?, ?, ?, ?)",
                [c.handle, c.nickname, c.bio, c.profile_url, c.region])
    # Append-only history, but one snapshot per cache window: re-reading cached data is not a new observation.
    con.execute(
        """INSERT INTO creator_snapshots
           SELECT ?, ?, ?, ? WHERE NOT EXISTS (
               SELECT 1 FROM creator_snapshots WHERE handle = ? AND followers = ? AND scraped_at > ?)""",
        [c.handle, c.scraped_at, c.followers, c.tier.value,
         c.handle, c.followers, c.scraped_at - timedelta(hours=settings.cache_ttl_hours)],
    )
    con.executemany(
        """INSERT OR REPLACE INTO videos
           (id, handle, url, caption, created_at, views, likes, comments, shares, saves, is_ad, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [[v.id, c.handle, v.url, v.caption, _naive_utc(v.created_at), v.views, v.likes, v.comments,
          v.shares, v.saves, v.is_ad, c.scraped_at] for v in enriched.videos],
    )


def _naive_utc(ts: datetime) -> datetime:
    # DuckDB TIMESTAMP columns are naive; store UTC.
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo else ts
