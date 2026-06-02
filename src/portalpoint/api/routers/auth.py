from fastapi import APIRouter

from portalpoint.api.schemas.auth import LoginRequest, LogoutResponse, SignupRequest, TokenResponse
from portalpoint.core.security import create_access_token, hash_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_STUB_USER_ID = 1001


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(body: SignupRequest):
    # STUB — hashes password but skips DB insert; replace with real user creation in Phase 2
    _hashed = hash_password(body.password)  # noqa: F841 — will be stored in DB
    token = create_access_token(_STUB_USER_ID)
    return TokenResponse(
        access_token=token,
        expires_in=3600,
        user_id=_STUB_USER_ID,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    # STUB — skips credential lookup; replace with DB query + verify_password in Phase 2
    token = create_access_token(_STUB_USER_ID)
    return TokenResponse(
        access_token=token,
        expires_in=3600,
        user_id=_STUB_USER_ID,
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout():
    # Stateless JWT — no server-side revocation in stub; add token denylist in Phase 2
    return LogoutResponse(message="Logged out successfully")
