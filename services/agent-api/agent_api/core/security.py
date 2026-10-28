"""API-key principals and role checks for the multi-user Agent boundary."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from agent_api.core.config import get_settings

ROLE_LEVEL = {"viewer": 10, "reviewer": 20, "editor": 30, "admin": 40}


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str


def principal_from_request(request: Request) -> Principal:
    settings = get_settings()
    if not settings.auth_enabled:
        return Principal(
            user_id=request.headers.get("X-User-ID", "development"), role="admin"
        )
    key = request.headers.get("X-API-Key", "").strip()
    authorization = request.headers.get("Authorization", "")
    if not key and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    record = settings.api_keys_json.get(key)
    if not isinstance(record, dict):
        raise HTTPException(status_code=401, detail="缺少或无效的 API Key")
    role = str(record.get("role") or "viewer")
    if role not in ROLE_LEVEL:
        raise HTTPException(status_code=403, detail="API Key 角色无效")
    return Principal(user_id=str(record.get("user_id") or "api-user"), role=role)


def enforce_user(request: Request, user_id: str, required_role: str = "viewer") -> Principal:
    principal = principal_from_request(request)
    if principal.user_id != user_id and principal.role != "admin":
        raise HTTPException(status_code=403, detail="不能访问其他用户的数据")
    if ROLE_LEVEL[principal.role] < ROLE_LEVEL[required_role]:
        raise HTTPException(status_code=403, detail="当前角色无权执行此操作")
    return principal


async def authorize_request(request: Request, call_next):
    settings = get_settings()
    if not settings.auth_enabled or not request.url.path.startswith("/api/v1"):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    try:
        request.state.principal = principal_from_request(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)
