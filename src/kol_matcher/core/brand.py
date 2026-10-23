from concurrent.futures import ThreadPoolExecutor

from kol_matcher.schemas.brand import BrandData, BrandProfile
from kol_matcher.sources import apify_scraper, web_scraper


def analyze_brand(con, settings, facebook_url: str | None = None, website_url: str | None = None) -> BrandData:
    """Collect raw brand data from Facebook and the website for the AI client to summarize.

    Sources run in parallel (each Apify actor takes ~30s). Invalid URLs raise ValueError
    (caller error). Runtime failures of one source are recorded in `errors` and the other
    sources still return; if every requested source fails, raise.
    """
    if not facebook_url and not website_url:
        raise ValueError("Provide at least one of facebook_url or website_url")

    sources = {}
    if facebook_url:
        sources["facebook_page"] = (apify_scraper.fetch_facebook_page, facebook_url)
        sources["facebook_posts"] = (apify_scraper.fetch_facebook_posts, facebook_url)
    if website_url:
        sources["website_text"] = (web_scraper.fetch_website, website_url)

    def run(fetch, url):
        # A DuckDB connection must not be shared across threads; each thread gets its own cursor.
        with con.cursor() as cur:
            return fetch(cur, settings, url)

    data = BrandData(profile_schema=BrandProfile.model_json_schema())
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {field: pool.submit(run, fetch, url) for field, (fetch, url) in sources.items()}
        for field, future in futures.items():
            try:
                setattr(data, field, future.result())
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001 - any source failure (network, actor run, replay miss) is reported, not fatal
                data.errors[field] = f"{type(e).__name__}: {e}"

    if len(data.errors) == len(sources):
        raise RuntimeError(f"All brand sources failed: {data.errors}")
    return data
