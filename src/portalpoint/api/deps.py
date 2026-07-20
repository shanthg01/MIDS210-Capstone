from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from portalpoint.core.security import decode_access_token
from portalpoint.db.models import User
from portalpoint.db.redis_client import get_redis
from portalpoint.db.session import get_db

_bearer = HTTPBearer()


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> int:
    try:
        return decode_access_token(credentials.credentials)
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Convenience aliases — import these in routers
CurrentUser = Annotated[int, Depends(get_current_user_id)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]


async def require_admin_user_id(user_id: CurrentUser, db: DbSession) -> int:
    """Authorization on top of CurrentUser's authentication — 403 for any
    valid, authenticated user who isn't flagged is_admin. First use: gating
    the news-monitoring agent's run-trigger endpoint (PR #64 review — that
    endpoint previously accepted any authenticated user)."""
    is_admin = (
        await db.execute(select(User.is_admin).where(User.id == user_id))
    ).scalar_one_or_none()
    if not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user_id


AdminUser = Annotated[int, Depends(require_admin_user_id)]
