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
    # Whether voyageai is installed or not, constructing VoyageEmbedClient
    # without a key must always raise EmbedError (never a raw ImportError or
    # KeyError that a caller would not know to catch).
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from services.retrieval.embed_client import VoyageEmbedClient
    with pytest.raises(EmbedError):
        VoyageEmbedClient(api_key=None)


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
    voyageai = pytest.importorskip(
        "voyageai",
        reason="voyageai not installed (pip install -e .[retrieval]) — skipping.",
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
