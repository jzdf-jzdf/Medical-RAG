"""Celery application for durable evaluation work."""

from celery import Celery

from evaluation_api.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "medical-evaluation",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["evaluation_api.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 21600},
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
