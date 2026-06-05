"""Production embedding loader (spec 03 §5, decision F).

L2 and the embedding model are deferred, so the embedding function is injectable
everywhere. This loader provides the production default: a sentence-transformers
model that MUST match L2 retrieval (align in shared config). Tests never call
this — they inject ``make_test_embed`` from ``testkit`` instead.
"""
from __future__ import annotations

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def load_embed_fn(model_name: str = DEFAULT_MODEL):
    """Return a ``str -> np.ndarray`` embedding function (L2-normalized float32)."""
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is not installed.\n"
            "Run: pip install -e .[clustering]\n"
            "Tests should inject services.enrich.clustering.testkit.make_test_embed."
        ) from exc

    model = SentenceTransformer(model_name)

    def embed(text: str):
        v = model.encode(text or "", normalize_embeddings=True)
        return np.asarray(v, dtype="float32")

    return embed
