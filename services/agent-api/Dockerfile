FROM python:3.12-slim
WORKDIR /app
COPY packages/python/medical-platform-core /opt/medical-platform-core
RUN pip install --no-cache-dir /opt/medical-platform-core
COPY services/agent-api/pyproject.toml ./
RUN mkdir -p agent_api \
    && touch agent_api/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf agent_api
COPY services/agent-api/agent_api ./agent_api
COPY services/agent-api/alembic.ini ./
COPY services/agent-api/migrations ./migrations
EXPOSE 8002
CMD ["sh", "-c", "alembic upgrade head && uvicorn agent_api.main:app --host 0.0.0.0 --port 8002"]

