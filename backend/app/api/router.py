from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversations_router


api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(conversations_router, prefix="/conversations", tags=["conversations"])
