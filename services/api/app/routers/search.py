import json
import logging
import uuid
from datetime import date
from collections.abc import Iterator
from functools import lru_cache

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.clerk import get_current_user
from app.config import get_settings
from app.db.base import get_db
from app.db.models import Document, Observation, Profile, User
from app.health.catalog import METRICS
from app.pipeline.providers.errors import PipelineError, TransientError
from app.routers.profiles import get_owned_profile
from app.schemas import AskRequest, SearchHitOut, SearchResponse
from app.search import answer, metric_query
from app.search.answer import MeasurementSource
from app.search.embedding import QueryEmbedder
from app.search.retrieval import Hit, hybrid_search

log = logging.getLogger(__name__)
router = APIRouter(tags=["search"])


@lru_cache
def get_query_embedder() -> QueryEmbedder | None:
    from app.pipeline.providers.embedder import get_embedder

    try:
        return QueryEmbedder(get_embedder())
    except PipelineError:
        log.warning("VOYAGE_API_KEY not set; search is keyword-only")
        return None


def get_answer_llm():
    from app.pipeline.providers.llm import get_ask_llm

    try:
        return get_ask_llm()
    except PipelineError:
        return None


def _retrieve(
    db: Session, user: User, query: str, profile_id: uuid.UUID | None, limit: int, embedder: QueryEmbedder | None
) -> tuple[list[Hit], str]:
    if profile_id is not None:
        get_owned_profile(db, user, profile_id)
    settings = get_settings()
    vector = embedder.embed(query) if embedder else None
    hits = hybrid_search(
        db,
        user_id=user.id,
        query=query,
        query_vector=vector,
        profile_id=profile_id,
        limit=limit,
        candidates=settings.search_candidates,
        min_similarity=settings.min_vector_similarity,
    )
    return hits, "hybrid" if vector is not None else "keyword"


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=1, max_length=500),
    profile_id: uuid.UUID | None = None,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embedder: QueryEmbedder | None = Depends(get_query_embedder),
):
    hits, mode = _retrieve(db, user, q, profile_id, limit, embedder)
    return SearchResponse(query=q, mode=mode, results=[SearchHitOut.model_validate(h) for h in hits])


MAX_MEASUREMENT_ROWS = 60


def _measurements(
    db: Session, user: User, question: str, profile_id: uuid.UUID | None
) -> tuple[list[MeasurementSource], list[dict]]:
    """Extracted values matching a measurement question, grouped per document page."""
    query = metric_query.parse(question)
    if query is None:
        return [], []
    filters = [
        Observation.user_id == user.id,
        Observation.hidden.is_(False),
        Observation.code.in_(query.codes),
    ]
    if profile_id is not None:
        filters.append(Observation.profile_id == profile_id)
    if query.date_from:
        filters.append(Observation.observed_at >= query.date_from)
    if query.date_to:
        filters.append(Observation.observed_at <= query.date_to)
    rows = db.execute(
        select(Observation, Profile.name, Document)
        .join(Profile, Profile.id == Observation.profile_id)
        .outerjoin(Document, Document.id == Observation.document_id)
        .where(*filters)
        .order_by(Observation.observed_at.asc().nulls_last())
        .limit(MAX_MEASUREMENT_ROWS)
    ).all()

    # Same-date readings in catalog order (systolic before diastolic), not whatever order Postgres returns.
    order = {code: i for i, code in enumerate(METRICS)}
    rows = sorted(rows, key=lambda r: (r[0].observed_at or date.max, order.get(r[0].code, len(order)), r[0].name))

    groups: dict[tuple, MeasurementSource] = {}
    observations: list[dict] = []
    for obs, profile_name, doc in rows:
        key = (obs.profile_id, obs.document_id, obs.page_no)
        if key not in groups:
            groups[key] = MeasurementSource(
                profile_name=profile_name,
                document_id=doc.id if doc else None,
                document_name=doc.original_name if doc else "Manually entered readings",
                doc_type=doc.doc_type if doc else None,
                doc_date=doc.doc_date if doc else None,
                page_no=obs.page_no,
                lines=[],
            )
        if obs.value_num is not None:
            # Converted values (e.g. umol/L -> mg/dL) carry float noise; show them at display precision.
            shown = round(obs.value_num, METRICS[obs.code].decimals) if obs.code in METRICS else obs.value_num
            value = f"{shown:g} {obs.unit or ''}".strip()
        else:
            value = obs.value_text
        when = obs.observed_at.isoformat() if obs.observed_at else "date unknown"
        flag = f", flagged {obs.flag}" if obs.flag in ("high", "low") else ""
        ref = f", reference {obs.ref_text}" if obs.ref_text else ""
        review = "" if obs.verified else " (unverified: not yet checked by the user)"
        groups[key].lines.append(f"{obs.name}: {value} on {when}{ref}{flag}{review}")
        observations.append({
            "source_key": key, "code": obs.code, "name": obs.name, "value_num": obs.value_num,
            "value_text": obs.value_text, "unit": obs.unit, "observed_at": obs.observed_at,
            "flag": obs.flag, "profile_name": profile_name, "verified": obs.verified,
        })

    numbers = {key: i for i, key in enumerate(groups, start=1)}
    for item in observations:
        item["source_number"] = numbers[item.pop("source_key")]
    return list(groups.values()), observations


