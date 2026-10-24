"""Backtest: does the matcher find creators a brand already worked with, without being told the brand?

Ground truth is the set of Thai creators whose clips mention the brand (found by searching the brand
name). The backtest then searches with the brand's profile, with every query that names the brand
removed, evaluates the discovered pool, and measures how many ground-truth creators reach the top K.
"""
import csv
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError

from kol_matcher.core import discover, evaluate
from kol_matcher.core.discover import is_thai_item
from kol_matcher.core.enrich import normalize_handles
from kol_matcher.schemas.brand import BrandProfile, profile_hash
from kol_matcher.schemas.scoring import Objective
from kol_matcher.sources import apify_scraper

# Caption tags that mark paid content when TikTok's own ad flag is missing.
SPONSORED_TAGS = ["#ad", "#sponsored", "#โฆษณา", "#ได้รับการสนับสนุน", "#paidpartnership"]
GT_FIELDS = ["handle", "sponsored", "video_url", "caption", "found_by", "keep"]

# clockworks/tiktok-scraper pay-per-event prices (USD, Oct 2026), for a rough cost line only;
# the Apify console has the exact charge.
PRICE_START = 0.001
PRICE_ITEM = 0.0037
PRICE_ITEM_PROXY = 0.0013


class GroundTruthRow(BaseModel):
    handle: str
    sponsored: bool
    video_url: str
    caption: str
    found_by: str
    keep: str = "y"   # set to "n" by hand to drop a row (e.g. a reseller, not a collaboration)


class BacktestResult(BaseModel):
    brand: str
    profile_hash: str
    objective: str
    k: int
    queries: list[str]
    excluded_queries: list[str]          # named the brand, so not searched
    ground_truth: int
    discovered: int                      # Thai candidates found
    evaluated: int                       # active creators ranked
    gt_discovered: list[str]             # ground truth found by search (ceiling for recall)
    gt_ranked: list[str]                 # ground truth among the ranked creators
    gt_ranks: list[int] = []             # their ranks (1 = best)
    seeded: list[str] = []               # ground truth added to the pool by hand (ranking-only test)
    top_k: list[str]
    hits: list[str]
    recall_at_k: float
    discovery_order_hits: int            # naive baseline: top K by search order, same pool
    random_hits: float                   # expected hits for K picked at random from the same pool
    seconds: float
    apify_runs: int
    apify_items: int
    est_cost_usd: float
    errors: dict[str, str] = {}


def _norm(text: str) -> str:
    return "".join(text.casefold().split())


def mentions(text: str, terms: list[str]) -> bool:
    """True if any brand term appears in text, ignoring case, spaces, and a leading '#'."""
    t = _norm(text)
    return any(_norm(term).lstrip("#") in t for term in terms if _norm(term).lstrip("#"))


def build_ground_truth(con, settings, brand_terms: list[str], queries: list[str] | None = None,
                       exclude_handles: list[str] | None = None) -> tuple[list[GroundTruthRow], dict[str, str]]:
    """One row per Thai creator with a clip that mentions the brand, sponsored clips preferred.

    Queries default to each brand term and its hashtag. `exclude_handles` are the brand's own accounts.
    """
    if not brand_terms:
        raise ValueError("Give at least one brand term (the brand name as creators write it)")
    if not queries:
        queries = [q for t in brand_terms for q in (t, "#" + _norm(t).lstrip("#"))]
    queries = list(dict.fromkeys(queries))
    excluded = {h.casefold() for h in normalize_handles(exclude_handles or [])}
    results, errors = apify_scraper.search_tiktok(con, settings, queries)
    if len(errors) == len(queries):
        raise RuntimeError(f"All ground-truth searches failed: {errors}")

    rows: dict[str, GroundTruthRow] = {}
    for q in queries:
        for item in results.get(q, []):
            try:
                creator = apify_scraper.parse_creator(item)
                video = apify_scraper.parse_video(item)
            except (KeyError, TypeError, ValidationError):
                continue
            hashtags = " ".join("#" + (h.get("name") or "") for h in item.get("hashtags") or [] if isinstance(h, dict))
            text = f"{video.caption} {hashtags}"
            if (creator.handle.casefold() in excluded or not is_thai_item(item, creator.bio, video.caption)
                    or not mentions(text, brand_terms)):
                continue
            sponsored = video.is_ad or any(tag in text.casefold() for tag in SPONSORED_TAGS)
            old = rows.get(creator.handle)
            if old is None or (sponsored and not old.sponsored):
                rows[creator.handle] = GroundTruthRow(handle=creator.handle, sponsored=sponsored,
                                                      video_url=video.url, caption=video.caption, found_by=q)
    return sorted(rows.values(), key=lambda r: (not r.sponsored, r.handle.casefold())), errors


