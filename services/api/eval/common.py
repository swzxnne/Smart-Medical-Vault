"""Shared helpers: golden-set loading, response caches, rate-limit retries, report output."""

import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar

from app.pipeline.providers.errors import TransientError

ROOT = Path(__file__).resolve().parent
DOCS, EXPECTED, RESULTS, CACHE = ROOT / "docs", ROOT / "expected", ROOT / "results", ROOT / ".cache"
log = logging.getLogger("eval")
T = TypeVar("T")


def load_cases(only: str | None = None) -> list[dict]:
    cases = [json.loads(p.read_text()) for p in sorted(EXPECTED.glob("*.json"))]
    if not cases:
        raise SystemExit("No golden set found: run `python -m eval.generate` first.")
    return [c for c in cases if not only or only in c["file"] or only == c["category"]]


def with_retries(fn: Callable[[], T], what: str, attempts: int = 6, wait: float = 30) -> T:
    """Free tiers rate-limit hard (Voyage ~3 req/min, Gemini 503/429 bursts): wait and retry."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except TransientError as exc:
            if attempt == attempts:
                raise
            log.warning("%s: %s; retry %d/%d in %ds", what, str(exc)[:120], attempt, attempts - 1, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


class CachingLLM:
    """Caches Gemini responses by (models, prompt, schema): re-scoring after a scoring or
    normalization change costs no quota. A prompt or model change misses the cache, as it should."""

    def __init__(self, llm, enabled: bool = True):
        self._llm, self.enabled = llm, enabled
        self.model = llm.model
        self.calls = self.hits = 0
        (CACHE / "llm").mkdir(parents=True, exist_ok=True)

    def generate_json(self, *, system: str, prompt: str, schema):
        path = CACHE / "llm" / f"{_key(self._llm.models, system, prompt, schema.model_json_schema())}.json"
        if self.enabled and path.exists():
            self.hits += 1
            data = json.loads(path.read_text())
            return schema.model_validate(data["result"]), data["model"]
        self.calls += 1
        result, model = with_retries(lambda: self._llm.generate_json(system=system, prompt=prompt, schema=schema), "gemini")
        path.write_text(json.dumps({"result": result.model_dump(mode="json"), "model": model}))
        return result, model

    def transcribe_image(self, png: bytes) -> tuple[str, str]:
        path = CACHE / "llm" / f"{_key(self._llm.models, 'vision', hashlib.sha256(png).hexdigest())}.json"
        if self.enabled and path.exists():
            self.hits += 1
            data = json.loads(path.read_text())
            return data["text"], data["model"]
        self.calls += 1
        text, model = with_retries(lambda: self._llm.transcribe_image(png), "gemini-vision")
        path.write_text(json.dumps({"text": text, "model": model}))
        return text, model

    def stream_text(self, *, system: str, prompt: str) -> Iterator[tuple[str, str]]:
        path = CACHE / "llm" / f"{_key(self._llm.models, system, prompt, 'stream')}.json"
        if self.enabled and path.exists():
            self.hits += 1
            data = json.loads(path.read_text())
            yield data["model"], data["text"]
            return
        self.calls += 1

        def run():
            parts, model = [], None
            for model, delta in self._llm.stream_text(system=system, prompt=prompt):
                parts.append(delta)
            return model, "".join(parts)

        model, text = with_retries(run, "gemini-stream")
        path.write_text(json.dumps({"model": model, "text": text}))
        yield model, text


class CachingEmbedder:
    """Caches Voyage vectors per text. Unlike the app's query path, it waits out rate limits
    instead of falling back to keyword search, so eval results don't depend on quota timing."""

    def __init__(self, embedder, enabled: bool = True):
        self._embedder, self.enabled = embedder, enabled
        self.model = embedder.model
        self.calls = self.hits = 0
        (CACHE / "embed").mkdir(parents=True, exist_ok=True)

    def _cached(self, texts: list[str], input_type: str, fetch) -> list[list[float]]:
        paths = [CACHE / "embed" / f"{_key(self.model, input_type, t)}.json" for t in texts]
        missing = [i for i, p in enumerate(paths) if not (self.enabled and p.exists())]
        self.hits += len(texts) - len(missing)
        if missing:
            self.calls += 1
            vectors = with_retries(lambda: fetch([texts[i] for i in missing]), "voyage", attempts=8, wait=25)
            for i, vector in zip(missing, vectors, strict=True):
                paths[i].write_text(json.dumps(vector))
        return [json.loads(p.read_text()) for p in paths]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._cached(texts, "document", self._embedder.embed_documents)

    def embed_query(self, text: str) -> list[float]:
        return self._cached([text], "query", lambda ts: [self._embedder.embed_query(ts[0])])[0]


def pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.0f}%"


def ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def write_results(name: str, payload: dict, save_baseline: bool) -> dict | None:
    """Write results/<name>-latest.json; optionally promote to baseline. Returns the previous baseline."""
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{name}-latest.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")
    baseline_path = RESULTS / f"{name}-baseline.json"
    previous = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    if save_baseline:
        baseline_path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    return previous


def delta(current: float | None, baseline: float | None) -> str:
    if current is None or baseline is None:
        return ""
    d = 100 * (current - baseline)
    return "" if abs(d) < 0.5 else f" ({'+' if d > 0 else ''}{d:.0f})"
