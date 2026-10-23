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