def _standalone_question(llm, body: AskRequest) -> str:
    if llm is None:
        return answer.fallback_standalone(body.history, body.question)
    try:
        result, _ = llm.generate_json(
            system=answer.REWRITE_SYSTEM,
            prompt=answer.rewrite_prompt(body.history, body.question),
            schema=answer.StandaloneQuestion,
        )
        rewritten = result.question.strip()
        return rewritten[:1000] if rewritten else body.question
    except (TransientError, PipelineError):
        log.warning("Follow-up rewrite failed; searching with the previous question as context")
        return answer.fallback_standalone(body.history, body.question)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/ask")
def ask(
    body: AskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    embedder: QueryEmbedder | None = Depends(get_query_embedder),
    llm=Depends(get_answer_llm),
):
    """Streams server-sent events: `sources`, then `delta`s of answer text, then `done`
    (with the source numbers actually cited) or `error`."""
    # Follow-ups ("and in 2023?") are rewritten into a standalone question before anything else.
    question = _standalone_question(llm, body) if body.history else body.question

    profile_id = body.profile_id
    if profile_id is None:
        # "What was Ravi's sugar?" -> answer from Ravi's records only.
        profiles = db.execute(select(Profile.id, Profile.name).where(Profile.user_id == user.id)).all()
        mentioned = metric_query.mentioned_profiles(question, [(p.id, p.name) for p in profiles])
        if len(mentioned) == 1:
            profile_id = mentioned[0]

    measurements, observations = _measurements(db, user, question, profile_id)
    top_k = get_settings().ask_top_k
    # With exact measurements in hand, fewer text passages keep the prompt focused.
    hits, mode = _retrieve(db, user, question, profile_id, top_k // 2 if measurements else top_k, embedder)
    sources = [
        {
            "number": i, "kind": "measurements", "document_id": m.document_id, "document_name": m.document_name,
            "doc_type": m.doc_type, "doc_date": m.doc_date, "profile_name": m.profile_name, "page_no": m.page_no,
            "snippet": "\n".join(m.lines),
        }
        for i, m in enumerate(measurements, start=1)
    ] + [
        {"number": i, "kind": "passage", **SearchHitOut.model_validate(hit).model_dump(mode="json")}
        for i, hit in enumerate(hits, start=len(measurements) + 1)
    ]
    source_count = len(sources)

    def events() -> Iterator[str]:
        yield _sse("sources", {"mode": mode, "sources": sources, "observations": observations, "standalone_question": question})
        if not sources:
            # Nothing to ground an answer in; don't spend an LLM call.
            yield _sse("delta", {"text": answer.NOT_FOUND})
            yield _sse("done", {"cited": [], "model": None})
            return
        if llm is None:
            yield _sse("error", {"message": "GEMINI_API_KEY is not set on the server."})
            return

        parts: list[str] = []
        model_used = None
        try:
            for model_used, delta in llm.stream_text(
                system=answer.SYSTEM_PROMPT,
                prompt=answer.build_prompt(body.question, hits, measurements, body.history, question),
            ):
                parts.append(delta)
                yield _sse("delta", {"text": delta})
        except TransientError:
            log.warning("Ask failed", exc_info=True)
            yield _sse("error", {"message": "The AI service is busy right now. Please try again in a minute."})
            return
        except Exception:
            log.exception("Ask failed")
            yield _sse("error", {"message": "Something went wrong while generating the answer."})
            return
        yield _sse("done", {"cited": answer.cited_numbers("".join(parts), source_count), "model": model_used})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
