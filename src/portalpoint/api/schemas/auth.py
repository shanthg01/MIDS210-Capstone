from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str
    school_id: int


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    user_id: int
    # | None: only new signups are guaranteed to have a school — login must
    # keep working for any pre-existing account that predates this field.
    school_id: int | None = None


class LogoutResponse(BaseModel):
    message: str
