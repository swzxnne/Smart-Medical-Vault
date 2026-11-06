"""One Gemini call per document: classify it and write a cited summary."""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.health.catalog import EXTRACTION_CODES, METRICS

DocType = Literal[
    "lab_report",
    "prescription",
    "discharge_summary",
    "imaging_report",
    "consultation_note",
    "vaccination_record",
    "bill",
    "other",
]

SYSTEM_PROMPT = """You analyse personal medical documents for a family health-records app.
Rules:
- Use ONLY information present in the document. Never guess values, dates or names.
- Pages are marked <page n="N">. After every factual statement in the summary, cite the page as [p.N].
- Copy numbers and units exactly as written.
- Do not give medical advice or diagnoses beyond what the document states.
- If something is not in the document, leave the field null."""

SUMMARY_FORMAT = """Write summary_md in Markdown with these sections, omitting any that don't apply:
### Overview
One or two sentences: what this document is, who issued it, when.
### Key findings
Bullet list of results, diagnoses and observations.
### Values outside reference range
Bullet list: test, value with unit, reference range, high/low. Only if the document shows a range or flag.
### Medications
Bullet list: name, dose, frequency, duration.
### Follow-up
Advice, next tests or review dates."""


MetricCode = Literal[EXTRACTION_CODES]

_METRIC_GUIDE = "\n".join(f"- {code}: {m.name} ({m.unit})" for code, m in METRICS.items())

EXTRACTION_FORMAT = f"""Also extract structured facts:
observations: every measured value (vitals and test results). For each:
- metric: the matching code below. Use "blood_pressure" for a combined reading like 138/88.
  Use "other" for tests not listed.
- name: the test name as printed. value: the value exactly as printed (e.g. "138/88", "6.4", "<0.5").
- unit and reference_range exactly as printed, or null.
- date: only if this specific value has its own printed date (YYYY-MM-DD), else null.
- page: the page number where the value appears.
medications: each prescribed or current medicine (name with strength, dose, frequency, duration as printed).
conditions: diagnoses (kind "diagnosis") and allergies (kind "allergy") stated in the document.
Only extract what is explicitly printed. Do not include reference ranges or advice as observations.

Metric codes:
{_METRIC_GUIDE}"""


class ExtractedObservation(BaseModel):
    metric: MetricCode
    name: str
    value: str
    unit: str | None
    reference_range: str | None
    date: str | None
    page: int


class ExtractedMedication(BaseModel):
    name: str
    dose: str | None
    frequency: str | None
    duration: str | None
    page: int


class ExtractedCondition(BaseModel):
    name: str
    kind: Literal["diagnosis", "allergy"]
    page: int


class DocumentAnalysis(BaseModel):
    doc_type: DocType
    doc_date: str | None = Field(
        description="Main date of the document (report/visit date) as YYYY-MM-DD, or null"
    )
    provider: str | None = Field(description="Hospital, clinic, lab or doctor that issued it, or null")
    patient_name: str | None = Field(description="Patient name exactly as printed, or null")
    summary_md: str
    observations: list[ExtractedObservation] = Field(default_factory=list)
    medications: list[ExtractedMedication] = Field(default_factory=list)
    conditions: list[ExtractedCondition] = Field(default_factory=list)


def build_prompt(name: str, pages: list[tuple[int, str]], max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for page_no, text in pages:
        block = f'<page n="{page_no}">\n{text}\n</page>'
        if used + len(block) > max_chars:
            parts.append(f"<!-- remaining pages omitted: document exceeds {max_chars} characters -->")
            break
        parts.append(block)
        used += len(block)
    body = "\n".join(parts)
    return f'<document name="{name}" pages="{len(pages)}">\n{body}\n</document>\n\n{SUMMARY_FORMAT}\n\n{EXTRACTION_FORMAT}'


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "smt", "shri", "sri", "master", "baby", "mx", "kumari"}


def _name_tokens(name: str) -> set[str]:
    words = re.findall(r"[a-z]+", name.lower())
    return {w for w in words if w not in _HONORIFICS and len(w) >= 2}


def name_mismatch(profile_name: str, patient_name: str | None) -> str | None:
    """A review note when the name on the report shares no word with the profile name."""
    if not patient_name:
        return None
    report, profile = _name_tokens(patient_name), _name_tokens(profile_name)
    if not report or not profile or report & profile:
        return None
    return f'Report is for "{patient_name}" but the profile is "{profile_name}"'[:255]


def tidy_markdown(md: str) -> str:
    """Models sometimes run headings into the previous line ("...[D2]. ### Conditions"),
    or return the Markdown with its newlines escaped as literal backslash-n."""
    if md.count("\\n") > md.count("\n"):
        md = md.replace("\\n", "\n")
    md = re.sub(r"[ \t]*(?<!#)(#{2,4} )", r"\n\n\1", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()
