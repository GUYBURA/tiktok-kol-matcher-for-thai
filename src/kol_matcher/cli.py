import shutil
from pathlib import Path
from typing import Annotated, NoReturn

import duckdb
import typer

from kol_matcher.config import Settings
from kol_matcher.core import backtest as bt
from kol_matcher.core import brand, discover, evaluate, shortlist
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.schemas.scoring import Objective
from kol_matcher.storage.db import open_db

app = typer.Typer(no_args_is_help=True, help="Find and rank Thai TikTok creators for a brand.")


def _open():
    """(connection, settings) for one CLI run. Separate function so tests can swap it."""
    settings = Settings()
    try:
        return open_db(settings), settings
    except duckdb.IOException as e:
        # Claude Desktop (or another CLI run) holds the DuckDB file lock while its server runs.
        _fail(RuntimeError(f"Cannot open the cache {settings.cache_path}. Is the MCP server running "
                           f"(Claude Desktop)? Quit it and retry. ({e})"))


def _fail(e: Exception) -> NoReturn:
    typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command()
def analyze(
    facebook_url: Annotated[str | None, typer.Option(help="Brand's Facebook page URL")] = None,
    website_url: Annotated[str | None, typer.Option(help="Brand's website URL")] = None,
    out: Annotated[Path | None, typer.Option(help="Write the JSON here instead of stdout")] = None,
):
    """Collect raw brand data (Facebook page, posts, website text) as JSON, plus the BrandProfile schema."""
    con, settings = _open()
    try:
        data = brand.analyze_brand(con, settings, facebook_url, website_url)
    except (ValueError, RuntimeError) as e:
        _fail(e)
    finally:
        con.close()
    text = data.model_dump_json(indent=2)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"Saved {out}")
    else:
        typer.echo(text)


@app.command()
def match(
    profile: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="BrandProfile JSON file")],
    objective: Annotated[Objective, typer.Option(help="Campaign goal; sets the score weights")] = Objective.engagement,
    query: Annotated[list[str] | None, typer.Option(help="Search query (repeatable). Default: the profile's "
                                                       "keywords and hashtags")] = None,
    pool: Annotated[int | None, typer.Option(min=1, help="Candidates to evaluate. Default: "
                                                        "MAX_CREATORS_PER_EVALUATE")] = None,
    top: Annotated[int, typer.Option(min=1, help="Creators to show and export")] = 3,
    out: Annotated[Path | None, typer.Option(help="Also export the top creators to this CSV")] = None,
):
    """Search, evaluate, and rank creators for a saved BrandProfile. Prints the top creators."""
    brand_profile = BrandProfile.model_validate_json(profile.read_text(encoding="utf-8"))
    con, settings = _open()
    try:
        found = discover.discover(con, settings, brand_profile, query or None)
        for q, err in found.errors.items():
            typer.secho(f"Query failed: {q}: {err}", fg=typer.colors.YELLOW, err=True)
        # Candidates come pre-ordered (queries matched, then followers); evaluate the first `pool`.
        handles = [c.creator.handle for c in found.candidates][:pool or settings.max_creators_per_evaluate]
        if not handles:
            raise ValueError(f"No Thai creators found for {len(found.queries)} queries")
        typer.echo(f"Found {len(found.candidates)} Thai creators, evaluating {len(handles)}...", err=True)
        result = evaluate.evaluate(con, settings, brand_profile, handles, objective)
        best = result.creators[:top]
        for c in best:
            typer.echo(f"{c.rank}. @{c.handle} ({c.tier.value}, {c.followers:,} followers) "
                       f"score {c.score.total:.2f}\n   {c.profile_url}\n   {c.rationale}")
        if result.inactive:
            typer.echo(f"Inactive (dropped): {', '.join('@' + i.handle for i in result.inactive)}", err=True)
        if out and best:
            saved = shortlist.save_shortlist(con, settings, brand_profile, [c.handle for c in best], objective,
                                             notes=f"cli match: top {top}, not reviewed by a person")
            shutil.copyfile(saved.path, out)
            typer.echo(f"Saved {out}")
    except (ValueError, RuntimeError) as e:
        _fail(e)
    finally:
        con.close()


# Backtests reuse cached scrapes for a month, so a rerun costs nothing and gives the same numbers.
EVAL_CACHE_HOURS = 24 * 30


def _confirm_spend(settings, n_queries: int, pool: int, yes: bool) -> None:
    cap = bt.max_cost_usd(settings, n_queries, pool)
    typer.echo(f"Up to {n_queries} searches and {pool} profile scrapes: at most ~${cap:.2f} of Apify "
               f"on a cold cache (cached results are free).", err=True)
    if not yes and not settings.replay_mode and not typer.confirm("Continue?", err=True):
        raise typer.Exit(1)


