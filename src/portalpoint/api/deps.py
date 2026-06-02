from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from portalpoint.core.security import decode_access_token

_bearer = HTTPBearer()


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> int:
    """Validate Bearer JWT and return the encoded user_id.

    Permissive stub: verifies signature and expiry but does NOT check that the
    user exists in the DB. Add a DB lookup here in Phase 2.
    """
    try:
        return decode_access_token(credentials.credentials)
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Convenience alias — import this in routers
CurrentUser = Annotated[int, Depends(get_current_user_id)]
