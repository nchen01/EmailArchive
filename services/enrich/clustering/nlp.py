"""Production spaCy loader (spec 03 decision E).

The ``en_core_web_sm`` model is a download, not a pip dependency. Load it with a
clear, actionable error if it is missing. Tests never call this — they inject a
fake nlp from ``testkit`` instead.
"""
from __future__ import annotations


def load_nlp():
    try:
        import spacy

        return spacy.load("en_core_web_sm")
    except OSError as exc:  # model not downloaded
        raise RuntimeError(
            "spaCy model 'en_core_web_sm' is not installed.\n"
            "Run: python -m spacy download en_core_web_sm\n"
            "Or:  python scripts/download_models.py"
        ) from exc
