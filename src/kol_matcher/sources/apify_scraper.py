import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

from apify_client import ApifyClient

from kol_matcher.schemas.creator import Creator, Video, tier_for
from kol_matcher.storage import cache

FB_PAGE_ACTOR = "apify/facebook-pages-scraper"
PAGE_FIELDS = ["title", "category", "categories", "intro", "info", "followers", "priceRange", "services", "website"]

FB_POSTS_ACTOR = "apify/facebook-posts-scraper"
MAX_POSTS = 20
MAX_POST_CHARS = 1000
POST_FIELDS = ["text", "time", "url", "likes", "comments", "shares",
               "isVideo", "viewsCount", "paidPartnership", "collaborators"]


# ponytail: per-process limit; a CLI and a server running at once can each use the full allowance.
# Needs a shared limiter (DB lock or queue) for a hosted multi-user server.
_slots: dict[int, threading.BoundedSemaphore] = {}
_slots_lock = threading.Lock()


def _run_slots(limit: int) -> threading.BoundedSemaphore:
    with _slots_lock:
        return _slots.setdefault(limit, threading.BoundedSemaphore(limit))


# One entry per live Apify run (cache hits add nothing), so a caller can measure what a job cost:
# take len(usage) before, read usage[n:] after.
usage: list[dict] = []


def run_actor(con, settings, actor:str, run_input:dict, max_items:int | None = None, fields: list[str] | None = None) -> list[dict]:
    if settings.replay_mode:
        hit = cache.get(con, actor, run_input, max_age_hours=None)
        if hit is None:
            raise RuntimeError(f"Replay mode: no fixture for {actor} {run_input}")
        return hit
    
    hit = cache.get(con, actor, run_input, max_age_hours=settings.cache_ttl_hours)
    if hit is not None:
        return hit
    
    items = _call_actor(settings, actor, run_input, max_items, fields)
    cache.put(con, actor, run_input, items)
    return items


def _call_actor(settings, actor: str, run_input: dict, max_items: int | None,
                fields: list[str] | None) -> list[dict]:
    """One Apify run, no cache. Safe to call from worker threads (touches no DuckDB connection)."""
    if settings.apify_token is None:
        raise RuntimeError("APIFY_TOKEN is not set (required outside replay mode)")
    client = ApifyClient(settings.apify_token.get_secret_value())
    # Apify caps total memory of concurrently running actors per account; extra runs wait here
    # instead of being refused.
    with _run_slots(settings.apify_max_concurrent_runs):
        run = client.actor(actor).call(run_input=run_input, max_items=max_items,
                                       memory_mbytes=settings.apify_run_memory_mb,
                                       max_total_charge_usd=Decimal("1.00"))
    if run is None:
        raise RuntimeError(f"Apify run failed: {actor}")
    items = client.dataset(run.default_dataset_id).list_items().items
    if fields:
        items = [{k: item.get(k) for k in fields} for item in items]
    with _slots_lock:
        usage.append({"actor": actor, "items": len(items), "proxy": "proxyCountryCode" in run_input})
    return items


