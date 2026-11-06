import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.clerk import get_current_user
from app.config import get_settings
from app.db.base import get_db
from app.db.models import Document, DocumentPage, Summary, User
from app.pipeline.enqueue import Pipeline, get_pipeline
from app.routers.profiles import get_owned_profile
from app.schemas import (
    ALLOWED_CONTENT_TYPES,
    DocumentCreate,
    DocumentOut,
    FileUrlResponse,
    PageOut,
    SummaryOut,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.storage.s3 import Storage, get_storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# Statuses from which a document may be (re)processed; anything else is mid-pipeline.
REPROCESSABLE = {"ready", "failed", "uploaded"}


def safe_filename(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned[-100:] or "file"


def storage_prefix(user_id: uuid.UUID, profile_id: uuid.UUID) -> str:
    return f"{user_id}/{profile_id}/"


def get_owned_document(db: Session, user: User, document_id: uuid.UUID) -> Document:
    doc = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.id))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


def start_pipeline(db: Session, doc: Document, pipeline: Pipeline) -> None:
    doc.status = "queued"
    doc.error = None
    db.commit()
    try:
        pipeline.start(doc.id)
    except Exception:
        log.exception("Could not queue document %s", doc.id)
        doc.status = "failed"
        doc.error = "Could not queue for processing (is Redis running?). Try Reprocess."
        db.commit()


@router.post("/upload-url", response_model=UploadUrlResponse)
def create_upload_url(
    body: UploadUrlRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: Storage = Depends(get_storage),
):
    profile = get_owned_profile(db, user, body.profile_id)
    if body.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported file type")

    key = f"{storage_prefix(user.id, profile.id)}{uuid.uuid4()}/{safe_filename(body.filename)}"
    expires = get_settings().presign_expiry_seconds
    return UploadUrlResponse(
        upload_url=storage.presign_put(key, body.content_type, expires),
        storage_key=key,
        headers={"Content-Type": body.content_type},
        expires_in=expires,
    )


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def register_document(
    body: DocumentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: Storage = Depends(get_storage),
    pipeline: Pipeline = Depends(get_pipeline),
):
    profile = get_owned_profile(db, user, body.profile_id)
    key = body.storage_key
    if not key.startswith(storage_prefix(user.id, profile.id)) or ".." in key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid storage key")
    if db.scalar(select(Document.id).where(Document.storage_key == key)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Document already registered")

    info = storage.head(key)
    if info is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload not found")
    if info.size > get_settings().max_upload_bytes:
        storage.delete(key)
        raise HTTPException(413, "File too large")
    if info.content_type not in ALLOWED_CONTENT_TYPES:
        storage.delete(key)
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported file type")

    checksum = info.etag or None
    if checksum:
        existing = db.scalar(
            select(Document).where(Document.profile_id == profile.id, Document.checksum == checksum)
        )
        if existing is not None:
            storage.delete(key)
            raise HTTPException(
                status.HTTP_409_CONFLICT, f'This file is already uploaded as "{existing.original_name}"'
            )

    doc = Document(
        user_id=user.id,
        profile_id=profile.id,
        storage_key=key,
        original_name=body.original_name,
        mime=info.content_type,
        size_bytes=info.size,
        checksum=checksum,
    )
    db.add(doc)
    db.flush()
    start_pipeline(db, doc, pipeline)
    db.refresh(doc)
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(
    profile_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Document).where(Document.user_id == user.id)
    if profile_id is not None:
        query = query.where(Document.profile_id == profile_id)
    return db.scalars(query.order_by(Document.created_at.desc())).all()


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return get_owned_document(db, user, document_id)


@router.post("/{document_id}/reprocess", response_model=DocumentOut)
def reprocess_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    pipeline: Pipeline = Depends(get_pipeline),
):
    doc = get_owned_document(db, user, document_id)
    if doc.status not in REPROCESSABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Document is already being processed")
    start_pipeline(db, doc, pipeline)
    db.refresh(doc)
    return doc


@router.get("/{document_id}/summary", response_model=SummaryOut)
def get_document_summary(
    document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    doc = get_owned_document(db, user, document_id)
    summary = db.scalar(select(Summary).where(Summary.document_id == doc.id))
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No summary yet")
    return summary


@router.get("/{document_id}/pages", response_model=list[PageOut])
def get_document_pages(
    document_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    doc = get_owned_document(db, user, document_id)
    return db.scalars(
        select(DocumentPage).where(DocumentPage.document_id == doc.id).order_by(DocumentPage.page_no)
    ).all()


@router.get("/{document_id}/file", response_model=FileUrlResponse)
def get_document_file(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: Storage = Depends(get_storage),
):
    doc = get_owned_document(db, user, document_id)
    expires = get_settings().presign_expiry_seconds
    url = storage.presign_get(doc.storage_key, doc.original_name, doc.mime, expires)
    return FileUrlResponse(url=url, expires_in=expires)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: Storage = Depends(get_storage),
    pipeline: Pipeline = Depends(get_pipeline),
):
    doc = get_owned_document(db, user, document_id)
    key, profile_id = doc.storage_key, doc.profile_id
    db.delete(doc)
    db.commit()
    try:
        storage.delete(key)
    except Exception:
        log.exception("Failed to delete file %s", key)
    try:
        pipeline.refresh_profile(profile_id)
    except Exception:
        log.exception("Could not schedule profile summary refresh for %s", profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
