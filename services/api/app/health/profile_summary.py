"""Profile-level health overview, written from structured facts + document summaries (not raw text)."""

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

SYSTEM_PROMPT = """You write a concise health overview of one person for their family's records app.
You get their documents (numbered [D1], [D2], ...), measurements, medications and conditions,
all extracted from those documents.
Rules:
- Use ONLY the given information. Never invent values, dates or diagnoses.
- Cite the source document after every fact as [D#]. Only use numbers that exist.
- Copy numbers and units exactly. Mention dates so the reader knows how current each fact is.
- Point out trends when a measurement has several dates (improving, worsening, stable), without judging.
- Measurements marked "(unverified)" were read from a hard-to-read scan or found away from their test
  name and haven't been checked by the user yet: say "unverified" when you mention one.
- No medical advice or new diagnoses. Neutral, plain language."""

FORMAT = """Write summary_md in Markdown with these sections, omitting empty ones:
### At a glance
Two or three sentences: main known conditions and the most notable recent findings.
### Conditions & allergies
### Current medications
Most recent prescription first; note the date prescribed.
### Key measurements
Bullet per measurement: latest value with date, previous values if any, trend, and whether it was flagged.
### Follow-ups mentioned
Pending tests or reviews that documents asked for."""


class ProfileSummary(BaseModel):
    summary_md: str


@dataclass
class DocLine:
    number: int
    name: str
    doc_type: str | None
    doc_date: date | None
    summary: str


def build_prompt(
    person: str,
    docs: list[DocLine],
    observations: list[tuple[str, str, str | None, date | None, str | None, int]],
    medications: list[tuple[str, int, date | None]],
    conditions: list[tuple[str, str, int]],
    max_summary_chars: int = 1200,
) -> str:
    """observations: (name, value with unit, flag, date, reference, doc number);
    medications: (description, doc number, date); conditions: (name, kind, doc number)."""
    lines = [f"<person>{person}</person>", "", "<documents>"]
    for d in docs:
        when = d.doc_date.isoformat() if d.doc_date else "date unknown"
        kind = (d.doc_type or "document").replace("_", " ")
        lines.append(f"[D{d.number}] {d.name} · {kind} · {when}\n{d.summary[:max_summary_chars]}\n")
    lines.append("</documents>\n\n<measurements>")
    for name, value, flag, when, reference, number in observations:
        extra = "".join(
            [f" (ref {reference})" if reference else "", f" [{flag}]" if flag and flag != "normal" else ""]
        )
        lines.append(f"- {name}: {value}{extra} on {when.isoformat() if when else 'unknown date'} [D{number}]")
    lines.append("</measurements>\n\n<medications>")
    for description, number, when in medications:
        lines.append(f"- {description} (prescribed {when.isoformat() if when else 'date unknown'}) [D{number}]")
    lines.append("</medications>\n\n<conditions>")
    for name, kind, number in conditions:
        lines.append(f"- {name} ({kind}) [D{number}]")
    lines.append("</conditions>\n\n" + FORMAT)
    return "\n".join(lines)
