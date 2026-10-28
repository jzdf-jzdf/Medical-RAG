from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from knowledge_api.api.auth import router as auth_router
from knowledge_api.api.dashboard import router as dashboard_router
from knowledge_api.api.documents import router as documents_router
from knowledge_api.api.knowledge_bases import router as knowledge_bases_router
from knowledge_api.api.processing_tasks import router as processing_tasks_router
from knowledge_api.core.config import get_settings
from knowledge_api.core.security import authorize_request
from knowledge_api.db.session import engine
from knowledge_api.services.mcp_server import mcp_http_app, mcp_server
from medical_platform_core import instrument, metrics_app
from knowledge_api.services.task_worker import ProcessingTaskWorker

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Bind the in-process Worker lifecycle to the FastAPI application.

    Startup prepares local storage and recovers durable jobs before accepting
    work. Shutdown gives the Worker a chance to requeue its current task, then
    closes pooled PostgreSQL connections cleanly.
    """

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    # Tests and API-only deployments may disable execution while preserving all
    # task endpoints and database models.
    async with mcp_server.session_manager.run():
        worker = ProcessingTaskWorker() if settings.task_worker_enabled else None
        if worker:
            await worker.start()
        try:
            yield
        finally:
            if worker:
                await worker.stop()
            await engine.dispose()


app = FastAPI(title="Medical Knowledge API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(instrument("medical-knowledge-api"))
app.mount("/metrics", metrics_app())
app.middleware("http")(authorize_request)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")
app.include_router(knowledge_bases_router, prefix="/api/v1")
app.include_router(processing_tasks_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "medical-knowledge-api"}


@app.get("/ready", tags=["health"])
async def ready() -> dict[str, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    return {"status": "ready"}


# Mount last so normal FastAPI routes retain precedence. The endpoint is
# available at /mcp/ (and Starlette redirects /mcp to that canonical path).
app.mount("/mcp", mcp_http_app)
