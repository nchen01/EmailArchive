"""Embedding client seam for L2 retrieval (S7.3, D12b).

Three exports:
- EmbedClient   — Protocol that any embedder must satisfy.
- FakeEmbedClient  — deterministic, offline, used by all unit tests and evals.
- VoyageEmbedClient — production client; requires VOYAGE_API_KEY + voyageai package.

All offline tests use FakeEmbedClient. VoyageEmbedClient is only constructed
when VOYAGE_API_KEY is available. voyageai is imported lazily inside
VoyageEmbedClient.__init__ so that importing this module never fails in
environments where the package is not installed.

Logging policy for VoyageEmbedClient: model name, batch size, latency, and
token count are logged. Text content, subjects, and query strings are never
logged — see D12d.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


class EmbedError(Exception):
    """Raised by EmbedClient implementations on configuration or API failure."""


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class EmbedClient(Protocol):
    """Minimal interface all embedding clients must satisfy."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document texts. Returns one vector per text."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string. Returns one vector."""
        ...

    @property
    def model(self) -> str:
        """Model identifier string, e.g. 'voyage-4' or 'fake-embed'."""
        ...

    @property
    def dim(self) -> int:
        """Embedding dimension, e.g. 1024."""
        ...


# ── FakeEmbedClient ───────────────────────────────────────────────────────────

class FakeEmbedClient:
    """Deterministic, offline embedder for tests and retrieval evals.

    Uses feature hashing over lowercased word tokens — the same technique as
    services/enrich/clustering/testkit.py. Texts sharing vocabulary land close
    in cosine space, just as a real sentence embedder would. This means retrieval
    evals test actual retrievability, not just plumbing.

    embed_query and embed_documents produce the same vector for the same text.
    Voyage AI's query/document modes are in the same comparable vector space; in
    fake mode the distinction is dropped so an exact text match has cosine
    similarity ≈ 1.0 and retrieval ranking is meaningful.

    No API key, network access, or installed voyageai package required.
    """

    def __init__(self, dim: int = 1024, model: str = "fake-embed") -> None:
        if dim <= 0:
            raise EmbedError(f"embed dim must be > 0, got {dim}")
        self._dim   = dim
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        """Feature-hash over lowercased word tokens → unit vector."""
        vec = [0.0] * self._dim
        for tok in text.lower().split():
            tok = "".join(ch for ch in tok if ch.isalnum())
            if len(tok) < 3:
                continue
            h      = hashlib.sha256(tok.encode()).digest()
            bucket = int.from_bytes(h[:4], "big") % self._dim
            sh     = hashlib.sha256((tok + "#sign").encode()).digest()
            sign   = 1.0 if (sh[0] & 1) else -1.0
            vec[bucket] += sign

        mag = math.sqrt(sum(x * x for x in vec))
        if mag == 0.0:
            # Fallback for empty or all-short-token text.
            h   = hashlib.sha256(text.encode()).digest()
            idx = int.from_bytes(h[:4], "big") % self._dim
            vec[idx] = 1.0
            mag = 1.0
        return [x / mag for x in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        # Identical to embed_documents in fake mode: query and document are in
        # the same space, so an exact text match scores cosine ≈ 1.0.
        return self._vector(text)


# ── VoyageEmbedClient ─────────────────────────────────────────────────────────

class VoyageEmbedClient:
    """Voyage AI embedding client for production use (voyage-4, 1024-dim).

    Requires the VOYAGE_API_KEY environment variable and the voyageai package
    (pip install voyageai, or pip install -e .[retrieval]).

    voyageai is imported lazily inside __init__ so importing this module is
    always safe in environments without the package installed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model:   str        = "voyage-4",
        dim:     int        = 1024,
    ) -> None:
        if dim <= 0:
            raise EmbedError(f"embed dim must be > 0, got {dim}")

        try:
            import voyageai as _voyageai
        except ImportError as exc:
            raise EmbedError(
                "voyageai is not installed.\n"
                "Run: pip install voyageai  (or: pip install -e .[retrieval])\n"
                "For tests and evals, use FakeEmbedClient instead."
            ) from exc

        key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise EmbedError(
                "VOYAGE_API_KEY is not set.\n"
                "Export it before constructing VoyageEmbedClient.\n"
                "For tests and evals, use FakeEmbedClient instead."
            )

        self._client = _voyageai.Client(api_key=key)
        self._model  = model
        self._dim    = dim

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, input_type="document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], input_type="query")[0]

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        t0 = time.monotonic()
        try:
            result = self._client.embed(
                texts,
                model=self._model,
                input_type=input_type,
                output_dimension=self._dim,
            )
        except Exception as exc:
            raise EmbedError(
                f"Voyage API call failed ({type(exc).__name__})"
            ) from exc
        latency_ms = (time.monotonic() - t0) * 1000
        tokens: Any = getattr(result, "total_tokens", None)
        log.debug(
            "voyage_embed",
            extra={
                "model":      self._model,
                "batch_size": len(texts),
                "input_type": input_type,
                "latency_ms": round(latency_ms, 1),
                "tokens":     tokens,
            },
        )
        embeddings = result.embeddings
        for i, vec in enumerate(embeddings):
            if len(vec) != self._dim:
                raise EmbedError(
                    f"Voyage returned vector of length {len(vec)} at index {i}, "
                    f"expected {self._dim} (model={self._model}, "
                    f"output_dimension={self._dim})"
                )
        return embeddings
