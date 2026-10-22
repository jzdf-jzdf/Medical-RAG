from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.governance import router as governance_router
from app.api.meta import router as meta_router
from app.api.models import router as models_router
from app.api.runs import router as runs_router
from app.core.config import get_settings
from app.core.security import authorize_request
from app.db.session import engine
from app.services.observability import instrument, metrics_app

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    yield
    await engine.dispose()


app = FastAPI(title="Medical Agent API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(instrument("medical-agent-api"))
app.mount("/metrics", metrics_app())
app.middleware("http")(authorize_request)
app.include_router(conversations_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(meta_router, prefix="/api/v1")
app.include_router(models_router, prefix="/api/v1")
app.include_router(governance_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "medical-agent-api"}


@app.get("/ready", tags=["health"])
async def ready() -> dict:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    return {
        "status": "ready",
        "mcp_servers": [item.name for item in settings.mcp_servers_json if item.enabled],
    }
