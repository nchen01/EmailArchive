"""TF-IDF over thread documents (spec 03 §5 note).

One document per thread (cleaned text, concatenated). ``top_terms`` returns the
highest-weight 1–2gram terms for a thread. Wrapped so the rest of the pipeline
never touches sklearn directly and so ``top_terms`` is deterministic (ties broken
by term string).
"""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer


class ThreadTfidf:
    def __init__(self, thread_ids: list[str], docs: list[str]):
        self._index = {tid: i for i, tid in enumerate(thread_ids)}
        # Guard the degenerate all-empty corpus: sklearn raises on an empty vocab.
        non_empty = [d for d in docs if d.strip()]
        if not non_empty:
            self._vec = None
            self._matrix = None
            self._terms: list[str] = []
            return
        self._vec = TfidfVectorizer(
            ngram_range=(1, 2),
            lowercase=True,
            stop_words="english",
            token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b",
        )
        self._matrix = self._vec.fit_transform(docs)
        self._terms = list(self._vec.get_feature_names_out())

    @classmethod
    def fit(cls, threads, messages_by_thread: dict) -> "ThreadTfidf":
        thread_ids, docs = [], []
        for th in sorted(threads, key=lambda t: t.id):
            msgs = messages_by_thread.get(th.id, [])
            thread_ids.append(th.id)
            docs.append(" ".join(m.clean_text for m in msgs))
        return cls(thread_ids, docs)

    def top_terms(self, thread_id: str, k: int = 12) -> list[str]:
        if self._vec is None or thread_id not in self._index:
            return []
        row = self._matrix[self._index[thread_id]]
        coo = row.tocoo()
        # Deterministic: sort by weight desc, then term string asc.
        scored = sorted(
            ((self._terms[j], v) for j, v in zip(coo.col, coo.data)),
            key=lambda kv: (-kv[1], kv[0]),
        )
        return [t for t, _ in scored[:k]]
