FROM python:3.12-slim

WORKDIR /app
ENV PIP_DEFAULT_TIMEOUT=300
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    "torch>=2.5,<3"
RUN mkdir -p app \
    && touch app/__init__.py \
    && pip install --no-cache-dir . \
    && mineru-kit --version \
    && rm -rf app
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations

EXPOSE 8001
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8001"]

