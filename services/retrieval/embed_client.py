"""Embedding client seam for L2 retrieval (S7.3, D12b).

Three exports:
- EmbedClient   — Protocol that any embedder must satisfy.
- FakeEmbedClient  — deterministic, offline, used by all unit tests and evals.
- VoyageEmbedClient — production client; requires VOYAGE_API_KEY + httpx.

All offline tests use FakeEmbedClient. VoyageEmbedClient is only constructed
when VOYAGE_API_KEY is available.

Runtime dependency policy (S10 runtime-reliability fix):
  VoyageEmbedClient talks to the Voyage AI REST API directly over HTTP via
  httpx.  It deliberately does NOT import the ``voyageai`` SDK, which pulls in
  langchain_text_splitters -> langchain_core -> uuid_utils, the last of which
  loads a native ``_uuid_utils*.pyd`` that Windows Application Control can block.
  That import chain was crashing the app runtime even though only a thin embed
  call was needed.  Keeping the runtime path on a plain HTTP client removes the
  native-dependency surface entirely.  httpx is imported lazily inside
  VoyageEmbedClient.__init__ so importing this module never fails in environments
  where httpx is not installed.

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

# Voyage AI REST embeddings endpoint. Overridable via VOYAGE_API_BASE so tests
# can point at a local stub without touching the real API.
_VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
# Per-request HTTP timeout (seconds). A hung Voyage endpoint must not hang the
# backend forever — fail loudly instead.
_VOYAGE_HTTP_TIMEOUT = 30.0


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

    def embed_queries_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple query strings in one call. Returns one vector per text.

        Prefer this over repeated embed_query() calls when many queries are
        needed at once (e.g. eval runs) — VoyageEmbedClient sends one API
        request instead of N, avoiding per-query rate-limit hits.
        """
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

    def embed_queries_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


# ── VoyageEmbedClient ─────────────────────────────────────────────────────────

class VoyageEmbedClient:
    """Voyage AI embedding client for production use (voyage-4, 1024-dim).

    Talks to the Voyage REST API directly over HTTP (httpx).  Requires the
    VOYAGE_API_KEY environment variable and httpx (already in the test/dev
    extras; install with ``pip install httpx`` otherwise).

    This client intentionally does not import the ``voyageai`` SDK — see the
    module docstring for why (native uuid_utils .pyd blocked by Windows
    Application Control).  httpx is imported lazily inside __init__ so importing
    this module is always safe even where httpx is absent.
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
            import httpx as _httpx
        except ImportError as exc:
            raise EmbedError(
                "httpx is not installed.\n"
                "Run: pip install httpx  (or: pip install -e .[dev])\n"
                "For tests and evals, use FakeEmbedClient instead."
            ) from exc

        key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise EmbedError(
                "VOYAGE_API_KEY is not set.\n"
                "Export it before constructing VoyageEmbedClient.\n"
                "For tests and evals, use FakeEmbedClient instead."
            )

        self._httpx  = _httpx
        self._key    = key
        self._model  = model
        self._dim    = dim
        self._url    = os.environ.get("VOYAGE_API_BASE", _VOYAGE_API_URL)

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

    def embed_queries_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of query strings in a single Voyage API call."""
        return self._embed(texts, input_type="query")

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        # Short-circuit empty batches: never spend an API call (and cost) on
        # nothing. Mirrors FakeEmbedClient.embed_documents([]) == [].
        if not texts:
            return []

        payload = {
            "input": texts,
            "model": self._model,
            "input_type": input_type,
            "output_dimension": self._dim,
        }
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            resp = self._httpx.post(
                self._url,
                json=payload,
                headers=headers,
                timeout=_VOYAGE_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            # Never include the response body or headers — they could echo the
            # request text or the bearer token. Surface only a safe class name.
            raise EmbedError(
                f"Voyage API call failed ({type(exc).__name__})"
            ) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbedError(
                f"Voyage returned {len(data) if isinstance(data, list) else 'no'} "
                f"embedding(s) for {len(texts)} input(s) (model={self._model})"
            )

        # Order by the response 'index' field — the API is documented to return
        # results in input order, but sorting makes us robust if it does not.
        try:
            ordered = sorted(data, key=lambda d: d["index"])
        except (KeyError, TypeError):
            ordered = data
        embeddings = [d["embedding"] for d in ordered]

        usage = body.get("usage") or {}
        tokens: Any = usage.get("total_tokens")
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

        for i, vec in enumerate(embeddings):
            if len(vec) != self._dim:
                raise EmbedError(
                    f"Voyage returned vector of length {len(vec)} at index {i}, "
                    f"expected {self._dim} (model={self._model}, "
                    f"output_dimension={self._dim})"
                )
        return embeddings
