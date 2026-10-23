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
            new = _ollama_embed(settings, [t for _, t in batch])
            rows = [(h, model, v) for (h, _), v in zip(batch, new)]
            con.executemany("INSERT OR IGNORE INTO embeddings VALUES (?, ?, ?)", rows)
            vectors.update({h: v for (h, _, v) in rows})

    matrix = np.array([vectors[h] for h in hashes], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


def _cached(con, model: str, hashes: list[str]) -> dict[str, list[float]]:
    rows = con.execute(
        "SELECT text_hash, vector FROM embeddings WHERE model = ? AND list_contains(?, text_hash)",
        [model, hashes],
    ).fetchall()
    return dict(rows)


def _ollama_embed(settings, texts: list[str]) -> list[list[float]]:
    resp = httpx.post(
        f"{settings.ollama_base_url}/api/embed",
        json={"model": settings.embedding_model, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    vectors = resp.json()["embeddings"]
    if len(vectors) != len(texts):
        raise RuntimeError(f"Ollama returned {len(vectors)} embeddings for {len(texts)} texts")
    return vectors
