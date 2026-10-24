"""The offline demo on the committed fixtures (Srichand): real data, no network, same answer every run."""
import asyncio
import json
from pathlib import Path

import jsonschema
import pytest
from fastmcp import Client

from kol_matcher.config import Settings
from kol_matcher.core import discover, evaluate
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.server import mcp
from kol_matcher.sources import apify_scraper, embeddings
from kol_matcher.storage.db import clock, open_db
from kol_matcher.tools import context

ROOT = Path(__file__).parent.parent
PROFILE = BrandProfile.model_validate_json((ROOT / "evals/profiles/srichand.json").read_text(encoding="utf-8"))
# Pinned from the recorded data. If this changes, scoring changed: check that it was intended.
TOP_5_ENGAGEMENT = ["ceo.kalmandkind", "superthankky168", "amykitiya", "aum_aemmika19", "bboom_chr"]


@pytest.fixture
def replay(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, replay_mode=True, fixtures_path=str(ROOT / "fixtures"),
                        shortlist_dir=str(tmp_path / "shortlists"), apify_token=None)
    monkeypatch.setattr(apify_scraper, "_call_actor", lambda *a, **k: pytest.fail("Apify call in replay"))
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: pytest.fail("Ollama call in replay"))
    con = open_db(settings)
    yield con, settings
    con.close()


def test_demo_ranking_is_pinned_and_repeatable(replay):
    con, settings = replay
    found = discover.discover(con, settings, PROFILE)
    assert len(found.candidates) == 123 and not found.errors
    handles = [c.creator.handle for c in found.candidates][:5]
    first = evaluate.evaluate(con, settings, PROFILE, handles)
    second = evaluate.evaluate(con, settings, PROFILE, handles)
    assert [c.handle for c in first.creators] == TOP_5_ENGAGEMENT
    assert first.model_dump() == second.model_dump()
    assert not first.inactive and not first.errors


def test_replay_clock_is_recording_time(replay):
    con, settings = replay
    recorded = con.execute("SELECT max(fetched_at) FROM raw_responses").fetchone()[0]
    assert clock(con, settings).replace(tzinfo=None) == recorded


def test_missing_creator_is_an_error_not_a_scrape(replay):
    con, settings = replay
    result = evaluate.evaluate(con, settings, PROFILE, ["not_in_fixtures_123"])
    assert "Replay mode" in result.errors["not_in_fixtures_123"]


def _call_all(steps):
    async def run():
        async with Client(mcp) as client:
            schemas = {t.name: t.output_schema for t in await client.list_tools()}
            outputs = []
            for name, args in steps:
                result = await client.call_tool(name, args)
                outputs.append((name, result.structured_content, schemas[name]))
            return outputs
    return asyncio.run(run())


def test_tool_flow_outputs_match_declared_schemas(replay):
    con, settings = replay
    context.configure(con, settings)
    profile = PROFILE.model_dump(mode="json")
    outputs = _call_all([
        ("search_creators", {"profile": profile}),
        ("evaluate_creators", {"profile": profile, "handles": TOP_5_ENGAGEMENT, "top_n": 3}),
        ("get_creator_detail", {"handle": TOP_5_ENGAGEMENT[0]}),
        ("save_shortlist", {"profile": profile, "handles": TOP_5_ENGAGEMENT[:2], "notes": "demo"}),
    ])
    for name, out, schema in outputs:
        assert schema, f"{name} declares no output schema"
        jsonschema.validate(out, schema)
    search, evaluated, detail, saved = (o for _, o, _ in outputs)
    assert search["profile_hash"] == evaluated["profile_hash"] == saved["profile_hash"]
    assert [c["handle"] for c in evaluated["creators"]] == TOP_5_ENGAGEMENT[:3]
    assert [m["handle"] for m in evaluated["more"]] == TOP_5_ENGAGEMENT[3:]
    assert all(c["profile_url"].startswith("https://www.tiktok.com/@") for c in evaluated["creators"])
    # save_shortlist recomputes from the cache: same scores as evaluate, in replay as live.
    scores = {c["handle"]: c["score"]["total"] for c in evaluated["creators"]}
    assert {r["handle"]: r["total_score"] for r in saved["rows"]} == {h: scores[h] for h in TOP_5_ENGAGEMENT[:2]}
    assert Path(saved["path"]).exists()
    json.dumps(detail, ensure_ascii=False)
