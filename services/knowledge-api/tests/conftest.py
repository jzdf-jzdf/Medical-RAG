import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/knowledge_test"
)
os.environ.setdefault("TASK_WORKER_ENABLED", "false")
