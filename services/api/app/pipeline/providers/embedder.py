"""Voyage AI embeddings."""

from collections.abc import Iterator
from functools import lru_cache

from app.config import get_settings
from app.db.models import EMBEDDING_DIM
from app.pipeline.providers.errors import PipelineError, TransientError

MAX_BATCH_ITEMS = 128


def estimate_tokens(text: str) -> int:
    return len(text) // 3 + 1  # conservative for English + numbers


def batches(texts: list[str], max_tokens: int, max_items: int = MAX_BATCH_ITEMS) -> Iterator[list[str]]:
    batch: list[str] = []
    tokens = 0
    for text in texts:
        cost = estimate_tokens(text)
        if batch and (tokens + cost > max_tokens or len(batch) >= max_items):
            yield batch
            batch, tokens = [], 0
        batch.append(text)
        tokens += cost
    if batch:
        yield batch


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str, batch_tokens: int):
        import voyageai

        if not api_key:
            raise PipelineError("VOYAGE_API_KEY is not set")
        # Documents: the client retries 429s itself with backoff; Celery retries whatever still fails.
        self._client = voyageai.Client(api_key=api_key, max_retries=4, timeout=60)
        # Queries: someone is waiting, so fail fast and let search fall back to keywords.
        self._query_client = voyageai.Client(api_key=api_key, max_retries=0, timeout=10)
        self.model = model
        self._batch_tokens = batch_tokens

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in batches(texts, self._batch_tokens):
            vectors.extend(self._embed(batch, "document"))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query", self._query_client)[0]

    def _embed(self, texts: list[str], input_type: str, client=None) -> list[list[float]]:
        import voyageai.error as verr

        try:
            result = (client or self._client).embed(
                texts, model=self.model, input_type=input_type, output_dimension=EMBEDDING_DIM
            )
        except (verr.RateLimitError, verr.ServiceUnavailableError, verr.ServerError, verr.APIConnectionError) as exc:
            raise TransientError(f"Voyage: {exc}") from exc
        except verr.VoyageError as exc:
            raise PipelineError(f"Voyage: {exc}") from exc
        return result.embeddings


@lru_cache
def get_embedder() -> VoyageEmbedder:
    s = get_settings()
    return VoyageEmbedder(s.voyage_api_key, s.voyage_model, s.voyage_batch_tokens)
