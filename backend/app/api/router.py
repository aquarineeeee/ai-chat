from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.branches import router as branches_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.keys import router as keys_router
from app.api.routes.providers import router as providers_router
from app.api.routes.messages import router as messages_router
from app.api.routes.mcp import router as mcp_router


api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(branches_router, tags=["branches"])
api_router.include_router(conversations_router, prefix="/conversations", tags=["conversations"])
api_router.include_router(keys_router, prefix="/keys", tags=["keys"])
api_router.include_router(providers_router, tags=["providers"])
api_router.include_router(messages_router, tags=["messages"])
api_router.include_router(mcp_router, tags=["mcp"])
