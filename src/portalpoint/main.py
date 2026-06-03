from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from portalpoint.api.routers import (
    auth,
    comparison,
    fit_scores,
    players,
    predictions,
    projections,
    recommendations,
    users,
)
from portalpoint.core.config import settings

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


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}
