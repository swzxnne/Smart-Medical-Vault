"""Google Gemini (free tier) with JSON-schema structured output and model fallback."""

import logging
from collections.abc import Iterator
from functools import lru_cache
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.pipeline.providers.errors import PipelineError, TransientError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}

VISION_PROMPT = """Transcribe all text on this scanned medical document page exactly as written.
- Plain text only. Keep the reading order and line breaks.
- Keep each label on the same line as its value (e.g. "Blood Pressure: 142/89 mmHg").
- Write table rows one per line, cells separated by " | ".
- Do not correct, complete, interpret, translate or summarise anything. Copy numbers and units exactly.
- Write [illegible] for anything you cannot read.
- If the page is blank or has no readable text, reply with exactly: NO_TEXT"""


class GeminiLLM:
    def __init__(self, api_key: str, model: str, fallback_models: list[str] | None = None):
        from google import genai

        if not api_key:
            raise PipelineError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)
        self.models = [model, *(m for m in fallback_models or [] if m != model)]

    @property
    def model(self) -> str:
        return self.models[0]

    def generate_json(self, *, system: str, prompt: str, schema: type[T]) -> tuple[T, str]:
        """Returns the parsed result and the model that produced it.

        Free-tier models are often overloaded (503) or rate limited (429); each fallback model
        has its own capacity and quota, so try them in order before asking Celery to retry.
        """
        from google.genai import errors

        failures: list[str] = []
        for index, model in enumerate(self.models):
            try:
                return self._generate(model, system, prompt, schema), model
            except errors.APIError as exc:
                failures.append(f"{model}: {exc.code} {exc.message}")
                if index == 0 and exc.code not in RETRYABLE_CODES:
                    # The configured model itself is wrong (bad key, retired model...).
                    raise PipelineError(f"Gemini {model} {exc.code}: {exc.message}") from exc
                log.warning("Gemini %s failed (%s); trying next model", model, exc.code)
        raise TransientError("All Gemini models unavailable: " + "; ".join(failures))

    def stream_text(self, *, system: str, prompt: str) -> Iterator[tuple[str, str]]:
        """Yields (model, text_delta). Falls back to the next model only if the current one
        fails before producing any text; a failure mid-answer is raised to the caller."""
        from google.genai import errors, types

        config = types.GenerateContentConfig(system_instruction=system, temperature=0.2)
        failures: list[str] = []
        for model in self.models:
            started = False
            try:
                for chunk in self._client.models.generate_content_stream(model=model, contents=prompt, config=config):
                    if chunk.text:
                        started = True
                        yield model, chunk.text
                return
            except errors.APIError as exc:
                if started:
                    raise TransientError(f"Gemini {model} stopped mid-answer: {exc.code}") from exc
                failures.append(f"{model}: {exc.code}")
                log.warning("Gemini %s failed (%s); trying next model", model, exc.code)
        raise TransientError("All Gemini models unavailable (" + ", ".join(failures) + ")")

    def transcribe_image(self, png: bytes) -> tuple[str, str]:
        """Verbatim transcription of a page image. Returns (text, model)."""
        from google.genai import errors, types

        failures: list[str] = []
        for model in self.models:
            try:
                resp = self._client.models.generate_content(
                    model=model,
                    contents=[types.Part.from_bytes(data=png, mime_type="image/png"), VISION_PROMPT],
                    config=types.GenerateContentConfig(temperature=0),
                )
                return (resp.text or "").strip(), model
            except errors.APIError as exc:
                failures.append(f"{model}: {exc.code}")
                if exc.code not in RETRYABLE_CODES and model == self.models[0]:
                    raise PipelineError(f"Gemini vision {model} {exc.code}: {exc.message}") from exc
                log.warning("Gemini vision %s failed (%s); trying next model", model, exc.code)
        raise TransientError("All Gemini models unavailable for vision (" + ", ".join(failures) + ")")

    def _generate(self, model: str, system: str, prompt: str, schema: type[T]) -> T:
        from google.genai import types

        resp = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )
        if isinstance(resp.parsed, schema):
            return resp.parsed
        try:
            return schema.model_validate_json(resp.text or "")
        except ValidationError as exc:
            # Usually a truncated or blocked response; another attempt often succeeds.
            raise TransientError(f"Gemini {model} returned invalid JSON: {exc.errors()[:1]}") from exc


def _fallbacks() -> list[str]:
    return [m.strip() for m in get_settings().gemini_fallback_models.split(",") if m.strip()]


@lru_cache
def get_llm() -> GeminiLLM:
    """Document analysis: quality first."""
    s = get_settings()
    return GeminiLLM(s.gemini_api_key, s.gemini_model, _fallbacks())


@lru_cache
def get_ask_llm() -> GeminiLLM:
    """Interactive answers: latency first, then the pipeline models."""
    s = get_settings()
    return GeminiLLM(s.gemini_api_key, s.gemini_ask_model, [s.gemini_model, *_fallbacks()])
