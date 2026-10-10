from celery import Celery
from src.config import settings

celery_app = Celery(
    "ticket_triage_worker",
    broker=settings.broker_url,
    backend=settings.result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=True,
    worker_pool="solo",  # Windows-friendly execution pool
)
