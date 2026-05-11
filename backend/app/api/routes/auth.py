from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session, get_current_user
from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse
from app.services.auth import authenticate_user


router = APIRouter()
settings = get_settings()


@router.post("/login", response_model=MeResponse)
async def login(payload: LoginRequest, response: Response, session: AsyncSession = Depends(db_session)) -> MeResponse:
    user = await authenticate_user(session=session, username=payload.username, password=payload.password)
    token = create_access_token(subject=str(user.id))
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_days * 24 * 60 * 60,
        path="/",
    )
    response.status_code = status.HTTP_200_OK
    return MeResponse(id=user.id, username=user.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    response.delete_cookie(key="access_token", path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
async def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(id=current_user.id, username=current_user.username)