@app.command("ground-truth")
def ground_truth(
    brand_term: Annotated[list[str], typer.Option(help="Brand name as creators write it (repeatable)")],
    out: Annotated[Path, typer.Option(help="CSV to write, e.g. evals/ground_truth/<brand>.csv")],
    query: Annotated[list[str] | None, typer.Option(help="Search query (repeatable). Default: each brand "
                                                       "term and its hashtag")] = None,
    exclude_handle: Annotated[list[str] | None, typer.Option(help="The brand's own TikTok accounts")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the cost prompt")] = False,
):
    """Find Thai creators whose clips mention the brand. Review the CSV and set keep=n on wrong rows."""
    con, settings = _open()
    settings = settings.model_copy(update={"cache_ttl_hours": EVAL_CACHE_HOURS})
    n = len(query) if query else 2 * len(brand_term)
    try:
        _confirm_spend(settings, n, 0, yes)
        rows, errors = bt.build_ground_truth(con, settings, brand_term, query, exclude_handle)
    except (ValueError, RuntimeError) as e:
        _fail(e)
    finally:
        con.close()
    for q, err in errors.items():
        typer.secho(f"Query failed: {q}: {err}", fg=typer.colors.YELLOW, err=True)
    bt.write_ground_truth(rows, out)
    sponsored = sum(r.sponsored for r in rows)
    typer.echo(f"Saved {out}: {len(rows)} creators ({sponsored} with a sponsored clip). Review before use.")


@app.command()
def backtest(
    profile: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="BrandProfile JSON file")],
    ground_truth: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Ground-truth CSV")],
    k: Annotated[int, typer.Option(min=1, help="Recall@K cut-off")] = 20,
    pool: Annotated[int, typer.Option(min=1, help="Discovered creators to evaluate and rank")] = 40,
    objective: Annotated[Objective, typer.Option(help="Campaign goal; sets the score weights")] = Objective.engagement,
    query: Annotated[list[str] | None, typer.Option(help="Search query (repeatable). Default: the profile's "
                                                       "keywords and hashtags")] = None,
    brand_term: Annotated[list[str] | None, typer.Option(help="Extra brand spellings; queries containing "
                                                            "any are dropped")] = None,
    exclude_handle: Annotated[list[str] | None, typer.Option(help="The brand's own TikTok accounts")] = None,
    seed: Annotated[int, typer.Option(min=0, help="Add this many ground-truth creators to the pool "
                                                   "(ranking-only test)")] = 0,
    out: Annotated[Path | None, typer.Option(help="Write the result JSON here")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the cost prompt")] = False,
):
    """Recall@K: how many of the brand's past collaborators reach the top K, searching without its name."""
    brand_profile = BrandProfile.model_validate_json(profile.read_text(encoding="utf-8"))
    con, settings = _open()
    settings = settings.model_copy(update={"cache_ttl_hours": EVAL_CACHE_HOURS})
    try:
        truth = bt.load_ground_truth(ground_truth)
        kept, _ = bt.brand_queries(query or discover.default_queries(brand_profile)[:discover.MAX_QUERIES],
                                   [brand_profile.brand_name, *(brand_term or [])])
        _confirm_spend(settings, len(kept), pool + seed, yes)
        result = bt.run_backtest(con, settings, brand_profile, truth, k=k, pool=pool, objective=objective,
                                 queries=query, brand_terms=brand_term, exclude_handles=exclude_handle,
                                 seed=seed)
    except (ValueError, RuntimeError) as e:
        _fail(e)
    finally:
        con.close()
    typer.echo(f"{result.brand}: recall@{k} = {result.recall_at_k:.2f} ({len(result.hits)}/{result.ground_truth})\n"
               f"  ground truth found by search: {len(result.gt_discovered)}, ranked: {len(result.gt_ranked)} "
               f"of {result.evaluated} evaluated\n"
               f"  hits@{k}: ranker {len(result.hits)}, search order {result.discovery_order_hits}, "
               f"random {result.random_hits}\n"
               f"  {result.seconds}s, {result.apify_runs} Apify runs, ~${result.est_cost_usd}")
    if result.seeded:
        typer.echo(f"  seeded {len(result.seeded)} ground-truth creators; ground-truth ranks: {result.gt_ranks}")
    if result.excluded_queries:
        typer.echo(f"  queries dropped (name the brand): {', '.join(result.excluded_queries)}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"Saved {out}")


@app.command("score-list")
def score_list(
    ground_truth: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Ground-truth CSV")],
    handles: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Ranked handles, one per line "
                                                                           "(e.g. from a baseline run)")],
    k: Annotated[int, typer.Option(min=1, help="Recall@K cut-off")] = 20,
):
    """Recall@K for a ranked list made elsewhere, such as an AI client with raw Apify tools."""
    try:
        truth = bt.load_ground_truth(ground_truth)
    except ValueError as e:
        _fail(e)
    ranked = [h.strip().lstrip("@") for h in handles.read_text(encoding="utf-8").splitlines() if h.strip()]
    hits, recall = bt.recall_at_k(ranked, truth, k)
    typer.echo(f"recall@{k} = {recall:.2f} ({len(hits)}/{len({h.casefold() for h in truth})}): "
               f"{', '.join(hits) or 'no hits'}")
