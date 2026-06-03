"""Request-scoped dependencies (spec 05)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from services.db.engine import SessionLocal


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
