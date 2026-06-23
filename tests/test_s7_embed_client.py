"""S7.3/S7.4 tests — EmbedClient seam, FakeEmbedClient, and RetrievalParams.

All tests here are offline by default; none touch the Voyage AI API.
The single integration test at the bottom is skipped unless VOYAGE_API_KEY is
set in the environment.
"""
from __future__ import annotations

import math
import os

import pytest

from services.retrieval.embed_client import EmbedClient, EmbedError, FakeEmbedClient
from services.retrieval.params import RetrievalParams


# ── FakeEmbedClient — determinism ─────────────────────────────────────────────

def test_fake_embed_document_is_deterministic():
    client = FakeEmbedClient(dim=8)
    v1 = client.embed_documents(["hello world"])[0]
    v2 = client.embed_documents(["hello world"])[0]
    assert v1 == v2


def test_fake_embed_query_is_deterministic():
    client = FakeEmbedClient(dim=8)
    assert client.embed_query("hello") == client.embed_query("hello")


def test_fake_embed_different_texts_differ():
    client = FakeEmbedClient(dim=32)
    v1 = client.embed_documents(["apple"])[0]
    v2 = client.embed_documents(["orange"])[0]
    assert v1 != v2


def test_fake_embed_query_and_document_identical_for_same_text():
    # In fake mode, query and document embeddings are the same vector for the
    # same text. Voyage's query/document modes are in the same comparable space;
    # making them identical here ensures an exact text match scores cosine ≈ 1.0
    # in retrieval evals, so evals test retrievability not just plumbing.
    client = FakeEmbedClient(dim=32)
    assert client.embed_documents(["hello"])[0] == client.embed_query("hello")


# ── FakeEmbedClient — shape and normalisation ─────────────────────────────────

def test_fake_embed_returns_correct_dim():
    client = FakeEmbedClient(dim=16)
    vec = client.embed_documents(["test text"])[0]
    assert len(vec) == 16


def test_fake_embed_query_returns_single_vector():
    client = FakeEmbedClient(dim=16)
    vec = client.embed_query("a query")
    assert isinstance(vec, list)
    assert len(vec) == 16


def test_fake_embed_is_unit_vector():
    client = FakeEmbedClient(dim=64)
    vec  = client.embed_documents(["normalised"])[0]
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-9


def test_fake_embed_documents_batch():
    client = FakeEmbedClient(dim=8)
    texts = ["one", "two", "three"]
    vecs  = client.embed_documents(texts)
    assert len(vecs) == 3
    assert all(len(v) == 8 for v in vecs)


def test_fake_embed_documents_empty_batch():
    client = FakeEmbedClient(dim=8)
    assert client.embed_documents([]) == []


def test_fake_embed_query_ranks_matching_document_first():
    # The core retrievability requirement: a query for "atlas rollout" must
    # score higher cosine similarity against a document containing those tokens
    # than against unrelated content. Validates that the feature-hash embedding
    # carries topical signal, not just plumbing.
    client = FakeEmbedClient(dim=64)
    query       = client.embed_query("atlas rollout deployment")
    doc_match   = client.embed_documents(["atlas rollout deployment complete"])[0]
    doc_noise   = client.embed_documents(["quarterly budget review finance"])[0]

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na  = math.sqrt(sum(x * x for x in a))
        nb  = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb)

    assert cosine(query, doc_match) > cosine(query, doc_noise), (
        f"Expected matching doc to rank higher: "
        f"sim(match)={cosine(query, doc_match):.3f}, "
        f"sim(noise)={cosine(query, doc_noise):.3f}"
    )


def test_fake_embed_dim_zero_raises():
    with pytest.raises(EmbedError, match="dim"):
        FakeEmbedClient(dim=0)


def test_fake_embed_dim_negative_raises():
    with pytest.raises(EmbedError, match="dim"):
        FakeEmbedClient(dim=-1)


# ── FakeEmbedClient — protocol conformance ───────────────────────────────────

def test_fake_embed_satisfies_protocol():
    client = FakeEmbedClient()
    assert isinstance(client, EmbedClient)


def test_fake_embed_model_and_dim_properties():
    client = FakeEmbedClient(dim=512, model="my-fake")
    assert client.model == "my-fake"
    assert client.dim   == 512


# ── VoyageEmbedClient — offline error paths ───────────────────────────────────

