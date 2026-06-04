"""Stage A — feature extraction (spec 03 §5).

Produces one ``ThreadFeatures`` per thread from the raw L0 records (``Thread`` +
its ``Message`` list) plus the L1 identity map. ``ThreadFeatures`` is a
compute-only artifact (not a persisted contract) per ekc_schemas convention #6.

Key correction vs. the spec's illustrative code: the real ``Message`` model
(ekc_schemas) does not carry ``participant_person_ids`` / ``sender_person_id`` /
``attachment_hashes``. We derive participants from ``sender`` / ``to`` / ``cc``
addresses through ``email_to_person_id``, and attachment hashes from
``attachment_refs``. The OWNER is excluded exactly once, here (convention #4).

All set-derived fields are produced from *sorted* inputs so nothing leaks
unordered iteration into downstream IDs/labels (decision H).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .labeling_tfidf import ThreadTfidf  # noqa: F401  (re-export convenience)


@dataclass(frozen=True)
class ThreadFeatures:
    thread_id: str
    participants: frozenset            # person_ids; OWNER EXCLUDED
    keywords: frozenset                # lemmatized entities + salient TF-IDF terms
    embedding: np.ndarray              # float32, L2-normalized
    t_start: datetime
    t_end: datetime
    attachment_hashes: frozenset       # sha256 of attachment bytes (from L0)
    link_domains: frozenset            # registered domains of URLs in bodies
    msg_count_by_person: dict          # person_id -> messages they sent in this thread


def _aware(dt: datetime) -> datetime:
    from datetime import timezone

    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def build_thread_features(
    threads,
    messages_by_thread: dict,
    email_to_person_id: dict,
    owner_person_id,
    embed_fn,
    nlp,
    tfidf,
) -> list[ThreadFeatures]:
    """Build features for every thread.

    Parameters
    ----------
    threads
        Iterable of L0 ``Thread`` records.
    messages_by_thread
        ``{thread.id: [Message, ...]}`` — messages for each thread (any order).
    email_to_person_id
        ``{email: person_id}`` from L1 identity resolution.
    owner_person_id
        The owner's resolved ``person_id``; excluded from participants.
    embed_fn
        ``str -> np.ndarray`` (shared with L2). Injectable for tests.
    nlp
        spaCy-like callable returning a doc with ``.ents`` (each ``.lemma_`` /
        ``.label_``). Injectable for tests.
    tfidf
        Fitted :class:`ThreadTfidf` exposing ``top_terms(thread_id, k)``.
    """
    feats: list[ThreadFeatures] = []
    # Stable thread ordering keeps the feature list deterministic regardless of
    # how the caller ordered `threads`.
    for th in sorted(threads, key=lambda t: t.id):
        msgs = sorted(messages_by_thread.get(th.id, []), key=lambda m: (m.ts, m.id))
        if not msgs:
            continue

        sent_counts: Counter = Counter()      # person_id -> messages they SENT
        participants: set = set()
        for m in msgs:
            sender_pid = email_to_person_id.get(m.sender.email)
            recipients = {
                email_to_person_id.get(a.email) for a in (m.to + m.cc)
            }
            for pid in {sender_pid} | recipients:
                if pid is not None and pid != owner_person_id:
                    participants.add(pid)
            if sender_pid is not None and sender_pid != owner_person_id:
                sent_counts[sender_pid] += 1

        # Embedding = token-length-weighted mean of per-message embeddings, normalized.
        embs = np.vstack([np.asarray(embed_fn(m.clean_text), dtype="float32") for m in msgs])
        w = np.array([max(len(m.clean_text.split()), 1) for m in msgs], dtype="float32")
        emb = (embs * w[:, None]).sum(0) / w.sum()
        emb = (emb / (np.linalg.norm(emb) + 1e-9)).astype("float32")

        # Keywords = NER entities + top TF-IDF terms.
        text = " ".join(m.clean_text for m in msgs)[:20000]
        ents = {
            e.lemma_.lower()
            for e in nlp(text).ents
            if e.label_ in {"ORG", "PRODUCT", "WORK_OF_ART", "PERSON", "EVENT"}
        }
        kw = ents | set(tfidf.top_terms(th.id, k=12))

        attach = sorted({a.sha256 for m in msgs for a in m.attachment_refs})
        domains = sorted({d for m in msgs for d in m.link_domains})

        feats.append(
            ThreadFeatures(
                thread_id=th.id,
                participants=frozenset(participants),
                keywords=frozenset(kw),
                embedding=emb,
                t_start=_aware(min(m.ts for m in msgs)),
                t_end=_aware(max(m.ts for m in msgs)),
                attachment_hashes=frozenset(attach),
                link_domains=frozenset(domains),
                msg_count_by_person={p: sent_counts[p] for p in sorted(sent_counts)},
            )
        )
    return feats
