import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.clerk import get_current_user
from app.config import get_settings
from app.db.base import get_db
from app.db.models import Condition, Document, DocumentPage, Medication, Observation, Summary, User
from app.health.catalog import GROUP_LABELS, METRICS
from app.health.normalize import RefRange, convert, flag_for
from app.pipeline.enqueue import PIPELINE_VERSION, Pipeline, get_pipeline
from app.routers.documents import get_owned_document
from app.routers.profiles import get_owned_profile
from app.schemas import (
    ConditionOut,
    DocumentFacts,
    HealthOverview,
    MedicationOut,
    MetricSnapshot,
    ObservationCreate,
    ObservationOut,
    ObservationUpdate,
    ProfileSummaryOut,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


def _observation_out(obs: Observation, document_name: str | None) -> ObservationOut:
    out = ObservationOut.model_validate(obs)
    out.document_name = document_name
    out.group = METRICS[obs.code].group if obs.code in METRICS else None
    return out


def _observations(db: Session, *filters) -> list[ObservationOut]:
    rows = db.execute(
        select(Observation, Document.original_name)
        .outerjoin(Document, Document.id == Observation.document_id)
        .where(Observation.hidden.is_(False), *filters)
        .order_by(Observation.observed_at.asc().nulls_last(), Observation.created_at)
    ).all()
    return [_observation_out(obs, name) for obs, name in rows]


def _with_doc_name(model, out_cls, db: Session, *filters, order_by):
    rows = db.execute(
        select(model, Document.original_name).join(Document, Document.id == model.document_id).where(*filters).order_by(*order_by)
    ).all()
    result = []
    for item, name in rows:
        out = out_cls.model_validate(item)
        out.document_name = name
        result.append(out)
    return result


@router.get("/health/catalog")
def catalog():
    """Metric metadata for charts and manual entry."""
    return {
        "groups": GROUP_LABELS,
        "metrics": [
            {"code": m.code, "name": m.name, "unit": m.unit, "group": m.group, "decimals": m.decimals}
            for m in METRICS.values()
        ],
    }


@router.get("/profiles/{profile_id}/health", response_model=HealthOverview)
def health_overview(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    pipeline: Pipeline = Depends(get_pipeline),
):
    profile = get_owned_profile(db, user, profile_id)
    observations = _observations(db, Observation.profile_id == profile.id)

    by_code: dict[str, list[ObservationOut]] = {}
    for obs in observations:
        if obs.code in METRICS and obs.observed_at is not None:
            by_code.setdefault(obs.code, []).append(obs)
    metrics = [
        MetricSnapshot(
            code=code, name=METRICS[code].name, unit=METRICS[code].unit, group=METRICS[code].group,
            count=len(series), latest=series[-1], previous=series[-2] if len(series) > 1 else None,
        )
        for code, series in by_code.items()
    ]
    metrics.sort(key=lambda m: list(METRICS).index(m.code))

    medications = _with_doc_name(
        Medication, MedicationOut, db, Medication.profile_id == profile.id,
        order_by=[Medication.prescribed_on.desc().nulls_last(), Medication.name],
    )
    conditions: list[ConditionOut] = []
    seen: set[str] = set()
    for cond in _with_doc_name(
        Condition, ConditionOut, db, Condition.profile_id == profile.id,
        order_by=[Condition.noted_on.desc().nulls_last()],
    ):
        if cond.name.lower() not in seen:  # most recent mention of each condition
            seen.add(cond.name.lower())
            conditions.append(cond)

    summary = db.scalar(select(Summary).where(Summary.profile_id == profile.id))
    outdated = db.scalar(
        select(func.count()).select_from(Document).where(
            Document.profile_id == profile.id,
            Document.status.in_(("ready", "failed", "uploaded")),
            func.coalesce(Document.pipeline_version, 0) < PIPELINE_VERSION,
        )
    )
    return HealthOverview(
        summary=ProfileSummaryOut(
            content_md=summary.content_md, citations=summary.citations or [], model=summary.model,
            created_at=summary.created_at,
        ) if summary else None,
        metrics=metrics,
        medications=medications,
        conditions=conditions,
        outdated_documents=outdated or 0,
        unverified_count=sum(not o.verified for o in observations),
        summary_pending=_summary_pending(pipeline, profile.id),
    )


def _summary_pending(pipeline: Pipeline, profile_id: uuid.UUID) -> bool:
    try:
        return pipeline.summary_pending(profile_id)
    except Exception:  # Redis down: don't break the overview over a spinner
        return False


def _refresh_summary(pipeline: Pipeline, profile_id: uuid.UUID) -> None:
    """Health facts changed by hand: rewrite the profile summary (debounced, so several edits in a
    row cost one Gemini call)."""
    try:
        pipeline.refresh_profile(profile_id, delay=60)
    except Exception:
        log.exception("Could not schedule profile summary refresh for %s", profile_id)


@router.get("/profiles/{profile_id}/observations", response_model=list[ObservationOut])
def list_observations(
    profile_id: uuid.UUID,
    code: list[str] | None = Query(None),
    group: str | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    profile = get_owned_profile(db, user, profile_id)
    filters = [Observation.profile_id == profile.id]
    codes = set(code or [])
    if group:
        codes |= {m.code for m in METRICS.values() if m.group == group}
    if codes:
        filters.append(Observation.code.in_(codes))
    if date_from:
        filters.append(Observation.observed_at >= date_from)
    if date_to:
        filters.append(Observation.observed_at <= date_to)
    return _observations(db, *filters)


@router.post("/profiles/{profile_id}/observations", response_model=ObservationOut, status_code=201)
def add_observation(
    profile_id: uuid.UUID,
    body: ObservationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    pipeline: Pipeline = Depends(get_pipeline),
):
    profile = get_owned_profile(db, user, profile_id)
    metric = METRICS.get(body.code)
    if metric is None:
        raise HTTPException(422, "Unknown metric")
    value, unit = _canonical(body.code, body.value, body.unit)
    if body.observed_at > date.today():
        raise HTTPException(422, "Date is in the future")
    obs = Observation(
        user_id=user.id, profile_id=profile.id, code=body.code, name=metric.name,
        value_num=value, value_text=f"{body.value:g} {body.unit or metric.unit}", unit=unit,
        observed_at=body.observed_at, source="manual",
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    _refresh_summary(pipeline, profile.id)
    return _observation_out(obs, None)


def _canonical(code: str, value: float, unit: str | None) -> tuple[float, str]:
    metric = METRICS[code]
    converted = convert(code, value, unit or metric.unit)
    if converted is None:
        raise HTTPException(422, f"Unknown unit for {metric.name}")
    low, high = metric.plausible
    if not low <= converted[0] <= high:
        raise HTTPException(
            422, f"{metric.name} should be between {low:g} and {high:g} {metric.unit}"
        )
    return converted[0], converted[1] or metric.unit


def _owned_observation(db: Session, user: User, observation_id: uuid.UUID) -> Observation:
    obs = db.scalar(select(Observation).where(Observation.id == observation_id, Observation.user_id == user.id))
    if obs is None or obs.hidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Observation not found")
    return obs


@router.patch("/observations/{observation_id}", response_model=ObservationOut)
def correct_observation(
    observation_id: uuid.UUID,
    body: ObservationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    pipeline: Pipeline = Depends(get_pipeline),
):
    obs = _owned_observation(db, user, observation_id)
    if body.value is not None:
        if obs.code in METRICS:
            value, unit = _canonical(obs.code, body.value, body.unit or obs.unit)
        else:
            value, unit = body.value, body.unit or obs.unit
        obs.value_num, obs.unit = value, unit
        obs.value_text = f"{body.value:g} {body.unit or unit or ''}".strip()
        obs.flag = flag_for(value, RefRange(obs.ref_low, obs.ref_high)) if obs.ref_low is not None or obs.ref_high is not None else None
    if body.observed_at is not None:
        obs.observed_at = body.observed_at
    # Editing or confirming means a person has checked it.
    obs.verified, obs.review_note = True, None
    if obs.source == "ai":
        obs.source = "corrected"
    db.commit()
    db.refresh(obs)
    _refresh_summary(pipeline, obs.profile_id)
    name = db.scalar(select(Document.original_name).where(Document.id == obs.document_id)) if obs.document_id else None
    return _observation_out(obs, name)


@router.delete("/observations/{observation_id}", status_code=204)
def delete_observation(
    observation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    pipeline: Pipeline = Depends(get_pipeline),
):
    obs = _owned_observation(db, user, observation_id)
    profile_id = obs.profile_id
    if obs.source == "manual":
        db.delete(obs)
    else:
        obs.hidden = True  # remembered, so re-analysing the document doesn't bring it back
    db.commit()
    _refresh_summary(pipeline, profile_id)
    return Response(status_code=204)


@router.get("/documents/{document_id}/facts", response_model=DocumentFacts)
def document_facts(document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = get_owned_document(db, user, document_id)
    return DocumentFacts(
        observations=_observations(db, Observation.document_id == doc.id),
        medications=_with_doc_name(Medication, MedicationOut, db, Medication.document_id == doc.id, order_by=[Medication.name]),
        conditions=_with_doc_name(Condition, ConditionOut, db, Condition.document_id == doc.id, order_by=[Condition.name]),
    )


@router.post("/profiles/{profile_id}/summary/refresh", status_code=202)
def refresh_profile_summary(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    pipeline: Pipeline = Depends(get_pipeline),
):
    profile = get_owned_profile(db, user, profile_id)
    pipeline.refresh_profile(profile.id, delay=0)
    return {"status": "queued"}


@router.post("/documents/reprocess-outdated")
def reprocess_outdated(
    profile_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Re-analyse documents processed by an older pipeline. Ready documents that already have
    text and embeddings only re-run the (cheap) summarize stage."""
    filters = [
        Document.user_id == user.id,
        Document.status.in_(("ready", "failed", "uploaded")),
        func.coalesce(Document.pipeline_version, 0) < PIPELINE_VERSION,
    ]
    if profile_id is not None:
        filters.append(Document.profile_id == get_owned_profile(db, user, profile_id).id)
    docs = db.scalars(select(Document).where(*filters)).all()
    ids = [d.id for d in docs]
    has_pages = set(db.scalars(select(DocumentPage.document_id).where(DocumentPage.document_id.in_(ids))))
    # Scans OCR'd with low confidence before the vision fallback existed need OCR again.
    low_ocr = set(db.scalars(select(DocumentPage.document_id).where(
        DocumentPage.document_id.in_(ids),
        DocumentPage.engine == "tesseract",
        or_(DocumentPage.ocr_confidence.is_(None), DocumentPage.ocr_confidence < get_settings().vision_ocr_threshold),
    )))
    for doc in docs:
        cheap = doc.status == "ready" and doc.id in has_pages and doc.id not in low_ocr
        stage = "pipeline.summarize" if cheap else "pipeline.parse"
        doc.status, doc.error = "queued", None
        db.commit()
        pipeline.start(doc.id, from_stage=stage)
    return {"queued": len(docs)}
