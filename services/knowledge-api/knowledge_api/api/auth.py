"""Expose the effective principal for frontend capability checks."""

from fastapi import APIRouter, Request

from knowledge_api.core.security import principal_from_request

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def current_principal(request: Request) -> dict[str, str]:
    principal = principal_from_request(request)
    if principal is None:
        return {"user_id": "anonymous", "role": "none"}
    return {"user_id": principal.user_id, "role": principal.role}
