"""Shared upsert plumbing for modeling pipeline DB writes.

All 4 models follow the same shape: optionally delete stale rows (for
multi-season tables that get fully rebuilt each run), then batch-upsert via
psycopg2 execute_values. Gap Matching has no delete step — it only updates
gap_match/breakdown onto rows that Scheme Fit already wrote, so delete_sql
stays None for that caller.
"""
from __future__ import annotations

from typing import Any, Sequence

from psycopg2.extras import execute_values
from sqlalchemy import Engine


def upsert_with_season_replace(
    engine: Engine,
    upsert_sql: str,
    records: Sequence[tuple[Any, ...]],
    delete_sql: str | None = None,
    delete_params: tuple[Any, ...] | None = None,
    page_size: int = 500,
) -> tuple[int, int]:
    """Delete stale rows (if delete_sql given), then batch-upsert records.

    Returns (deleted_count, upserted_count).
    """
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            deleted = 0
            if delete_sql is not None:
                cur.execute(delete_sql, delete_params or ())
                deleted = cur.rowcount
            execute_values(cur, upsert_sql, records, page_size=page_size)
        raw_conn.commit()
    finally:
        raw_conn.close()
    return deleted, len(records)
