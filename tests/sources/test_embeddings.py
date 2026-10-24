import httpx
import numpy as np
import pytest

from kol_matcher.config import Settings
from kol_matcher.sources import embeddings
from kol_matcher.sources.embeddings import BATCH_SIZE, embed
from kol_matcher.storage.db import connect


def settings(replay_mode=False, model="bge-m3", provider="ollama"):
    return Settings(_env_file=None, replay_mode=replay_mode, embedding_model=model, embedding_provider=provider)


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def fake_ollama(monkeypatch):
    """Replace httpx.post with a deterministic fake; records the texts of each request."""
    calls = []

    def post(url, json, **_kwargs):
        calls.append(json["input"])
        vectors = [[float(len(t)), 1.0, 0.0] for t in json["input"]]
        return httpx.Response(200, json={"embeddings": vectors}, request=httpx.Request("POST", url))

    monkeypatch.setattr(embeddings.httpx, "post", post)
    return calls


def test_returns_normalized_rows_in_input_order(con, fake_ollama):
    out = embed(con, settings(), ["วิ่ง", "รองเท้าวิ่งมือใหม่"])
    assert out.shape == (2, 3)
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, rtol=1e-6)
    assert out[1, 0] > out[0, 0]  # longer text -> larger first component in the fake


def test_second_call_served_from_cache(con, fake_ollama):
    first = embed(con, settings(), ["วิ่ง", "running"])
    second = embed(con, settings(), ["running", "วิ่ง"])
    assert len(fake_ollama) == 1
    np.testing.assert_allclose(second, first[::-1])


def test_only_missing_texts_are_embedded(con, fake_ollama):
    embed(con, settings(), ["a"])
    embed(con, settings(), ["a", "bb"])
    assert fake_ollama == [["a"], ["bb"]]


def test_duplicate_texts_embedded_once(con, fake_ollama):
    out = embed(con, settings(), ["same", "same", "same"])
    assert fake_ollama == [["same"]]
    assert out.shape == (3, 3)


def test_cache_is_per_model(con, fake_ollama):
    embed(con, settings(model="bge-m3"), ["x"])
    embed(con, settings(model="other-model"), ["x"])
    assert len(fake_ollama) == 2


def test_large_input_is_batched(con, fake_ollama):
    texts = [f"text {i}" for i in range(BATCH_SIZE + 5)]
    out = embed(con, settings(), texts)
    assert [len(batch) for batch in fake_ollama] == [BATCH_SIZE, 5]
    assert out.shape == (BATCH_SIZE + 5, 3)


def test_empty_input(con, fake_ollama):
    assert embed(con, settings(), []).shape == (0, 0)
    assert fake_ollama == []


def test_replay_serves_cache_and_misses_raise(con, fake_ollama):
    expected = embed(con, settings(), ["cached"])
    np.testing.assert_allclose(embed(con, settings(replay_mode=True), ["cached"]), expected)
    with pytest.raises(RuntimeError, match="Replay mode"):
        embed(con, settings(replay_mode=True), ["not cached"])
    assert len(fake_ollama) == 1


def test_unknown_provider_rejected(con, fake_ollama):
    with pytest.raises(ValueError):
        embed(con, settings(provider="openai"), ["x"])


@pytest.fixture
def nan_ollama(monkeypatch):
    """Like Ollama's bge-m3: a 500 for the whole request if any input contains a NaN trigger."""
    calls = []

    def post(url, json, **_kwargs):
        calls.append(json["input"])
        request = httpx.Request("POST", url)
        if any("\n.\n" in t or "BROKEN" in t for t in json["input"]):
            return httpx.Response(500, json={"error": "json: unsupported value: NaN"}, request=request)
        return httpx.Response(200, json={"embeddings": [[float(len(t)), 1.0, 0.0] for t in json["input"]]},
                              request=request)

    monkeypatch.setattr(embeddings.httpx, "post", post)
    return calls


def test_nan_text_does_not_fail_the_batch(con, nan_ollama):
    vectors = embed(con, settings(), ["ok", "สวย \n.\n.\n#แต่งหน้า", "BROKEN"])
    assert vectors.shape == (3, 3)
    assert np.allclose(np.linalg.norm(vectors[:2], axis=1), 1.0)
    assert not vectors[2].any()                               # cannot embed even cleaned: zero row
    assert "สวย #แต่งหน้า" in [t for call in nan_ollama for t in call]   # retried cleaned
    cached = con.execute("SELECT count(*) FROM embeddings").fetchone()[0]
    assert cached == 2                                         # zero row is not cached


def test_all_texts_failing_raises(con, nan_ollama):
    with pytest.raises(RuntimeError, match="could not embed"):
        embed(con, settings(), ["BROKEN"])


def test_other_http_errors_still_raise(con, monkeypatch):
    monkeypatch.setattr(embeddings.httpx, "post", lambda url, json, **_: httpx.Response(
        503, json={"error": "busy"}, request=httpx.Request("POST", url)))
    with pytest.raises(httpx.HTTPStatusError):
        embed(con, settings(), ["ok"])


def test_missing_model_says_how_to_pull(con, monkeypatch):
    monkeypatch.setattr(embeddings.httpx, "post", lambda url, json, **_: httpx.Response(
        404, json={"error": "model not found"}, request=httpx.Request("POST", url)))
    with pytest.raises(RuntimeError, match="ollama pull bge-m3"):
        embed(con, settings(), ["ok"])


def test_ollama_not_running_says_start_it(con, monkeypatch):
    def refuse(url, json, **_):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(embeddings.httpx, "post", refuse)
    with pytest.raises(RuntimeError, match="Start Ollama"):
        embed(con, settings(), ["ok"])
