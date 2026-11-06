"""Hybrid retrieval: pgvector similarity + Postgres full-text, fused with Reciprocal Rank Fusion.

Every query is scoped to one user (and optionally one profile) before ranking.
"""

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, Profile

RRF_K = 60
HIGHLIGHT_START, HIGHLIGHT_STOP = "⟦", "⟧"  # ⟦ ⟧, split on by the UI
_HEADLINE_OPTIONS = f"StartSel={HIGHLIGHT_START},StopSel={HIGHLIGHT_STOP},MaxWords=40,MinWords=15,MaxFragments=2"


@dataclass
class Hit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    doc_type: str | None
    doc_date: date | None
    profile_id: uuid.UUID
    profile_name: str
    page_no: int
    text: str
    snippet: str
    score: float
    matched_by: list[str] = field(default_factory=list)


def keyword_query(query: str) -> str | None:
    """OR together the query's words so natural-language questions still match on key terms.

    Only letters and digits reach to_tsquery, so user input can't inject tsquery syntax.
    """
    tokens = re.findall(r"[A-Za-z0-9]+", query)
    return " | ".join(tokens) if tokens else None


def hybrid_search(
    db: Session,
    *,
    user_id: uuid.UUID,
    query: str,
    query_vector: list[float] | None,
    profile_id: uuid.UUID | None = None,
    limit: int = 10,
    candidates: int = 30,
    min_similarity: float = 0.4,
) -> list[Hit]:
    scope = [Chunk.user_id == user_id]
    if profile_id is not None:
        scope.append(Chunk.profile_id == profile_id)

    scores: dict[uuid.UUID, float] = defaultdict(float)
    matched: dict[uuid.UUID, list[str]] = defaultdict(list)

    if query_vector is not None:
        # With a WHERE filter, HNSW may return too few rows; iterative scan keeps searching (pgvector >= 0.8).
        db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        distance = Chunk.embedding.cosine_distance(query_vector)
        rows = db.execute(
            select(Chunk.id, distance.label("distance")).where(*scope).order_by(distance).limit(candidates)
        ).all()
        for rank, (chunk_id, dist) in enumerate(r for r in rows if 1 - r.distance >= min_similarity):
            scores[chunk_id] += 1 / (RRF_K + rank + 1)
            matched[chunk_id].append("semantic")

    tsq_text = keyword_query(query)
    tsquery = func.to_tsquery("english", tsq_text) if tsq_text else None
    if tsquery is not None:
        rank_expr = func.ts_rank_cd(Chunk.tsv, tsquery)
        ids = db.scalars(
            select(Chunk.id).where(*scope, Chunk.tsv.op("@@")(tsquery)).order_by(rank_expr.desc()).limit(candidates)
        ).all()
        for rank, chunk_id in enumerate(ids):
            scores[chunk_id] += 1 / (RRF_K + rank + 1)
            matched[chunk_id].append("keyword")

    top = sorted(scores, key=scores.__getitem__, reverse=True)[:limit]
    if not top:
        return []

    snippet = (
        func.ts_headline("english", Chunk.text, tsquery, _HEADLINE_OPTIONS)
        if tsquery is not None
        else func.left(Chunk.text, 300)
    )
    rows = db.execute(
        select(Chunk, Document.original_name, Document.doc_type, Document.doc_date, Profile.name, snippet.label("snippet"))
        .join(Document, Document.id == Chunk.document_id)
        .join(Profile, Profile.id == Chunk.profile_id)
        .where(Chunk.id.in_(top))
    ).all()
    by_id = {
        row.Chunk.id: Hit(
            chunk_id=row.Chunk.id,
            document_id=row.Chunk.document_id,
            document_name=row.original_name,
            doc_type=row.doc_type,
            doc_date=row.doc_date,
            profile_id=row.Chunk.profile_id,
            profile_name=row.name,
            page_no=row.Chunk.page_no,
            text=row.Chunk.text,
            snippet=row.snippet,
            score=round(scores[row.Chunk.id], 5),
            matched_by=matched[row.Chunk.id],
        )
        for row in rows
    }
    return [by_id[i] for i in top if i in by_id]
