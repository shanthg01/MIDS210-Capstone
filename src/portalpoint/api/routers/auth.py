from fastapi import APIRouter

from portalpoint.api.schemas.auth import LoginRequest, LogoutResponse, SignupRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

_STUB_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".c3R1Yi1wYXlsb2Fk"
    ".stub-signature"
)


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(body: SignupRequest):
    # STUB — replace with DB user creation + real JWT in Step 5
    return TokenResponse(
        access_token=_STUB_TOKEN,
        token_type="bearer",
        expires_in=3600,
        user_id=1001,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    # STUB — replace with credential check + real JWT in Step 5
    return TokenResponse(
        access_token=_STUB_TOKEN,
        token_type="bearer",
        expires_in=3600,
        user_id=1001,
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout():
    # STUB — replace with token revocation in Step 5
    return LogoutResponse(message="Logged out successfully")
