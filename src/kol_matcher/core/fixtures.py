"""Replay fixtures: the cached data one demo profile needs, exported so the demo runs offline."""
import json

from pydantic import ValidationError

from kol_matcher.core.discover import MAX_QUERIES, default_queries
from kol_matcher.schemas import stable_hash
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.sources import apify_scraper, embeddings
from kol_matcher.storage.db import connect, export_fixtures


def build_fixtures(con, settings, profile: BrandProfile, out_dir: str,
                   queries: list[str] | None = None) -> dict[str, int]:
    """Export the profile's searches, the cached recent videos of every creator they found, and the
    embeddings both need. Never calls Apify: a search that is not cached raises, and a creator
    without cached videos is left out (replay then reports it as missing). Ollama embeds any
    caption that has no cached embedding yet.
    """
    queries = queries or default_queries(profile)[:MAX_QUERIES]
    rows, items = [], []
    for q in queries:
        row = _latest(con, apify_scraper.search_input(settings, [q]))
        if row is None:
            raise ValueError(f"Search not in the cache: {q!r}. Run it live once first.")
        rows.append(row)
        items += json.loads(row[3])

    handles = sorted({(i.get("authorMeta") or {}).get("name") for i in items} - {None})
    creators = 0
    for h in handles:
        row = _latest(con, apify_scraper.profile_input([h]))
        if row is not None:
            rows.append(row)
            items += json.loads(row[3])
            creators += 1

    texts = list(dict.fromkeys([*profile.content_themes, *_captions(items)]))
    embeddings.embed(con, settings, texts)
    hashes = [stable_hash(t) for t in texts]

    out = connect(":memory:")
    out.executemany("INSERT INTO raw_responses VALUES (?, ?, ?, ?)", rows)
    vectors = con.execute(
        "SELECT text_hash, model, vector FROM embeddings WHERE model = ? AND list_contains(?, text_hash)",
        [settings.embedding_model, hashes],
    ).fetchall()
    out.executemany("INSERT INTO embeddings VALUES (?, ?, ?)", vectors)
    export_fixtures(out, out_dir)
    out.close()
    return {"searches": len(queries), "creators": creators, "creators_found": len(handles),
            "embeddings": len(vectors)}


def _latest(con, run_input: dict):
    return con.execute(
        """SELECT actor, input_hash, fetched_at, payload FROM raw_responses
           WHERE actor = ? AND input_hash = ? ORDER BY fetched_at DESC LIMIT 1""",
        [apify_scraper.TIKTOK_ACTOR, stable_hash(run_input)],
    ).fetchone()


def _captions(items: list[dict]) -> list[str]:
    captions = []
    for item in items:
        try:
            caption = apify_scraper.parse_video(item).caption   # same truncation the engine embeds
        except (KeyError, TypeError, ValidationError):
            continue
        if caption.strip():
            captions.append(caption)
    return captions
