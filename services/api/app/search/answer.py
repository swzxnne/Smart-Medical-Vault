"""Grounded answers: numbered sources in, [S#]-cited text out."""

import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from app.search.retrieval import Hit

SYSTEM_PROMPT = """You answer questions about a family's personal medical records.
You are given numbered sources [S1], [S2], ... Each is an excerpt from one page of an uploaded
document, labelled with the person it belongs to, the document and its date.

Rules:
- Use ONLY the sources. If they don't contain the answer, say you couldn't find it in the
  uploaded documents, and stop. Never guess or use outside knowledge for facts about the person.
- Cite every fact with its source number in square brackets right after it, e.g. "BP was 146/92 mmHg [S2]".
  Cite several like [S1][S3]. Only cite numbers that exist.
- When giving a value, say whose record it is and the date, and copy the number and unit exactly.
- If sources cover several dates, list them chronologically.
- If the question names a person, only use that person's sources.
- Sources marked "measurements extracted" list values already read from that page; prefer them for
  numbers and include every matching date, but cite them like any other source.
- A value marked "unverified" hasn't been checked by the user yet (hard-to-read scan): still give it,
  but say it's unverified.
- Be concise: a direct answer first, then short bullet points if useful.
- You may briefly explain what a medical term means, but do not diagnose or give treatment advice;
  suggest discussing concerns with their doctor."""

NOT_FOUND = "I couldn't find anything about that in the uploaded documents."

MAX_HISTORY_TURNS = 3
HISTORY_ANSWER_CHARS = 600

REWRITE_SYSTEM = """You turn the latest message in a conversation about a family's medical records into
one standalone search question. Resolve references like "it", "her", "that test", "and in 2023?" or
"what about Ravi?" using the conversation. Keep every name, test and date. Do not answer the question.
If the message is already standalone, return it unchanged."""


class StandaloneQuestion(BaseModel):
    question: str


def format_history(history) -> str:
    turns = history[-MAX_HISTORY_TURNS:]
    return "\n\n".join(f"Q: {t.question}\nA: {t.answer[:HISTORY_ANSWER_CHARS]}" for t in turns)


def rewrite_prompt(history, question: str) -> str:
    return f"<conversation>\n{format_history(history)}\n</conversation>\n\nLatest message: {question}"


def fallback_standalone(history, question: str) -> str:
    """No AI available: searching with the previous question as context still helps short follow-ups."""
    return f"{history[-1].question} {question}" if history else question

_CITATION = re.compile(r"\[S(\d+)\]")


def format_source(number: int, hit: Hit) -> str:
    label = " · ".join(
        part
        for part in (
            hit.profile_name,
            hit.document_name,
            (hit.doc_type or "").replace("_", " ") or None,
            hit.doc_date.isoformat() if hit.doc_date else "date unknown",
            f"page {hit.page_no}",
        )
        if part
    )
    return f"[S{number}] {label}\n{hit.text}"


@dataclass
class MeasurementSource:
    """Extracted measurements from one document page, offered to the LLM as a source."""

    profile_name: str
    document_id: object
    document_name: str
    doc_type: str | None
    doc_date: date | None
    page_no: int | None
    lines: list[str]


def format_measurement_source(number: int, src: MeasurementSource) -> str:
    label = " · ".join(
        part
        for part in (
            src.profile_name,
            src.document_name,
            (src.doc_type or "").replace("_", " ") or None,
            src.doc_date.isoformat() if src.doc_date else None,
            f"page {src.page_no}" if src.page_no else None,
        )
        if part
    )
    body = "\n".join(f"- {line}" for line in src.lines)
    return f"[S{number}] {label} (measurements extracted from this page)\n{body}"


def build_prompt(
    question: str,
    hits: list[Hit],
    measurements: list[MeasurementSource] | None = None,
    history=None,
    standalone: str | None = None,
) -> str:
    """Measurement sources are numbered first, then text passages."""
    measurements = measurements or []
    blocks = [format_measurement_source(i, m) for i, m in enumerate(measurements, start=1)]
    blocks += [format_source(i, hit) for i, hit in enumerate(hits, start=len(measurements) + 1)]
    sources = "\n\n---\n\n".join(blocks)
    prompt = f"<sources>\n{sources}\n</sources>\n\n"
    if history:
        prompt += f"<earlier_conversation>\n{format_history(history)}\n</earlier_conversation>\n\n"
    prompt += f"Question: {question}"
    if standalone and standalone.strip().lower() != question.strip().lower():
        prompt += f"\n(Meaning, in context: {standalone})"
    return prompt


def cited_numbers(answer: str, source_count: int) -> list[int]:
    """Source numbers the answer actually cites, in first-use order, ignoring invented ones."""
    seen: list[int] = []
    for match in _CITATION.finditer(answer):
        n = int(match.group(1))
        if 1 <= n <= source_count and n not in seen:
            seen.append(n)
    return seen
