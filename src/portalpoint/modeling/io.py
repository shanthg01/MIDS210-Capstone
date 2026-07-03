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

    sslmode is extracted from the URL query string and passed via connect_args
    instead of the URL — SQLAlchemy's psycopg2 dialect misroutes host resolution
    when sslmode appears as a URL query param (libpq reads it before applying
    the explicit host/port, causing it to fall back to service/passfile lookup).
    """
    import re
    raw_url = os.environ.get("DATABASE_URL") or load_env().get(
        "DATABASE_URL", "postgresql+asyncpg://postgres:password@localhost:5433/portalpoint"
    )
    # Strip ssl/sslmode from URL query string; pass via connect_args instead
    sync_url = raw_url.replace("+asyncpg", "+psycopg2")
    ssl_required = bool(re.search(r"[?&]ssl(?:mode)?=require", sync_url))
    sync_url = re.sub(r"[?&]ssl(?:mode)?=require", "", sync_url)
    # Clean up dangling ? or & left over after stripping
    sync_url = re.sub(r"\?$", "", sync_url)
    connect_args: dict = {}
    if ssl_required:
        connect_args["sslmode"] = "require"
    return create_engine(
        sync_url, echo=False, pool_pre_ping=True, pool_recycle=1800,
        connect_args=connect_args,
    )
