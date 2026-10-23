import shutil
from pathlib import Path
from typing import Annotated, NoReturn

import duckdb
import typer

from kol_matcher.config import Settings
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


@app.command()
def backtest():
    """Recall@K against known past collaborations (phase 7)."""
    _fail(NotImplementedError("backtest is not implemented yet"))