def test_voyage_client_raises_embed_error_without_key(monkeypatch):
    # Constructing VoyageEmbedClient without a key must always raise EmbedError
    # (never a raw ImportError or KeyError that a caller would not know to catch).
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from services.retrieval.embed_client import VoyageEmbedClient
    with pytest.raises(EmbedError):
        VoyageEmbedClient(api_key=None)


def test_voyage_client_dim_zero_raises():
    from services.retrieval.embed_client import VoyageEmbedClient
    with pytest.raises(EmbedError, match="dim"):
        VoyageEmbedClient(api_key="k", dim=0)


# ── VoyageEmbedClient — HTTP path (offline, stubbed httpx) ────────────────────
#
# These exercise the REST client without a live API call by monkeypatching
# httpx.post on the instance.  They prove the client no longer depends on the
# voyageai SDK and that response parsing, ordering, validation, and error
# sanitisation behave correctly.

class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP 500 with secret body that must not leak")

    def json(self):
        return self._payload


def _client_with_stub(monkeypatch, response=None, raises=None, capture=None):
    from services.retrieval import embed_client as ec
    client = ec.VoyageEmbedClient(api_key="test-key", dim=4)

    def fake_post(url, json=None, headers=None, timeout=None):
        if capture is not None:
            capture["url"] = url
            capture["json"] = json
            capture["headers"] = headers
            capture["timeout"] = timeout
        if raises is not None:
            raise raises
        return response

    monkeypatch.setattr(client._httpx, "post", fake_post)
    return client


def test_voyage_does_not_import_voyageai_sdk():
    """Importing the client must never pull in the voyageai/langchain chain.

    Runs in a fresh subprocess so the assertion is not contaminated by other
    test modules that may have already imported these packages into sys.modules
    in this pytest session.  This is the regression guard for the S10 runtime
    fix: the native uuid_utils .pyd (blocked by Windows Application Control) must
    not be on the embed-client import path.
    """
    import subprocess
    import sys

    code = (
        "import sys; "
        "from services.retrieval.embed_client import VoyageEmbedClient; "
        "forbidden={'voyageai','langchain','langchain_core',"
        "'langchain_text_splitters','uuid_utils'}; "
        "pulled={m.split('.')[0] for m in sys.modules}; "
        "bad=forbidden & pulled; "
        "sys.exit('FORBIDDEN:'+','.join(sorted(bad)) if bad else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"embed client pulled in forbidden modules: {result.stdout}{result.stderr}"
    )


def test_voyage_embed_documents_parses_response(monkeypatch):
    payload = {
        "data": [
            {"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4]},
            {"index": 1, "embedding": [0.5, 0.6, 0.7, 0.8]},
        ],
        "usage": {"total_tokens": 7},
    }
    client = _client_with_stub(monkeypatch, response=_FakeResponse(payload))
    vecs = client.embed_documents(["a", "b"])
    assert vecs == [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]


def test_voyage_embed_query_returns_single_vector(monkeypatch):
    payload = {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]}]}
    client = _client_with_stub(monkeypatch, response=_FakeResponse(payload))
    vec = client.embed_query("hello")
    assert vec == [1.0, 0.0, 0.0, 0.0]


def test_voyage_orders_by_index(monkeypatch):
    # Response intentionally out of order — client must sort by 'index'.
    payload = {
        "data": [
            {"index": 1, "embedding": [9, 9, 9, 9]},
            {"index": 0, "embedding": [0, 0, 0, 0]},
        ],
    }
    client = _client_with_stub(monkeypatch, response=_FakeResponse(payload))
    vecs = client.embed_documents(["first", "second"])
    assert vecs[0] == [0, 0, 0, 0]
    assert vecs[1] == [9, 9, 9, 9]


def test_voyage_empty_batch_short_circuits(monkeypatch):
    called = {"n": 0}
    client = _client_with_stub(monkeypatch, response=_FakeResponse({"data": []}))

    def counting_post(*a, **k):
        called["n"] += 1
        return _FakeResponse({"data": []})

    monkeypatch.setattr(client._httpx, "post", counting_post)
    assert client.embed_documents([]) == []
    assert called["n"] == 0, "empty batch must not make an HTTP call"


