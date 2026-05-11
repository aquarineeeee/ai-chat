from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.security import decode_token
from app.db.session import get_db_session
from app.models.user import User


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def get_current_user(request: Request, session: AsyncSession = Depends(db_session)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise AppError(status_code=401, code="UNAUTHORIZED", message="未登录")

    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise AppError(status_code=401, code="UNAUTHORIZED", message="登录已过期或无效") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise AppError(status_code=401, code="UNAUTHORIZED", message="登录凭证缺少用户信息")

    user = await session.scalar(select(User).where(User.id == int(user_id)))
    if user is None:
        raise AppError(status_code=401, code="UNAUTHORIZED", message="用户不存在")

    return user