def write_ground_truth(rows: list[GroundTruthRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:   # BOM so Excel reads Thai
        w = csv.DictWriter(f, fieldnames=GT_FIELDS)
        w.writeheader()
        w.writerows(r.model_dump() for r in rows)


def load_ground_truth(path: Path) -> list[str]:
    """Handles of the kept rows (keep is not n/no/0)."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "handle" not in reader.fieldnames:
            raise ValueError(f"{path}: needs a 'handle' column")
        handles = [r["handle"] for r in reader
                   if r["handle"].strip() and (r.get("keep") or "y").strip().casefold() not in {"n", "no", "0"}]
    handles = normalize_handles(handles)
    if not handles:
        raise ValueError(f"{path}: no ground-truth creators kept")
    return handles


def recall_at_k(ranked: list[str], truth: list[str], k: int) -> tuple[list[str], float]:
    """(hits in the top k, hits / size of truth). Handles compare case-insensitively."""
    gt = {h.casefold() for h in truth}
    hits = [h for h in ranked[:k] if h.casefold() in gt]
    return hits, len(hits) / len(gt) if gt else 0.0


def brand_queries(queries: list[str], brand_terms: list[str]) -> tuple[list[str], list[str]]:
    """(kept, excluded): a query that names the brand would find the ground truth by construction."""
    kept = [q for q in queries if not mentions(q, brand_terms)]
    return kept, [q for q in queries if q not in kept]


def run_backtest(con, settings, profile: BrandProfile, truth: list[str], k: int = 20, pool: int = 12,
                 objective: Objective = Objective.engagement, queries: list[str] | None = None,
                 brand_terms: list[str] | None = None, exclude_handles: list[str] | None = None,
                 seed: int = 0) -> BacktestResult:
    """`seed` adds that many ground-truth creators search missed to the pool. Recall then no longer
    measures search, but hits vs random shows whether scoring lifts known collaborators."""
    started, runs_before = time.perf_counter(), len(apify_scraper.usage)
    terms = [profile.brand_name, *(brand_terms or [])]
    kept, excluded = brand_queries(queries or discover.default_queries(profile)[:discover.MAX_QUERIES], terms)
    if not kept:
        raise ValueError(f"Every query names the brand ({', '.join(excluded)}); give queries without it")

    found = discover.discover(con, settings, profile, kept)
    skip = {h.casefold() for h in normalize_handles(exclude_handles or [])}
    order = [c.creator.handle for c in found.candidates if c.creator.handle.casefold() not in skip]
    gt = {h.casefold() for h in truth}
    pool_handles = order[:pool]
    in_pool = {h.casefold() for h in pool_handles}
    seeded = [h for h in truth if h.casefold() not in in_pool and h.casefold() not in skip][:seed]
    pool_handles += seeded
    errors = dict(found.errors)

    ranked: list[str] = []
    if pool_handles:
        # The tool caps a call at a few creators for cost; a backtest ranks the whole pool at once.
        wide = settings.model_copy(update={"max_creators_per_evaluate": len(pool_handles)})
        result = evaluate.evaluate(con, wide, profile, pool_handles, objective)
        ranked = [c.handle for c in result.creators]
        errors.update(result.errors)

    hits, recall = recall_at_k(ranked, truth, k)
    in_ranked = [h for h in ranked if h.casefold() in gt]
    position = {h.casefold(): i for i, h in enumerate(order)}
    by_search = sorted(ranked, key=lambda h: position.get(h.casefold(), len(order)))
    top = min(k, len(ranked))
    runs = apify_scraper.usage[runs_before:]
    return BacktestResult(
        brand=profile.brand_name, profile_hash=profile_hash(profile), objective=objective.value, k=k,
        queries=kept, excluded_queries=excluded, ground_truth=len(gt), discovered=len(order),
        evaluated=len(ranked), gt_discovered=[h for h in order if h.casefold() in gt], gt_ranked=in_ranked,
        gt_ranks=[i for i, h in enumerate(ranked, 1) if h.casefold() in gt], seeded=seeded,
        top_k=ranked[:k], hits=hits, recall_at_k=round(recall, 4),
        discovery_order_hits=len(recall_at_k(by_search, truth, k)[0]),
        random_hits=round(top * len(in_ranked) / len(ranked), 2) if ranked else 0.0,
        seconds=round(time.perf_counter() - started, 1),
        apify_runs=len(runs), apify_items=sum(r["items"] for r in runs),
        est_cost_usd=round(sum(_run_cost(r) for r in runs), 3), errors=errors,
    )


def _run_cost(run: dict) -> float:
    if run["actor"] != apify_scraper.TIKTOK_ACTOR:
        return 0.0   # other actors' prices are not tracked here
    per_item = PRICE_ITEM + (PRICE_ITEM_PROXY if run["proxy"] else 0)
    return PRICE_START + run["items"] * per_item


def max_cost_usd(settings, n_queries: int, pool: int) -> float:
    """Upper bound for a cold cache: every query and every pool creator fetched live."""
    search = n_queries * settings.max_results_per_query * (PRICE_ITEM + PRICE_ITEM_PROXY)
    profiles = pool * apify_scraper.VIDEOS_PER_PROFILE * PRICE_ITEM
    runs = -(-n_queries // apify_scraper.QUERIES_PER_RUN) - (-pool // apify_scraper.PROFILES_PER_RUN)
    return round(search + profiles + runs * PRICE_START, 2)
