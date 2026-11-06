"""Document processing stages, chained per document: parse -> embed -> summarize.

Each stage reads its input from the database, replaces its own output, and is safe to re-run.
"""

import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from celery import Task
from sqlalchemy import delete, select

from app.config import get_settings
from app.db import base
from app.db.models import (
    Chunk,
    Condition,
    Document,
    DocumentPage,
    Medication,
    Observation,
    Profile,
    Summary,
)
from app.health import profile_summary
from app.health.facts import save_document_facts
from app.pipeline import analysis
from app.pipeline.celery_app import celery_app
from app.pipeline.chunking import chunk_pages
from app.pipeline.enqueue import PIPELINE_VERSION, get_pipeline, profile_summary_key, redis_client
from app.pipeline.providers.errors import PipelineError, TransientError
from app.storage.s3 import get_storage

log = logging.getLogger(__name__)

IN_PROGRESS = ("queued", "parsing", "indexing", "summarizing")


def get_parser():
    from app.pipeline.providers import parser

    return parser.parse_file


def get_embedder():
    from app.pipeline.providers.embedder import get_embedder as factory

    return factory()


def get_llm():
    from app.pipeline.providers.llm import get_llm as factory

    return factory()


class PipelineTask(Task):
    autoretry_for = (TransientError,)
    retry_backoff = 30
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 6

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        stage = self.name.rsplit(".", 1)[-1]
        message = str(exc) if isinstance(exc, (PipelineError, TransientError)) else f"{type(exc).__name__}: {exc}"
        mark_failed(args[0], f"{stage}: {message}")


def mark_failed(document_id: str, message: str) -> None:
    log.error("Document %s failed: %s", document_id, message)
    with base.SessionLocal() as db:
        doc = db.get(Document, uuid.UUID(document_id))
        if doc is not None:
            doc.status = "failed"
            doc.error = message[:2000]
            # Failed under the current pipeline: re-analysing wouldn't change anything.
            doc.pipeline_version = PIPELINE_VERSION
            db.commit()
            profile_id = doc.profile_id
        else:
            return
    # A summary refresh may have been skipped while this document was busy; schedule one now.
    try:
        get_pipeline().refresh_profile(profile_id)
    except Exception:
        log.exception("Could not schedule profile summary refresh for %s", profile_id)


def _set_status(document_id: str, status: str) -> Document | None:
    """Move to `status`; None means the document was deleted and the stage should stop."""
    with base.SessionLocal() as db:
        doc = db.get(Document, uuid.UUID(document_id))
        if doc is None:
            return None
        doc.status = status
        doc.error = None
        db.commit()
        return doc


def _load_pages(db, document_id: uuid.UUID) -> list[tuple[int, str]]:
    rows = db.execute(
        select(DocumentPage.page_no, DocumentPage.text)
        .where(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_no)
    ).all()
    return [(page_no, text) for page_no, text in rows]


@celery_app.task(name="pipeline.parse", base=PipelineTask)
def parse_document(document_id: str) -> None:
    doc = _set_status(document_id, "parsing")
    if doc is None:
        return

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"source{Path(doc.storage_key).suffix.lower()}"
        get_storage().download(doc.storage_key, path)
        pages = get_parser()(path, doc.mime)

    if not any(page.text.strip() for page in pages):
        raise PipelineError("No readable text found. Is the scan blank or too blurry?")

    with base.SessionLocal() as db:
        db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id))
        db.add_all(
            DocumentPage(
                document_id=doc.id,
                page_no=page.page_no,
                text=page.text,
                engine=page.engine,
                ocr_confidence=page.ocr_confidence,
            )
            for page in pages
        )
        db.get(Document, doc.id).page_count = len(pages)
        db.commit()


@celery_app.task(name="pipeline.embed", base=PipelineTask)
def embed_document(document_id: str) -> None:
    doc = _set_status(document_id, "indexing")
    if doc is None:
        return

    with base.SessionLocal() as db:
        chunks = chunk_pages(_load_pages(db, doc.id))
    # A short header gives each chunk's embedding its document context.
    inputs = [f"Document: {doc.original_name}\nPage {c.page_no}\n\n{c.text}" for c in chunks]
    vectors = get_embedder().embed_documents(inputs)

    with base.SessionLocal() as db:
        db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        db.add_all(
            Chunk(
                document_id=doc.id,
                profile_id=doc.profile_id,
                user_id=doc.user_id,
                page_no=c.page_no,
                chunk_index=c.chunk_index,
                text=c.text,
                embedding=vector,
            )
            for c, vector in zip(chunks, vectors, strict=True)
        )
        db.commit()


@celery_app.task(name="pipeline.summarize", base=PipelineTask)
def summarize_document(document_id: str) -> None:
    doc = _set_status(document_id, "summarizing")
    if doc is None:
        return

    with base.SessionLocal() as db:
        pages = _load_pages(db, doc.id)
        profile_name = db.scalar(select(Profile.name).where(Profile.id == doc.profile_id)) or ""

    llm = get_llm()
    prompt = analysis.build_prompt(doc.original_name, pages, get_settings().llm_max_input_chars)
    result, model_used = llm.generate_json(
        system=analysis.SYSTEM_PROMPT, prompt=prompt, schema=analysis.DocumentAnalysis
    )

    with base.SessionLocal() as db:
        row = db.get(Document, doc.id)
        if row is None:
            return
        row.doc_type = result.doc_type
        row.doc_date = analysis.parse_date(result.doc_date)
        save_document_facts(db, row, result, dict(pages))
        row.provider = (result.provider or None) and result.provider[:255]
        row.patient_name = (result.patient_name or None) and result.patient_name[:255]
        row.review_reason = analysis.name_mismatch(profile_name, result.patient_name)

        summary = db.scalar(select(Summary).where(Summary.document_id == doc.id))
        if summary is None:
            summary = Summary(document_id=doc.id, content_md="", model="")
            db.add(summary)
        summary.content_md = analysis.tidy_markdown(result.summary_md)
        summary.model = model_used

        row.status = "ready"
        row.error = None
        row.pipeline_version = PIPELINE_VERSION
        row.processed_at = datetime.now(timezone.utc)
        db.commit()

    try:
        get_pipeline().refresh_profile(doc.profile_id)
    except Exception:
        log.exception("Could not schedule profile summary refresh for %s", doc.profile_id)


