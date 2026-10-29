import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/evaluation_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("KNOWLEDGE_API_URL", "http://localhost:8001")
os.environ.setdefault("AGENT_API_URL", "http://localhost:8002")
os.environ.setdefault("EVALUATION_DISPATCHER_ENABLED", "false")
