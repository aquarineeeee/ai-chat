from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.user import User


settings = get_settings()
engine = create_async_engine(settings.async_database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def bootstrap_admin_user() -> None:
    async with AsyncSessionLocal() as session:
        user_count = await session.scalar(select(func.count()).select_from(User))
        if not user_count:
            session.add(User(id=1, username="admin", password_hash=settings.login_password_hash))
            await session.commit()
