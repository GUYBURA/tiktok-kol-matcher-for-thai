"""CLI tests: run the real Typer app with Apify, websites, and Ollama faked; DuckDB in memory."""

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from typer.testing import CliRunner

from kol_matcher import cli
from kol_matcher.config import Settings
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.sources import apify_scraper, embeddings, web_scraper
from kol_matcher.storage.db import connect

PROFILE = BrandProfile(
    brand_name="Yuedpao", category="clothing", products=["t-shirts"], tone="casual", price_tier="mid",
    target_audience="Thai adults", content_themes=["outfits"], keywords_th=["เสื้อยืด"], keywords_en=["tee"],
)
NOW = datetime.now(UTC)
runner = CliRunner()


def clip(handle, video_id, likes=500, days_ago=1):
    created = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "id": video_id, "text": "แต่งตัวไปทำงาน", "textLanguage": "th", "createTimeISO": created,
        "webVideoUrl": f"https://www.tiktok.com/@{handle}/video/{video_id}",
        "playCount": 10_000, "diggCount": likes, "commentCount": 20, "shareCount": 5, "collectCount": 10,
        "authorMeta": {"name": handle, "signature": "", "fans": 20_000,
                       "profileUrl": f"https://www.tiktok.com/@{handle}"},
    }


HANDLES = ["a1", "b2", "c3", "d4", "e5", "f6", "g7"]


@pytest.fixture(autouse=True)
def fakes(tmp_path, monkeypatch, as_batch):
    con = connect(":memory:")
    settings = Settings(_env_file=None, shortlist_dir=str(tmp_path / "shortlists"), max_creators_per_evaluate=6)
    monkeypatch.setattr(cli, "_open", lambda: (con.cursor(), settings))
    monkeypatch.setattr(apify_scraper, "fetch_facebook_page", lambda c, s, url: {"title": "Yuedpao"})
    monkeypatch.setattr(apify_scraper, "fetch_facebook_posts", lambda c, s, url: [{"text": "โปร 1 แถม 1"}])
    monkeypatch.setattr(web_scraper, "fetch_website", lambda c, s, url: "เสื้อยืดผ้านุ่ม")
    monkeypatch.setattr(apify_scraper, "search_tiktok",
                        as_batch(lambda c, s, q: [clip(h, str(i)) for i, h in enumerate(HANDLES)]))
    evaluated = []

    def fetch_profile_videos(c, s, handle):
        evaluated.append(handle)
        return [clip(handle, f"{handle}{i}", likes=100 * (HANDLES.index(handle) + 1)) for i in range(3)]
    monkeypatch.setattr(apify_scraper, "fetch_profile_videos", as_batch(fetch_profile_videos))
    monkeypatch.setattr(embeddings, "embed", lambda c, s, texts: np.array([[1.0, 0.0] for _ in texts]))
    yield evaluated
    con.close()


@pytest.fixture
def profile_file(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(PROFILE.model_dump_json(), encoding="utf-8")
    return path


def test_analyze_prints_brand_data_json():
    result = runner.invoke(cli.app, ["analyze", "--facebook-url", "https://www.facebook.com/yuedpao"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["facebook_page"]["title"] == "Yuedpao"
    assert "properties" in data["profile_schema"]


def test_analyze_without_urls_fails_cleanly():
    result = runner.invoke(cli.app, ["analyze"])
    assert result.exit_code == 1
    assert "Error:" in result.stderr


def test_match_evaluates_pool_prints_top_with_links_and_exports(fakes, profile_file, tmp_path):
    out = tmp_path / "shortlist.csv"
    result = runner.invoke(cli.app, ["match", "--profile", str(profile_file), "--top", "3", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert len(fakes) == 6                                    # pool capped at MAX_CREATORS_PER_EVALUATE
    lines = [line for line in result.stdout.splitlines() if line[:2] in {"1.", "2.", "3.", "4."}]
    assert [line[:2] for line in lines] == ["1.", "2.", "3."]
    assert result.stdout.count("https://www.tiktok.com/@") == 3
    rows = out.read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 4                                     # header + top 3
    assert "not reviewed by a person" in rows[1]


def test_match_pool_option(fakes, profile_file):
    result = runner.invoke(cli.app, ["match", "--profile", str(profile_file), "--pool", "2"])
    assert result.exit_code == 0, result.output
    assert len(fakes) == 2


def test_match_rejects_invalid_profile(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"brand_name": "x"}), encoding="utf-8")
    result = runner.invoke(cli.app, ["match", "--profile", str(bad)])
    assert result.exit_code != 0


def test_readme_example_profile_is_valid():
    from pathlib import Path
    path = Path(__file__).parent.parent / "fixtures" / "profiles" / "example.json"
    BrandProfile.model_validate_json(path.read_text(encoding="utf-8"))


def test_score_list(tmp_path):
    gt = tmp_path / "gt.csv"
    gt.write_text("handle,keep\na1,y\nb2,n\nz9,y\n", encoding="utf-8")
    ranked = tmp_path / "ranked.txt"
    ranked.write_text("@c3\na1\n\nb2\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["score-list", "--ground-truth", str(gt), "--handles", str(ranked), "--k", "2"])
    assert result.exit_code == 0, result.output
    assert "recall@2 = 0.50 (1/2): a1" in result.stdout


def test_backtest_prompts_for_cost_and_writes_json(profile_file, tmp_path):
    gt = tmp_path / "gt.csv"
    gt.write_text("handle\ng7\nf6\n", encoding="utf-8")
    args = ["backtest", "--profile", str(profile_file), "--ground-truth", str(gt), "--k", "2", "--pool", "7"]
    declined = runner.invoke(cli.app, args, input="n\n")
    assert declined.exit_code == 1
    assert "at most ~$" in declined.stderr

    out = tmp_path / "res.json"
    result = runner.invoke(cli.app, [*args, "--yes", "--out", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["hits"] == ["g7", "f6"]          # most likes in the fake profiles
    assert "recall@2 = 1.00" in result.stdout
