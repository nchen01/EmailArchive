"""Deterministic test doubles for clustering (spec 03 decisions E & F).

These exist so tests and the eval never require a model download:
- ``make_test_embed`` — hash-based deterministic embeddings (stand-in for L2).
- ``FakeNlp`` — capitalized-token entity extractor (stand-in for spaCy NER).

Production code uses the real ``embed_fn`` and ``load_nlp()`` instead.
"""
from __future__ import annotations

import hashlib

import numpy as np


def make_test_embed(dim: int = 64):
    """Return a deterministic ``str -> np.ndarray`` embedding function.

    Unlike a pure SHA-of-the-whole-string (which makes even near-identical texts
    orthogonal), this uses **feature hashing over lowercased word tokens**: two
    texts that share vocabulary land close in cosine space, the way a real
    sentence embedding would. Still fully deterministic and model-free, so the
    eval and tests need no download (decision F), but the embedding component now
    carries real topical signal at fixture scale.
    """

    def _bucket(tok: str) -> int:
        h = hashlib.sha256(tok.encode()).digest()
        return int.from_bytes(h[:4], "big") % dim

    def _sign(tok: str) -> float:
        h = hashlib.sha256((tok + "#sign").encode()).digest()
        return 1.0 if (h[0] & 1) else -1.0

    def embed(text: str) -> np.ndarray:
        v = np.zeros(dim, dtype=np.float32)
        for tok in text.lower().split():
            tok = "".join(ch for ch in tok if ch.isalnum())
            if len(tok) < 3:
                continue
            v[_bucket(tok)] += _sign(tok)
        n = np.linalg.norm(v)
        if n == 0.0:  # empty / all-short text: deterministic fallback unit vector
            v = np.zeros(dim, dtype=np.float32)
            v[_bucket(text)] = 1.0
            n = 1.0
        return (v / n).astype(np.float32)

    return embed


class FakeNlp:
    """A spaCy-shaped fake: ``nlp(text).ents`` of ORG entities for Capitalized words."""

    class _Ent:
        def __init__(self, text: str):
            self.lemma_ = text.lower()
            self.label_ = "ORG"

    def __call__(self, text: str):
        ents = [FakeNlp._Ent(w) for w in text.split() if w[:1].isupper() and len(w) > 3]

        class _Doc:
            pass

        doc = _Doc()
        doc.ents = ents
        return doc
