import threading
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


def run_actor(con, settings, actor:str, run_input:dict, max_items:int | None = None, fields: list[str] | None = None) -> list[dict]:
    if settings.replay_mode:
        hit = cache.get(con, actor, run_input, max_age_hours=None)
        if hit is None:
            raise RuntimeError(f"Replay mode: no fixture for {actor} {run_input}")
        return hit
    
    hit = cache.get(con, actor, run_input, max_age_hours=settings.cache_ttl_hours)
    if hit is not None:
        return hit
    
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
    cache.put(con, actor, run_input, items)
    return items
    
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
# Metadata only: no media downloads, no comments. Thai proxy so search results are localized.
TIKTOK_BASE_INPUT = {
    "shouldDownloadVideos": False,
    "shouldDownloadCovers": False,
    "shouldDownloadAvatars": False,
    "shouldDownloadMusicCovers": False,
    "shouldDownloadSlideshowImages": False,
    "commentsPerPost": 0,
    "proxyCountryCode": "TH",
}


def search_tiktok(con, settings, query: str) -> list[dict]:
    n = settings.max_results_per_query
    return run_actor(con, settings, TIKTOK_ACTOR,
                     {"searchQueries": [query], "resultsPerPage": n, **TIKTOK_BASE_INPUT},
                     max_items=n, fields=TIKTOK_FIELDS)


def fetch_profile_videos(con, settings, handle: str) -> list[dict]:
    handle = handle.strip().lstrip("@")
    if not handle:
        raise ValueError("Empty TikTok handle")
    return run_actor(con, settings, TIKTOK_ACTOR,
                     {"profiles": [handle], "resultsPerPage": VIDEOS_PER_PROFILE,
                      "excludePinnedPosts": True, **TIKTOK_BASE_INPUT},
                     max_items=VIDEOS_PER_PROFILE, fields=TIKTOK_FIELDS)


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