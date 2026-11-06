import logging
from collections import OrderedDict
from threading import Lock

from app.pipeline.providers.errors import PipelineError, TransientError

log = logging.getLogger(__name__)


class QueryEmbedder:
    """Embeds search queries, caching recent ones: the free Voyage tier allows only a few
    requests per minute, and people often repeat or refine the same search."""

    def __init__(self, embedder, max_size: int = 512):
        self._embedder = embedder
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = Lock()
        self._max_size = max_size

    def embed(self, query: str) -> list[float] | None:
        key = " ".join(query.lower().split())
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        try:
            vector = self._embedder.embed_query(query)
        except (TransientError, PipelineError) as exc:
            log.warning("Query embedding failed, falling back to keyword search: %s", exc)
            return None
        with self._lock:
            self._cache[key] = vector
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
        return vector
