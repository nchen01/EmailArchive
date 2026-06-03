"""SQLAlchemy engine + session factory (spec 04).

The connection string comes from the ``DATABASE_URL`` env var so the same code
serves dev (Docker Postgres), CI, and tests. Falls back to the dev URL.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg2://ekc:ekc_dev_password@localhost:5432/ekc_dev"
)


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


engine = create_engine(get_database_url(), future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
