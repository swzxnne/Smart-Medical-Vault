from celery import Celery

from app.config import get_settings

QUEUE_OCR, QUEUE_EMBED, QUEUE_LLM = "ocr", "embed", "llm"

celery_app = Celery("vault", broker=get_settings().redis_url, include=["app.pipeline.tasks"])
celery_app.conf.update(
    task_routes={
        "pipeline.parse": {"queue": QUEUE_OCR},
        "pipeline.embed": {"queue": QUEUE_EMBED},
        "pipeline.summarize": {"queue": QUEUE_LLM},
    },
    task_default_queue=QUEUE_LLM,
    # Re-deliver a task if the worker dies mid-way; every task is idempotent.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_ignore_result=True,
)
