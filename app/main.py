from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.evaluation import router as evaluation_router
from app.api.workbench import router as workbench_router
from app.core.config import get_settings
from app.db import engine
from app.services.observability import instrument, metrics_app

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Medical Evaluation API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(instrument("medical-evaluation-api"))
app.mount("/metrics", metrics_app())
app.include_router(workbench_router, prefix="/api/v1")
app.include_router(evaluation_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "medical-evaluation-api"}


@app.get("/ready", tags=["health"])
async def ready() -> dict:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    return {
        "status": "ready",
        "dependencies": {
            "knowledge": str(settings.knowledge_api_url),
            "agent": str(settings.agent_api_url),
        },
    }