def _batched(con, settings, actor: str, keys: list[str], single_input, batch_input, key_of,
             per_key: int, batch_size: int, fields: list[str]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Fetch many keys (queries or handles) in few Apify runs, cached per key.

    Each actor start costs ~15-30 s and only a few runs fit in the memory cap, so one run per key
    made a 20-creator evaluation take several minutes. Keys missing from the cache are sent
    `batch_size` per run (runs in parallel); items are split back by `key_of` and cached under the
    same single-key input as before, so cache hits do not depend on how keys were batched.
    Returns (items per key, error per key).
    """
    results, errors, missing = {}, {}, []
    for k in keys:
        hit = cache.get(con, actor, single_input(k),
                        max_age_hours=None if settings.replay_mode else settings.cache_ttl_hours)
        if hit is not None:
            results[k] = hit
        elif settings.replay_mode:
            errors[k] = f"Replay mode: no fixture for {actor} {k}"
        else:
            missing.append(k)
    chunks = [missing[i:i + batch_size] for i in range(0, len(missing), batch_size)]
    if not chunks:
        return results, errors

    def run(chunk):
        return _call_actor(settings, actor, batch_input(chunk), per_key * len(chunk), [*fields, "searchQuery", "input"])

    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futures = [(chunk, pool.submit(run, chunk)) for chunk in chunks]
        for chunk, future in futures:
            try:
                items = future.result()
            except Exception as e:  # noqa: BLE001 - one failed run should not lose the other chunks
                errors.update({k: f"{type(e).__name__}: {e}" for k in chunk})
                continue
            grouped: dict[str, list[dict]] = {k.casefold(): [] for k in chunk}
            for item in items:
                # A one-key run owns every item, even if the actor echoes the key differently.
                key = chunk[0].casefold() if len(chunk) == 1 else (key_of(item) or "").casefold()
                if key in grouped:
                    grouped[key].append({f: item.get(f) for f in fields})
            for k in chunk:
                results[k] = grouped[k.casefold()][:per_key]
                cache.put(con, actor, single_input(k), results[k])
    return results, errors
    
def _check_facebook_url(url: str):
    if not url.startswith(("https://www.facebook.com/", "https://facebook.com/", "https://m.facebook.com/")):
            raise ValueError(f"Not a Facebook page URL: {url}")
    

def fetch_facebook_page(con, settings, url: str) -> dict:
    _check_facebook_url(url)
    items = run_actor(con, settings, FB_PAGE_ACTOR, {"startUrls": [{"url": url}]},
                  max_items=1, fields=PAGE_FIELDS)
    if not items:
        raise RuntimeError(f"No page data returned for {url}")
    return items[0]

def fetch_facebook_posts(con, settings, url: str) -> list[dict]:
    _check_facebook_url(url)
    items = run_actor(con, settings, FB_POSTS_ACTOR,
                      {"startUrls": [{"url": url}], "resultsLimit": MAX_POSTS},
                      max_items=MAX_POSTS, fields=POST_FIELDS)
    return [{**p, "text": p["text"][:MAX_POST_CHARS]} for p in items if p.get("text")]


TIKTOK_ACTOR = "clockworks/tiktok-scraper"
VIDEOS_PER_PROFILE = 30
MAX_CAPTION_CHARS = 500
TIKTOK_FIELDS = [
    "id", "text", "textLanguage", "createTimeISO", "webVideoUrl",
    "playCount", "diggCount", "commentCount", "shareCount", "collectCount",
    "isAd", "isSponsored", "hasTikTokShopProduct", "isPinned",
    "hashtags", "authorMeta",
]
# Metadata only: no media downloads, no comments.
TIKTOK_BASE_INPUT = {
    "shouldDownloadVideos": False,
    "shouldDownloadCovers": False,
    "shouldDownloadAvatars": False,
    "shouldDownloadMusicCovers": False,
    "shouldDownloadSlideshowImages": False,
    "commentsPerPost": 0,
}
# Thai proxy only for search, so results are localized. A profile's videos are the same from any
# country, and the proxy is charged per item (~26% of the per-item price).
SEARCH_EXTRA_INPUT = {"proxyCountryCode": "TH"}
QUERIES_PER_RUN = 5
PROFILES_PER_RUN = 10


def search_tiktok(con, settings, queries: list[str]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """TikTok search results per query, QUERIES_PER_RUN queries per Apify run."""
    n = settings.max_results_per_query
    base = {"resultsPerPage": n, **TIKTOK_BASE_INPUT, **SEARCH_EXTRA_INPUT}
    return _batched(con, settings, TIKTOK_ACTOR, list(dict.fromkeys(queries)),
                    single_input=lambda q: {"searchQueries": [q], **base},
                    batch_input=lambda qs: {"searchQueries": qs, **base},
                    key_of=lambda item: item.get("searchQuery"),
                    per_key=n, batch_size=QUERIES_PER_RUN, fields=TIKTOK_FIELDS)


def fetch_profile_videos(con, settings, handles: list[str]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Recent videos per creator, PROFILES_PER_RUN creators per Apify run."""
    handles = [h.strip().lstrip("@") for h in handles]  # "@x" and "x" share one cache entry
    if any(not h for h in handles):
        raise ValueError("Empty TikTok handle")
    base = {"resultsPerPage": VIDEOS_PER_PROFILE, "excludePinnedPosts": True, **TIKTOK_BASE_INPUT}
    return _batched(con, settings, TIKTOK_ACTOR, list(dict.fromkeys(handles)),
                    single_input=lambda h: {"profiles": [h], **base},
                    batch_input=lambda hs: {"profiles": hs, **base},
                    key_of=lambda item: item.get("input") or (item.get("authorMeta") or {}).get("name"),
                    per_key=VIDEOS_PER_PROFILE, batch_size=PROFILES_PER_RUN, fields=TIKTOK_FIELDS)


TIKTOK_COMMENTS_ACTOR = "clockworks/tiktok-comments-scraper"
COMMENTS_PER_VIDEO = 50
MAX_COMMENT_VIDEOS = 10
# Comment text and which video it belongs to. Never commenter names or IDs (PDPA).
COMMENT_FIELDS = ["text", "videoWebUrl"]


def fetch_comments(con, settings, video_urls: list[str]) -> list[dict]:
    """Comments for a few top videos. Only used for top candidates; it is the most expensive actor."""
    urls = sorted(set(video_urls))  # stable cache key regardless of input order
    if not urls:
        return []
    if len(urls) > MAX_COMMENT_VIDEOS:
        raise ValueError(f"At most {MAX_COMMENT_VIDEOS} videos per comments call, got {len(urls)}")
    return run_actor(con, settings, TIKTOK_COMMENTS_ACTOR,
                     {"postURLs": urls, "commentsPerPost": COMMENTS_PER_VIDEO, "maxRepliesPerComment": 0},
                     max_items=len(urls) * COMMENTS_PER_VIDEO, fields=COMMENT_FIELDS)


def parse_video(item: dict) -> Video:
    return Video(
        id=item["id"],
        url=item["webVideoUrl"],
        caption=(item.get("text") or "")[:MAX_CAPTION_CHARS],
        created_at=item["createTimeISO"],
        views=item.get("playCount") or 0,
        likes=item.get("diggCount") or 0,
        comments=item.get("commentCount") or 0,
        shares=item.get("shareCount") or 0,
        saves=item.get("collectCount") or 0,
        is_ad=bool(item.get("isAd") or item.get("isSponsored")),
    )


def parse_creator(item: dict) -> Creator:
    author = item["authorMeta"]
    followers = author.get("fans") or 0
    return Creator(
        handle=author["name"],
        nickname=author.get("nickName") or "",
        bio=author.get("signature") or "",
        followers=followers,
        tier=tier_for(followers),
        profile_url=author.get("profileUrl") or f"https://www.tiktok.com/@{author['name']}",
        scraped_at=datetime.now(UTC),
    )