"""Download and validate the spaCy model used for clustering NER (spec 03 §E).

The ``en_core_web_sm`` model is a model download, not a pip dependency, so it is
provisioned here rather than via ``pyproject.toml``. Idempotent: a second run is
a no-op if the model already loads.

Usage:
    python scripts/download_models.py
"""
from __future__ import annotations

import subprocess
import sys

MODEL = "en_core_web_sm"


def _try_load() -> bool:
    try:
        import spacy

        spacy.load(MODEL)
        return True
    except Exception:
        return False


def main() -> int:
    try:
        import spacy  # noqa: F401
    except ImportError:
        print("spaCy is not installed. Run: pip install -e .[clustering]", file=sys.stderr)
        return 1

    if _try_load():
        print(f"[ok] spaCy model '{MODEL}' already present.")
        return 0

    print(f"[..] downloading spaCy model '{MODEL}' ...")
    proc = subprocess.run([sys.executable, "-m", "spacy", "download", MODEL])
    if proc.returncode != 0:
        print(f"[fail] spacy download exited {proc.returncode}", file=sys.stderr)
        return proc.returncode

    if not _try_load():
        print(f"[fail] '{MODEL}' downloaded but does not load.", file=sys.stderr)
        return 1

    print(f"[ok] spaCy model '{MODEL}' downloaded and validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
