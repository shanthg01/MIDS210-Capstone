from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from portalpoint.api.routers import (
    agent,
    auth,
    comparison,
    fit_scores,
    players,
    predictions,
    projections,
    recommendations,
    schools,
    users,
)
from portalpoint.core.config import settings
from portalpoint.db.session import AsyncSessionLocal

app = FastAPI(
    title="PortalPoint API",
    version="0.1.0",
    description="Transfer portal decision platform for college basketball",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(players.router)
app.include_router(recommendations.router)
app.include_router(fit_scores.router)
app.include_router(predictions.router)
app.include_router(projections.router)
app.include_router(users.router)
app.include_router(comparison.router)
app.include_router(schools.router)
app.include_router(agent.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready", tags=["health"])
async def ready(response: Response) -> dict[str, str]:
    """DB-aware readiness check — wired into the ALB target group health check,
    not /health, so a DB outage marks the ECS task unhealthy instead of every
    real request silently 500ing (see docs/production_db_connectivity_plan.md).
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not ready", "database": "unreachable"}
    return {"status": "ready", "database": "ok"}
