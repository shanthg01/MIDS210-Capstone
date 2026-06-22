from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from portalpoint.api.deps import DbSession
from portalpoint.api.schemas.auth import LoginRequest, LogoutResponse, SignupRequest, TokenResponse
from portalpoint.core.security import create_access_token, hash_password, verify_password
from portalpoint.db.models import User, UserPreference

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(body: SignupRequest, db: DbSession):
    existing = (
        await db.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
    )
    db.add(user)
    try:
        await db.flush()  # populate user.id before creating preferences
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    db.add(UserPreference(user_id=user.id))
    await db.commit()
    await db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=3600,
        user_id=user.id,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DbSession):
    user = (
        await db.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in=3600,
        user_id=user.id,
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout():
    # Stateless JWT — no server-side revocation; add token denylist in Phase 2
    return LogoutResponse(message="Logged out successfully")
