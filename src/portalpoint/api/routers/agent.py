"""Live API surface for the news-monitoring agent (scripts/run_news_monitoring.py
was CLI-only until now — no router, not registered in main.py, zero frontend
visibility into what the agent has found or when it last ran).

A run does real Tavily + Gemini calls (can take minutes), so it's kicked off
via BackgroundTasks and polled by run_id rather than blocking the request.
Redis is the job store — this is ephemeral operational state (which run is
in flight, its summary), not a modeling artifact, so a new DB table isn't
warranted.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from redis.asyncio import Redis
from sqlalchemy import select

from portalpoint.agents.news_monitoring.runner import run as run_news_monitoring_cycle
from portalpoint.api.deps import AdminUser, CurrentUser, DbSession, RedisClient
from portalpoint.api.schemas.agent import (
    AgentRunAccepted,
    AgentRunRequest,
    AgentRunStatus,
    ProgramEventItem,
    ProgramEventsResponse,
)
from portalpoint.db.models import Coach, Player, ProgramEvent, School

# Agent writes coach name into raw_text but does not set coach_id today —
# recover a display name so Agent Activity is not blank for coach_departed.
_COACH_NAME_FROM_RAW = re.compile(
    r"(?:\[demo\]\s*)?News agent detected coaching departure:\s*(.+?)\s+from\s+",
    re.IGNORECASE,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])
log = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "agent:news_monitoring:run:"
_REDIS_TTL_SECONDS = 86400  # 24h — long enough to check on an overnight run

# Single-flight lock (PR #64 review — the run endpoint had no protection
# against overlapping Tavily/Gemini jobs; any authenticated user could fire
# unlimited concurrent runs). TTL is a crash-recovery ceiling only — a
# successful run always releases the lock itself in the `finally` below;
# 30 min comfortably covers a real run and bounds how long a hard crash
# (process killed mid-run, no `finally` executed) can wedge the lock.
_LOCK_KEY = "agent:news_monitoring:lock"
_LOCK_TTL_SECONDS = 1800


def _redis_key(run_id: str) -> str:
    return f"{_REDIS_KEY_PREFIX}{run_id}"


async def _execute_agent_run(run_id: str, redis: Redis, body: AgentRunRequest, started_at: str) -> None:
    """Background task: run the agent cycle in a thread, write status to Redis.

    Runs after the request has already returned, so it can't raise back to
    the caller — failures are captured into the Redis record instead.
    `started_at` is threaded through explicitly (rather than read back from
    the cycle's own summary) so it's always a real timestamp even when the
    cycle raises before producing a summary at all — AgentRunStatus.started_at
    is a required datetime field, and a null there was a real 500 in review.
    """
    kwargs: dict = {"dry_run": body.dry_run, "use_llm_classifier": body.use_llm}
    if body.season is not None:
        kwargs["season"] = body.season
    if body.window_days is not None:
        kwargs["window_days"] = body.window_days

    try:
        summary = await run_in_threadpool(run_news_monitoring_cycle, **kwargs)
        status = "completed" if summary.get("success") else "failed"
        record = {
            "run_id": run_id,
            "status": status,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — this is the terminal error boundary for a background task
        log.exception("news-monitoring run %s failed", run_id)
        record = {
            "run_id": run_id,
            "status": "failed",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "summary": None,
            "error": str(exc),
        }
    finally:
        try:
            await redis.delete(_LOCK_KEY)
        except Exception:
            log.exception("Failed to release news-monitoring run lock for %s", run_id)

    try:
        await redis.set(_redis_key(run_id), json.dumps(record), ex=_REDIS_TTL_SECONDS)
    except Exception:
        log.exception("Failed to persist news-monitoring run %s status to Redis", run_id)


@router.post("/news-monitoring/run", response_model=AgentRunAccepted, status_code=202)
async def start_news_monitoring_run(
    current_user: AdminUser,
    redis: RedisClient,
    background_tasks: BackgroundTasks,
    body: AgentRunRequest = AgentRunRequest(),
) -> AgentRunAccepted:
    run_id = str(uuid4())
    now = datetime.now(timezone.utc)

    # Fail-closed, not open: this endpoint depends on Redis for the concurrency
    # lock and run-status storage, and there's no ElastiCache in production yet
    # (REDIS_URL isn't reachable — see docs/status/ARCHITECTURE_STATUS.md's Cache
    # row). A 503 here is deliberate — the alternative (skip the lock, run anyway)
    # was considered and deferred; revisit alongside that decision, not in isolation.
    try:
        acquired = await redis.set(_LOCK_KEY, run_id, nx=True, ex=_LOCK_TTL_SECONDS)
    except Exception as exc:
        log.exception("Redis unavailable — cannot start news-monitoring run")
        raise HTTPException(
            status_code=503,
            detail="News-monitoring agent is temporarily unavailable (cache backend unreachable).",
        ) from exc

    if not acquired:
        raise HTTPException(
            status_code=409,
            detail="A news-monitoring run is already in progress — wait for it to finish before starting another.",
        )

    initial_record = {
        "run_id": run_id,
        "status": "running",
        "started_at": now.isoformat(),
        "finished_at": None,
        "summary": None,
        "error": None,
    }
    try:
        await redis.set(_redis_key(run_id), json.dumps(initial_record), ex=_REDIS_TTL_SECONDS)
    except Exception as exc:
        log.exception("Redis unavailable — cannot record initial run status for %s", run_id)
        try:
            await redis.delete(_LOCK_KEY)
        except Exception:
            log.exception("Failed to release lock after initial-status write failure for %s", run_id)
        raise HTTPException(
            status_code=503,
            detail="News-monitoring agent is temporarily unavailable (cache backend unreachable).",
        ) from exc
    background_tasks.add_task(_execute_agent_run, run_id, redis, body, now.isoformat())
    return AgentRunAccepted(run_id=run_id, status="running")


@router.get("/news-monitoring/runs/{run_id}", response_model=AgentRunStatus)
async def get_news_monitoring_run(
    current_user: CurrentUser,
    redis: RedisClient,
    run_id: str,
) -> AgentRunStatus:
    try:
        raw = await redis.get(_redis_key(run_id))
    except Exception as exc:
        log.exception("Redis unavailable — cannot fetch run status for %s", run_id)
        raise HTTPException(
            status_code=503,
            detail="News-monitoring agent status is temporarily unavailable (cache backend unreachable). "
            "Check GET /api/agent/news-monitoring/events for confirmed results instead.",
        ) from exc

    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"No run found for run_id={run_id} (expired after {_REDIS_TTL_SECONDS // 3600}h or never existed).",
        )
    record = json.loads(raw)
    return AgentRunStatus(**record)


@router.get("/news-monitoring/events", response_model=ProgramEventsResponse)
async def get_news_monitoring_events(
    current_user: CurrentUser,
    db: DbSession,
    school_id: int | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
) -> ProgramEventsResponse:
    """Recent program_events the agent has written — 'what did it find' feed."""
    stmt = (
        select(
            ProgramEvent,
            School.name.label("school_name"),
            Player.full_name.label("player_name"),
            Coach.full_name.label("coach_name"),
        )
        .outerjoin(School, ProgramEvent.school_id == School.id)
        .outerjoin(Player, ProgramEvent.player_id == Player.id)
        .outerjoin(Coach, ProgramEvent.coach_id == Coach.id)
        .order_by(ProgramEvent.created_at.desc())
        .limit(limit)
    )
    if school_id is not None:
        stmt = stmt.where(ProgramEvent.school_id == school_id)
    rows = (await db.execute(stmt)).all()
    events: list[ProgramEventItem] = []
    for event, school_name, player_name, coach_name in rows:
        resolved_coach = coach_name
        if resolved_coach is None and event.raw_text:
            match = _COACH_NAME_FROM_RAW.search(event.raw_text)
            if match:
                resolved_coach = match.group(1).strip() or None
        events.append(
            ProgramEventItem(
                id=event.id,
                event_type=event.event_type,
                school_id=event.school_id,
                school_name=school_name,
                player_id=event.player_id,
                player_name=player_name,
                coach_id=event.coach_id,
                coach_name=resolved_coach,
                event_date=event.event_date,
                source=event.source,
                confidence=event.confidence,
                match_status=event.match_status,
                created_at=event.created_at,
            )
        )
    return ProgramEventsResponse(events=events, total=len(events))
