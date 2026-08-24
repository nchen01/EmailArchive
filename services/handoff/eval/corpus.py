"""Load the synthetic handoff-eval corpus (S43).

Pure file IO + a light coherence check. No DB, no network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# services/handoff/eval/corpus.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS_DIR = _REPO_ROOT / "fixtures" / "handoff_eval"


@dataclass
class Scenario:
    name: str
    data: dict
    path: str


def default_corpus_dir() -> Path:
    return DEFAULT_CORPUS_DIR


def load_corpus(corpus_dir: str | Path | None = None) -> list[Scenario]:
    """Load every ``*.json`` scenario in ``corpus_dir`` (sorted for determinism)."""
    d = Path(corpus_dir) if corpus_dir else DEFAULT_CORPUS_DIR
    out: list[Scenario] = []
    for p in sorted(d.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        out.append(Scenario(name=data["name"], data=data, path=str(p)))
    return out


def scenario_defined_headers(data: dict) -> set[str]:
    """All message_id_headers a scenario defines across its threads."""
    return {
        m["header"]
        for t in data.get("threads", [])
        for m in t.get("messages", [])
    }


def check_scenario_coherence(data: dict) -> list[str]:
    """Return a list of coherence problems (empty means coherent). Pure/DB-free.

    Verifies that every referenced header and project actually exists in the
    scenario, so a gold citation can never point at a message that was never seeded.
    """
    problems: list[str] = []
    headers = scenario_defined_headers(data)
    project_keys = {p["key"] for p in data.get("projects", [])}
    labels = {p["label"] for p in data.get("projects", [])}

    for e in data.get("events", []):
        for h in e.get("headers", []):
            if h not in headers:
                problems.append(f"event cites undefined header {h!r}")
        pk = e.get("project")
        if pk is not None and pk not in project_keys:
            problems.append(f"event references undefined project key {pk!r}")

    gold = data.get("gold", {})
    for key in ("decisions", "open_loops", "blockers"):
        for g in gold.get(key, []):
            for h in g.get("cites", []):
                if h not in headers:
                    problems.append(f"gold {key} cites undefined header {h!r}")
    for h in gold.get("excluded_headers", []):
        if h not in headers:
            problems.append(f"gold excluded_header {h!r} is not a defined message")
    for lbl in gold.get("project_labels", []):
        if lbl not in labels:
            problems.append(f"gold project_label {lbl!r} is not a defined project label")
    return problems
