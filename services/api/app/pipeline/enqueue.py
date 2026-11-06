"""Starting pipeline work from the API (and from tasks). Tasks are referenced by name, so the
API image doesn't need the worker's heavy dependencies."""

import uuid
from functools import lru_cache

import redis
from celery import chain

from app.config import get_settings
from app.pipeline.celery_app import celery_app

# Bump when extraction/summarization changes so old documents can be re-analysed.
# 3: Gemini vision re-reads low-confidence OCR pages; values carry review flags.
PIPELINE_VERSION = 3

STAGES = ("pipeline.parse", "pipeline.embed", "pipeline.summarize")
PROFILE_SUMMARY_DELAY = 30  # seconds; several documents finishing together cause one refresh


def profile_summary_key(profile_id: uuid.UUID | str) -> str:
    return f"profile-summary:{profile_id}"


class Pipeline:
    def start(self, document_id: uuid.UUID, from_stage: str = "pipeline.parse") -> None:
        stages = STAGES[STAGES.index(from_stage):]
        chain(*(celery_app.signature(name, args=[str(document_id)], immutable=True) for name in stages)).apply_async()

    def refresh_profile(self, profile_id: uuid.UUID, delay: int = PROFILE_SUMMARY_DELAY) -> None:
        """Debounced: each call replaces the token, and only the task holding the latest token runs."""
        token = uuid.uuid4().hex
        redis_client().set(profile_summary_key(profile_id), token, ex=3600)
        celery_app.send_task("pipeline.profile_summary", args=[str(profile_id), token], countdown=delay)

    def summary_pending(self, profile_id: uuid.UUID) -> bool:
        """A refresh is scheduled or running (the task clears the key when it finishes)."""
        return bool(redis_client().exists(profile_summary_key(profile_id)))


@lru_cache
def redis_client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


@lru_cache
def get_pipeline() -> Pipeline:
    return Pipeline()
