import httpx
import trafilatura
from kol_matcher.storage import cache

MAX_CHARS = 20_000

def fetch_website(con, url: str, settings) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Only http(s) URLs are allowed: {url}")
    if settings.replay_mode:
        hit = cache.get(con, "website", {"url": url}, max_age_hours=None)
        if hit is None:
            raise RuntimeError(f"Replay mode: no fixture for {url}")
        return hit
    hit = cache.get(con, "website", {"url": url}, max_age_hours=settings.cache_ttl_hours)
    if hit is not None:
        return hit
    resp = httpx.get(url, follow_redirects=True, timeout=20,
                     headers={"User-Agent": "Mozilla/5.0 (kol-matcher)"})
    resp.raise_for_status()
    text = (trafilatura.extract(resp.text) or "")[:MAX_CHARS]
    cache.put(con, "website", {"url": url}, text)
    return text