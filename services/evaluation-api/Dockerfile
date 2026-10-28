FROM python:3.12-slim
WORKDIR /app
COPY packages/python/medical-platform-core /opt/medical-platform-core
RUN pip install --no-cache-dir /opt/medical-platform-core
COPY services/evaluation-api/pyproject.toml ./
RUN mkdir -p evaluation_api \
    && touch evaluation_api/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf evaluation_api
COPY services/evaluation-api/evaluation_api ./evaluation_api
COPY services/evaluation-api/alembic.ini ./
COPY services/evaluation-api/migrations ./migrations
EXPOSE 8003
CMD ["sh", "-c", "alembic upgrade head && uvicorn evaluation_api.main:app --host 0.0.0.0 --port 8003"]

