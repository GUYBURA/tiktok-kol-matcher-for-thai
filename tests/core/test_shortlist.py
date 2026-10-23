import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from kol_matcher.config import Settings
from kol_matcher.core import enrich as enrich_mod
from kol_matcher.core import features as features_mod
from kol_matcher.core.enrich import enrich
from kol_matcher.core.shortlist import save_shortlist
from kol_matcher.schemas.brand import BrandProfile, profile_hash
from kol_matcher.schemas.scoring import Objective
from kol_matcher.storage.db import connect

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PROFILE = BrandProfile(
    brand_name="ยืดเปล่า Yuedpao", category="clothing", products=["t-shirts"], tone="casual",
    price_tier="mid", target_audience="Thai adults", content_themes=["outfits"],
    keywords_th=["เสื้อยืด"], keywords_en=["tee"],
)


def item(handle, video_id, days_ago=1, views=10_000, likes=800, bio="", fans=20_000, caption="แต่งตัวไปทำงาน"):
    created = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "id": video_id, "text": caption, "createTimeISO": created,
        "webVideoUrl": f"https://www.tiktok.com/@{handle}/video/{video_id}",
        "playCount": views, "diggCount": likes, "commentCount": 20, "shareCount": 5, "collectCount": 30,
        "authorMeta": {"name": handle, "signature": bio, "fans": fans,
                       "profileUrl": f"https://www.tiktok.com/@{handle}"},
    }


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    def embed(_con, _settings, texts):
        return np.array([[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(features_mod.embeddings, "embed", embed)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, shortlist_dir=str(tmp_path / "shortlists"))


@pytest.fixture
def evaluated(con, settings, monkeypatch, as_batch):
    """Two creators already enriched into the cache, then Apify made unreachable."""
    profiles = {
        "mint": [item("mint", "1", likes=2_000), item("mint", "2", likes=1_500)],
        "bank": [item("bank", "3", likes=100, bio="ขอบคุณที่อุดหนุนร้านของเรา")],
    }
    monkeypatch.setattr(enrich_mod.apify_scraper, "fetch_profile_videos", as_batch(lambda _c, _s, h: profiles[h]))
    enrich(con, settings, ["mint", "bank"], now=NOW)

    def no_apify(*_args, **_kwargs):
        raise AssertionError("save_shortlist must not call Apify")
    monkeypatch.setattr(enrich_mod.apify_scraper, "fetch_profile_videos", no_apify)
    monkeypatch.setattr(enrich_mod.apify_scraper, "run_actor", no_apify)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_rows_ranked_from_cache_without_apify(con, settings, evaluated):
    sl = save_shortlist(con, settings, PROFILE, ["@bank", "mint"], Objective.engagement, now=NOW)
    assert [r.handle for r in sl.rows] == ["mint", "bank"]   # ranked by score, not input order
    assert [r.rank for r in sl.rows] == [1, 2]
    assert sl.rows[0].profile_url == "https://www.tiktok.com/@mint"
    assert sl.profile_hash == profile_hash(PROFILE)


def test_csv_written_with_bom_and_thai(con, settings, evaluated):
    sl = save_shortlist(con, settings, PROFILE, ["mint", "bank"], notes="ส่งให้ลูกค้าวันจันทร์", now=NOW)
    raw = Path(sl.path).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                    # Excel needs the BOM for Thai
    rows = read_csv(sl.path)
    assert [r["handle"] for r in rows] == ["mint", "bank"]
    assert rows[0]["brand"] == "ยืดเปล่า Yuedpao"
    assert rows[0]["notes"] == "ส่งให้ลูกค้าวันจันทร์"
    assert rows[0]["objective"] == "engagement"
    assert "shop_account" in rows[1]["flags"]
    assert rows[1]["rationale"].endswith("review flags: shop_account.")


def test_filename_is_ascii(con, settings, evaluated):
    sl = save_shortlist(con, settings, PROFILE, ["mint"], now=NOW)
    assert sl.path.endswith(f"shortlist_{profile_hash(PROFILE)}_20261023_120000.csv")
    assert sl.path.isascii()


def test_metrics_match_cached_videos(con, settings, evaluated):
    [row] = save_shortlist(con, settings, PROFILE, ["mint"], now=NOW).rows
    # 2 clips x 10,000 plays; engagement per clip = likes + 20 + 5 + 30.
    total_engagement = (2_000 + 55) + (1_500 + 55)
    assert row.er_by_impression == pytest.approx(total_engagement / 20_000 * 100, abs=0.01)
    assert row.median_impressions == 10_000
    assert row.followers == 20_000


def test_log_appended(con, settings, evaluated):
    save_shortlist(con, settings, PROFILE, ["mint", "bank"], Objective.conversion, notes="v1", now=NOW)
    save_shortlist(con, settings, PROFILE, ["mint"], Objective.conversion, notes="v2", now=NOW)
    log = con.execute("SELECT handle, rank, objective, notes FROM shortlist_log ORDER BY notes, rank").fetchall()
    assert log == [("mint", 1, "conversion", "v1"), ("bank", 2, "conversion", "v1"), ("mint", 1, "conversion", "v2")]


def test_unknown_handle_rejected_before_writing(con, settings, evaluated, tmp_path):
    with pytest.raises(ValueError, match="run evaluate_creators first: ghost"):
        save_shortlist(con, settings, PROFILE, ["mint", "ghost"], now=NOW)
    assert not (tmp_path / "shortlists").exists()
    assert con.execute("SELECT count(*) FROM shortlist_log").fetchone()[0] == 0


def test_empty_handles_rejected(con, settings, evaluated):
    with pytest.raises(ValueError):
        save_shortlist(con, settings, PROFILE, ["", "@"], now=NOW)


def test_cached_comment_stats_used(con, settings, evaluated):
    con.execute("INSERT INTO comment_stats VALUES ('mint', ?, 40, 0.9, 0.15, 0.0, 0.0)",
                [NOW.replace(tzinfo=None)])
    [row] = save_shortlist(con, settings, PROFILE, ["mint"], Objective.conversion, now=NOW).rows
    assert row.audience is not None
    assert "inferred from 40 comments" in row.rationale


def test_same_input_same_csv(con, settings, evaluated):
    a = save_shortlist(con, settings, PROFILE, ["mint", "bank"], now=NOW)
    b = save_shortlist(con, settings, PROFILE, ["bank", "mint"], now=NOW)
    assert a.rows == b.rows
