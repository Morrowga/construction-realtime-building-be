# app/workers/celery_app.py
from celery import Celery

from app.config import settings

celery_app = Celery(
    "construction_backend",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=600,  # hard limit: 10 minutes per task
    task_soft_time_limit=540,
)
