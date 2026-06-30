"""Shared repo-root/.env/DB-connection helpers for the modeling pipeline.

Notebooks use a sync psycopg2 engine (interactive, no event loop); the async
engine in portalpoint.db.session is for the FastAPI app and ingest scripts —
kept separate intentionally.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine


@lru_cache(maxsize=1)
def find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "pyproject.toml").exists():
            return p
    raise FileNotFoundError("Could not find repo root (pyproject.toml)")


@lru_cache(maxsize=1)
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    dotenv = find_repo_root() / ".env"
    if not dotenv.exists():
        return env
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def get_sync_engine() -> Engine:
    """Sync SQLAlchemy engine (psycopg2) for notebook/script DB access.

    Real env vars win over .env file (matches mlflow_helpers.ensure_aws_env's
    precedence) — needed for CI, which sets DATABASE_URL directly and has no
    .env file at all.
    """
    raw_url = os.environ.get("DATABASE_URL") or load_env().get(
        "DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost:5433/portalpoint"
    )
    sync_url = raw_url.replace("+asyncpg", "+psycopg2").replace("ssl=require", "sslmode=require")
    return create_engine(sync_url, echo=False)