class ProfileTask(Task):
    autoretry_for = (TransientError,)
    retry_backoff = 60
    retry_backoff_max = 900
    retry_jitter = True
    max_retries = 5

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # Retries exhausted: stop showing "updating summary" for this profile.
        _clear_pending(args[0], args[1] if len(args) > 1 else None)


def _clear_pending(profile_id: str, token: str | None) -> None:
    key = profile_summary_key(profile_id)
    if token is not None and redis_client().get(key) == token:
        redis_client().delete(key)


@celery_app.task(name="pipeline.profile_summary", base=ProfileTask)
def summarize_profile(profile_id: str, token: str | None = None) -> None:
    if token is not None and redis_client().get(profile_summary_key(profile_id)) != token:
        return  # superseded by a newer refresh request
    try:
        _summarize_profile(profile_id)
    except TransientError:
        raise  # Celery retries with the same token, so keep it
    except Exception:
        _clear_pending(profile_id, token)
        raise
    # Busy-document skips leave the key: the busy document's completion (or failure) refreshes again.
    if not _profile_busy(profile_id):
        _clear_pending(profile_id, token)


def _profile_busy(profile_id: str) -> bool:
    with base.SessionLocal() as db:
        return db.scalar(
            select(Document.id).where(Document.profile_id == uuid.UUID(profile_id), Document.status.in_(IN_PROGRESS))
        ) is not None


def _summarize_profile(profile_id: str) -> None:
    pid = uuid.UUID(profile_id)
    with base.SessionLocal() as db:
        profile = db.get(Profile, pid)
        if profile is None:
            return
        busy = db.scalar(select(Document.id).where(Document.profile_id == pid, Document.status.in_(IN_PROGRESS)))
        if busy is not None:
            return  # the document still processing will request another refresh when done
        docs = db.scalars(
            select(Document)
            .where(Document.profile_id == pid, Document.status == "ready")
            .order_by(Document.doc_date.desc().nulls_last(), Document.created_at.desc())
        ).all()
        existing = db.scalar(select(Summary).where(Summary.profile_id == pid))
        if not docs:
            if existing is not None:
                db.delete(existing)
                db.commit()
            return
        prompt, citations = _profile_prompt(db, profile, docs)
        person = profile.name

    llm = get_llm()
    result, model_used = llm.generate_json(
        system=profile_summary.SYSTEM_PROMPT, prompt=prompt, schema=profile_summary.ProfileSummary
    )
    with base.SessionLocal() as db:
        if db.get(Profile, pid) is None:
            return
        summary = db.scalar(select(Summary).where(Summary.profile_id == pid))
        if summary is None:
            summary = Summary(profile_id=pid, content_md="", model="")
            db.add(summary)
        summary.content_md = analysis.tidy_markdown(result.summary_md)
        summary.citations = citations
        summary.model = model_used
        db.commit()
    log.info("Profile summary for %s refreshed with %s", person, model_used)


def _profile_prompt(db, profile: Profile, docs: list[Document]) -> tuple[str, list[dict]]:
    numbers = {d.id: i for i, d in enumerate(docs, start=1)}
    summaries = dict(
        db.execute(select(Summary.document_id, Summary.content_md).where(Summary.document_id.in_(numbers))).all()
    )
    doc_lines = [
        profile_summary.DocLine(numbers[d.id], d.original_name, d.doc_type, d.doc_date, summaries.get(d.id, ""))
        for d in docs
    ]
    observations = [
        (o.name + ("" if o.verified else " (unverified)"),
         f"{o.value_text} {o.unit or ''}".strip() if o.code == "other" else f"{o.value_num:g} {o.unit or ''}".strip(),
         o.flag, o.observed_at, o.ref_text, numbers[o.document_id])
        for o in db.scalars(
            select(Observation)
            .where(Observation.profile_id == profile.id, Observation.hidden.is_(False), Observation.document_id.in_(numbers))
            .order_by(Observation.code, Observation.observed_at)
        )
    ]
    medications = [
        (" ".join(filter(None, [m.name, m.dose, m.frequency, m.duration])), numbers[m.document_id], m.prescribed_on)
        for m in db.scalars(select(Medication).where(Medication.document_id.in_(numbers)))
    ]
    conditions = [
        (c.name, c.kind, numbers[c.document_id])
        for c in db.scalars(select(Condition).where(Condition.document_id.in_(numbers)))
    ]
    details = [profile.name]
    if profile.sex:
        details.append(profile.sex)
    if profile.dob:
        details.append(f"born {profile.dob.isoformat()}")
    if profile.blood_group:
        details.append(f"blood group {profile.blood_group}")
    prompt = profile_summary.build_prompt(", ".join(details), doc_lines, observations, medications, conditions)
    citations = [
        {"number": numbers[d.id], "document_id": str(d.id), "document_name": d.original_name,
         "doc_date": d.doc_date.isoformat() if d.doc_date else None}
        for d in docs
    ]
    return prompt, citations
