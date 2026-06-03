"""Stage H — persistence (spec 00 §12). S1 is in-memory only; no DB."""
from __future__ import annotations

from dataclasses import dataclass

from ekc_schemas import Message, Thread


@dataclass
class IngestStore:
    messages: list[Message]
    threads: list[Thread]


def persist(messages: list[Message], threads: list[Thread]) -> IngestStore:
    return IngestStore(messages=messages, threads=threads)
