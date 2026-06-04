"""Deterministic test doubles for clustering (spec 03 decisions E & F).

These exist so tests and the eval never require a model download:
- ``make_test_embed`` — hash-based deterministic embeddings (stand-in for L2).
- ``FakeNlp`` — capitalized-token entity extractor (stand-in for spaCy NER).

Production code uses the real ``embed_fn`` and ``load_nlp()`` instead.
"""
from __future__ import annotations

import hashlib

import numpy as np


def make_test_embed(dim: int = 16):
    """Return a deterministic ``str -> np.ndarray`` embedding function."""

    def embed(text: str) -> np.ndarray:
        b = hashlib.sha256(text.encode()).digest()
        v = np.frombuffer(b[: dim * 2], dtype=np.int16).astype(np.float32)
        n = np.linalg.norm(v)
        return (v / (n + 1e-9)).astype(np.float32)

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
