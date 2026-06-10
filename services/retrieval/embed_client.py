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
import random
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

    Each text is hashed with SHA-256, which seeds a PRNG to generate a
    Gaussian vector that is then L2-normalised to a unit vector. The same
    text always produces the same embedding; different texts produce different
    embeddings with high probability.

    Document and query embeddings are distinct: "doc:<text>" and
    "query:<text>" are hashed separately, mirroring how Voyage AI's
    input_type parameter produces different representations for the same text.

    No API key, network access, or installed voyageai package required.
    """

    def __init__(self, dim: int = 1024, model: str = "fake-embed") -> None:
        self._dim   = dim
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, key: str) -> list[float]:
        digest = hashlib.sha256(key.encode()).digest()
        seed   = int.from_bytes(digest[:8], "big")
        rng    = random.Random(seed)
        vec    = [rng.gauss(0.0, 1.0) for _ in range(self._dim)]
        mag    = math.sqrt(sum(x * x for x in vec))
        return [x / mag for x in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(f"doc:{t}") for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(f"query:{text}")


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
        return result.embeddings