def test_voyage_request_shape_and_headers(monkeypatch):
    capture: dict = {}
    payload = {"data": [{"index": 0, "embedding": [0, 0, 0, 0]}]}
    client = _client_with_stub(
        monkeypatch, response=_FakeResponse(payload), capture=capture
    )
    client.embed_query("q")
    assert capture["json"]["model"] == "voyage-4"
    assert capture["json"]["input_type"] == "query"
    assert capture["json"]["output_dimension"] == 4
    assert capture["json"]["input"] == ["q"]
    assert capture["headers"]["Authorization"] == "Bearer test-key"
    assert capture["timeout"] is not None


def test_voyage_wrong_vector_length_raises(monkeypatch):
    payload = {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}  # dim 2, expected 4
    client = _client_with_stub(monkeypatch, response=_FakeResponse(payload))
    with pytest.raises(EmbedError, match="length 2"):
        client.embed_documents(["x"])


def test_voyage_count_mismatch_raises(monkeypatch):
    payload = {"data": [{"index": 0, "embedding": [0, 0, 0, 0]}]}  # 1 vec for 2 inputs
    client = _client_with_stub(monkeypatch, response=_FakeResponse(payload))
    with pytest.raises(EmbedError):
        client.embed_documents(["x", "y"])


def test_voyage_http_error_is_sanitised(monkeypatch):
    # A non-2xx response must raise EmbedError WITHOUT leaking the response body.
    client = _client_with_stub(
        monkeypatch, response=_FakeResponse({}, status_ok=False)
    )
    with pytest.raises(EmbedError) as exc_info:
        client.embed_documents(["x"])
    assert "secret body" not in str(exc_info.value)
    assert "Voyage API call failed" in str(exc_info.value)


def test_voyage_network_error_is_sanitised(monkeypatch):
    client = _client_with_stub(
        monkeypatch, raises=ConnectionError("connection refused to 1.2.3.4")
    )
    with pytest.raises(EmbedError) as exc_info:
        client.embed_documents(["x"])
    assert "1.2.3.4" not in str(exc_info.value)
    assert "ConnectionError" in str(exc_info.value)


# ── RetrievalParams ───────────────────────────────────────────────────────────

def test_retrieval_params_defaults():
    p = RetrievalParams()
    assert p.embed_model            == "voyage-4"
    assert p.embed_dim              == 1024
    assert p.vector_top_k           == 20
    assert p.fts_top_k              == 20
    assert p.rerank_top_k           == 10
    assert p.min_vector_score       == 0.60
    assert p.min_fts_score          == 0.0
    assert p.vector_weight          == 0.6
    assert p.fts_weight             == 0.4
    assert p.recency_weight         == 0.05
    assert p.include_noise          is False
    assert p.include_sensitive      is False
    assert p.enable_reranking       is False
    assert p.project_boost          == 0.15
    assert p.person_boost           == 0.10
    assert p.recency_half_life_days == 180


def test_retrieval_params_overrideable():
    p = RetrievalParams(embed_model="fake-embed", embed_dim=8, vector_top_k=5)
    assert p.embed_model  == "fake-embed"
    assert p.embed_dim    == 8
    assert p.vector_top_k == 5
    # Unchanged defaults remain
    assert p.fts_top_k    == 20


def test_retrieval_params_weights_sum_to_one():
    p = RetrievalParams()
    assert abs((p.vector_weight + p.fts_weight) - 1.0) < 1e-9


# ── Voyage AI integration test (skipped without VOYAGE_API_KEY) ───────────────

@pytest.mark.skipif(
    not os.environ.get("VOYAGE_API_KEY"),
    reason="VOYAGE_API_KEY not set — skipping live Voyage AI integration test.",
)
def test_voyage_embed_documents_live():
    pytest.importorskip(
        "httpx",
        reason="httpx not installed (pip install -e .[dev]) — skipping.",
    )
    from services.retrieval.embed_client import VoyageEmbedClient

    client = VoyageEmbedClient()
    assert client.model == "voyage-4"
    assert client.dim   == 1024

    vecs = client.embed_documents(["This is a test document for embedding."])
    assert len(vecs) == 1
    assert len(vecs[0]) == 1024

    norm = math.sqrt(sum(x * x for x in vecs[0]))
    assert abs(norm - 1.0) < 0.05, f"Expected unit vector, got norm={norm:.4f}"
