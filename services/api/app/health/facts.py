"""Store a document's extracted facts, keeping only values that can be found in its text."""

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Condition, Document, DocumentPage, Medication, Observation
from app.health.normalize import label_near_value, label_words, normalize, valid_date, value_in_text
from app.pipeline.analysis import DocumentAnalysis, parse_date

log = logging.getLogger(__name__)


def ground_page(needle_check, page_hint: int, pages: dict[int, str]) -> int | None:
    """The page where the fact really appears: the LLM's page if it checks out, else the first that does."""
    order = [page_hint] + [p for p in sorted(pages) if p != page_hint]
    for page_no in order:
        if page_no in pages and needle_check(pages[page_no]):
            return page_no
    return None


def _words_present(name: str, text: str) -> bool:
    """Every significant word of a medicine/diagnosis name appears on the page (case-insensitive)."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3]
    lowered = text.lower()
    return bool(words) and all(w in lowered for w in words)


@dataclass
class PreparedFacts:
    """Grounded, normalized facts ready to store (or to score, in the eval harness)."""

    observations: list[dict] = field(default_factory=list)  # Observation column values
    medications: list[dict] = field(default_factory=list)
    conditions: list[dict] = field(default_factory=list)
    dropped: int = 0  # not found in the text
    # Found in the text but rejected by normalization (unknown unit, implausible, unparseable).
    rejected: list[dict] = field(default_factory=list)


NOTE_LABEL = "Test name not found next to this value on the page"
NOTE_VISION = "Read by AI from a poor-quality scan"


def prepare_facts(
    result: DocumentAnalysis,
    pages: dict[int, str],
    doc_date: date | None,
    protected: frozenset = frozenset(),
    vision_pages: frozenset = frozenset(),
) -> PreparedFacts:
    """Keep only facts found in the document's text, normalized. `protected` holds
    (code, observed_at) pairs the user has corrected or removed, which AI values must not replace.
    Values that need a human look (label not beside them, or from a page in `vision_pages`) are
    kept but marked unverified."""
    out = PreparedFacts()
    seen: set[tuple] = set()
    for item in result.observations:
        page_no = ground_page(lambda text: value_in_text(item.value, text), item.page, pages)
        if page_no is None:
            out.dropped += 1
            continue
        observed_at = valid_date(parse_date(item.date), doc_date)
        normalized = normalize(item.metric, item.name, item.value, item.unit, item.reference_range)
        if not normalized:
            out.rejected.append(dict(code=item.metric, value=item.value, unit=item.unit, page=page_no))
        for n in normalized:
            # Page is part of the key: the same value on another page is a separate reading
            # (e.g. admission vs discharge BP), while a value repeated on one page is a duplicate.
            key = (n.code, observed_at, n.value_num, n.name.lower(), page_no)
            user_owned = (n.code, observed_at) in protected and n.code != "other"
            if user_owned or key in seen:
                continue
            seen.add(key)
            note = None
            if page_no in vision_pages:
                note = NOTE_VISION
            elif not label_near_value(item.value, label_words(item.name, item.metric), pages[page_no]):
                note = NOTE_LABEL
            out.observations.append(dict(
                verified=note is None,
                review_note=note,
                page_no=page_no,
                code=n.code,
                name=n.name[:160],
                value_num=n.value_num,
                value_text=n.value_text[:80],
                unit=(n.unit or None) and n.unit[:40],
                ref_low=n.ref_low,
                ref_high=n.ref_high,
                ref_text=(n.ref_text or None) and n.ref_text[:80],
                flag=n.flag,
                observed_at=observed_at,
            ))

    names: set[str] = set()
    for med in result.medications:
        page_no = ground_page(lambda text: _words_present(med.name, text), med.page, pages)
        if page_no is None or med.name.lower() in names:
            out.dropped += page_no is None
            continue
        names.add(med.name.lower())
        out.medications.append(dict(
            page_no=page_no, name=med.name[:160], dose=(med.dose or None) and med.dose[:120],
            frequency=(med.frequency or None) and med.frequency[:120],
            duration=(med.duration or None) and med.duration[:80], prescribed_on=doc_date,
        ))

    names = set()
    for cond in result.conditions:
        page_no = ground_page(lambda text: _words_present(cond.name, text), cond.page, pages)
        if page_no is None or cond.name.lower() in names:
            out.dropped += page_no is None
            continue
        names.add(cond.name.lower())
        out.conditions.append(dict(page_no=page_no, name=cond.name[:200], kind=cond.kind, noted_on=doc_date))
    return out


def save_document_facts(db: Session, doc: Document, result: DocumentAnalysis, pages: dict[int, str]) -> dict[str, int]:
    """Replace this document's AI-extracted facts. User corrections and removals survive re-runs."""
    kept = db.scalars(
        select(Observation).where(
            Observation.document_id == doc.id, (Observation.source != "ai") | Observation.hidden
        )
    ).all()
    vision_pages = frozenset(db.scalars(
        select(DocumentPage.page_no).where(DocumentPage.document_id == doc.id, DocumentPage.engine == "gemini-vision")
    ))
    facts = prepare_facts(result, pages, doc.doc_date, frozenset((o.code, o.observed_at) for o in kept), vision_pages)

    owner = dict(user_id=doc.user_id, profile_id=doc.profile_id, document_id=doc.id)
    db.execute(
        delete(Observation).where(
            Observation.document_id == doc.id, Observation.source == "ai", Observation.hidden.is_(False)
        )
    )
    db.add_all(Observation(**owner, **o, source="ai") for o in facts.observations)
    db.execute(delete(Medication).where(Medication.document_id == doc.id))
    db.add_all(Medication(**owner, **m) for m in facts.medications)
    db.execute(delete(Condition).where(Condition.document_id == doc.id))
    db.add_all(Condition(**owner, **c) for c in facts.conditions)

    if facts.dropped:
        log.info("Document %s: dropped %d extracted facts not found in its text", doc.id, facts.dropped)
    for r in facts.rejected:
        log.warning("Document %s: rejected %s %s %s (unknown unit or implausible value)", doc.id, r["code"], r["value"], r["unit"])
    return {"observations": len(facts.observations), "dropped": facts.dropped}
