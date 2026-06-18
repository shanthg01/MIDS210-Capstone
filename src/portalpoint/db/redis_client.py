from __future__ import annotations

import redis.asyncio as redis

from portalpoint.core.config import settings

_pool = redis.ConnectionPool.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)


def get_redis() -> redis.Redis:
    """FastAPI dependency — Redis client backed by a shared connection pool.

    Connection/command failures are not raised here; callers must catch them
    at the point of use (get/set) so a down Redis degrades to direct DB reads
    instead of failing the request.
    """
    return redis.Redis(connection_pool=_pool)
