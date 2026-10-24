import re

import httpx
import numpy as np

from kol_matcher.schemas import stable_hash

BATCH_SIZE = 64


def embed(con, settings, texts: list[str]) -> np.ndarray:
    """Return one L2-normalized row per text, in input order. Cache-first via the embeddings table."""
    if settings.embedding_provider != "ollama":
        raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    model = settings.embedding_model
    hashes = [stable_hash(t) for t in texts]
    vectors = _cached(con, model, list(set(hashes)))

    missing = {h: t for h, t in zip(hashes, texts) if h not in vectors}
    if missing:
        if settings.replay_mode:
            raise RuntimeError(f"Replay mode: {len(missing)} texts have no cached embedding for {model}")
        items = list(missing.items())
        for start in range(0, len(items), BATCH_SIZE):
            batch = items[start:start + BATCH_SIZE]
            new = _embed_batch(settings, [t for _, t in batch])
            rows = [(h, model, v) for (h, _), v in zip(batch, new) if v is not None]
            if rows:
                con.executemany("INSERT OR IGNORE INTO embeddings VALUES (?, ?, ?)", rows)
            vectors.update({h: v for (h, _, v) in rows})

    # A text the model cannot embed is no evidence either way: zero row, similarity 0.
    # Not cached, so a fixed model gets another try.
    if not vectors:
        raise RuntimeError(f"Embedding model {model} could not embed any of {len(texts)} texts")
    dim = len(next(iter(vectors.values())))
    matrix = np.array([vectors.get(h, [0.0] * dim) for h in hashes], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


def _cached(con, model: str, hashes: list[str]) -> dict[str, list[float]]:
    rows = con.execute(
        "SELECT text_hash, vector FROM embeddings WHERE model = ? AND list_contains(?, text_hash)",
        [model, hashes],
    ).fetchall()
    return dict(rows)


def _embed_batch(settings, texts: list[str]) -> list[list[float] | None]:
    """Ollama's bge-m3 returns NaN for a few odd inputs (e.g. "\n.\n.\n." runs) and answers 500
    for the whole batch. Then embed one by one; a failing text is retried cleaned, else None."""
    try:
        return _ollama_embed(settings, texts)
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 500:
            raise
    out: list[list[float] | None] = []
    for t in texts:
        vector = None
        for variant in dict.fromkeys([t, _clean(t)]):
            try:
                vector = _ollama_embed(settings, [variant])[0]
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 500:
                    raise
        out.append(vector)
    return out


def _clean(text: str) -> str:
    """Collapse whitespace and drop tokens with no letters or digits (lone dots, dashes)."""
    return " ".join(w for w in text.split() if re.search(r"\w", w))


def _ollama_embed(settings, texts: list[str]) -> list[list[float]]:
    try:
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/embed",
            json={"model": settings.embedding_model, "input": texts},
            timeout=120,
        )
    except httpx.ConnectError as e:
        raise RuntimeError(f"Cannot reach Ollama at {settings.ollama_base_url}. Start Ollama, then retry "
                           f"(relevance scoring needs the {settings.embedding_model} model).") from e
    if resp.status_code == 404:
        raise RuntimeError(f"Ollama has no model {settings.embedding_model}. "
                           f"Run: ollama pull {settings.embedding_model}")
    resp.raise_for_status()
    vectors = resp.json()["embeddings"]
    if len(vectors) != len(texts):
        raise RuntimeError(f"Ollama returned {len(vectors)} embeddings for {len(texts)} texts")
    return vectors
