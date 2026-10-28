FROM python:3.12-slim

WORKDIR /app
ENV PIP_DEFAULT_TIMEOUT=300
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*
COPY packages/python/medical-platform-core /opt/medical-platform-core
RUN pip install --no-cache-dir /opt/medical-platform-core
COPY services/knowledge-api/pyproject.toml ./
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    "torch>=2.5,<3"
RUN mkdir -p knowledge_api \
    && touch knowledge_api/__init__.py \
    && pip install --no-cache-dir . \
    && mineru-kit --version \
    && rm -rf knowledge_api
COPY services/knowledge-api/knowledge_api ./knowledge_api
COPY services/knowledge-api/alembic.ini ./
COPY services/knowledge-api/migrations ./migrations

EXPOSE 8001
CMD ["sh", "-c", "alembic upgrade head && uvicorn knowledge_api.main:app --host 0.0.0.0 --port 8001"]

