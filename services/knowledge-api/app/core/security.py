"""Optional API-key authentication and small role hierarchy."""

import json
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings

ROLE_LEVEL = {"viewer": 10, "reviewer": 20, "editor": 30, "admin": 40}


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str


def _configured_keys() -> dict[str, Principal]:
    settings = get_settings()
    try:
        raw = json.loads(settings.api_keys_json or "{}")
    except json.JSONDecodeError:
        return {}
    result: dict[str, Principal] = {}
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        role = str(value.get("role", "viewer"))
        if role not in ROLE_LEVEL:
            continue
        result[key] = Principal(
            user_id=str(value.get("user_id") or value.get("name") or "api-user"),
            role=role,
        )
    return result


def principal_from_request(request: Request) -> Principal | None:
    settings = get_settings()
    if not settings.auth_enabled:
        return Principal(user_id="development", role="admin")
    key = request.headers.get("X-API-Key", "").strip()
    authorization = request.headers.get("Authorization", "")
    if not key and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    return _configured_keys().get(key)


async def authorize_request(request: Request, call_next):
    """Protect API and MCP paths while leaving health and docs public."""

    settings = get_settings()
    protected = request.url.path.startswith(("/api/v1", "/mcp"))
    if not settings.auth_enabled or not protected or request.method == "OPTIONS":
        return await call_next(request)
    principal = principal_from_request(request)
    if principal is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "缺少或无效的 API Key"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    required = "viewer" if request.method in {"GET", "HEAD"} else "editor"
    if "/evidence-spans/" in request.url.path and request.url.path.endswith("/review"):
        required = "reviewer"
    if ROLE_LEVEL[principal.role] < ROLE_LEVEL[required]:
        return JSONResponse(status_code=403, content={"detail": "当前角色无权执行此操作"})
    request.state.principal = principal
    return await call_next(request)

