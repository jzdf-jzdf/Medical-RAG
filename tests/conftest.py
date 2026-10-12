import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/agent_test")
os.environ.setdefault("MODEL_PROVIDER", "test")
os.environ.setdefault("MODEL_NAME", "test-model")
os.environ.setdefault("MODEL_BASE_URL", "http://localhost:9999")
