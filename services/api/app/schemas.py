import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Sex = Literal["male", "female", "other"]
BloodGroup = Literal["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dob: date | None = None
    sex: Sex | None = None
    blood_group: BloodGroup | None = None
    relation: str | None = Field(default=None, max_length=40)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    dob: date | None = None
    sex: Sex | None = None
    blood_group: BloodGroup | None = None
    relation: str | None = Field(default=None, max_length=40)

    @field_validator("name")
    @classmethod
    def name_not_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("name cannot be null")
        return value


class ProfileOut(ProfileCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class UploadUrlRequest(BaseModel):
    profile_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str


class UploadUrlResponse(BaseModel):
    upload_url: str
    storage_key: str
    headers: dict[str, str]
    expires_in: int


class DocumentCreate(BaseModel):
    profile_id: uuid.UUID
    storage_key: str = Field(min_length=1, max_length=512)
    original_name: str = Field(min_length=1, max_length=255)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    original_name: str
    mime: str
    size_bytes: int
    status: str
    error: str | None
    doc_type: str | None
    doc_date: date | None
    provider: str | None
    patient_name: str | None
    review_reason: str | None
    page_count: int | None
    processed_at: datetime | None
    created_at: datetime


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_no: int
    text: str
    engine: str
    ocr_confidence: float | None


class SummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    content_md: str
    model: str
    created_at: datetime


class FileUrlResponse(BaseModel):
    url: str
    expires_in: int


class SearchHitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    doc_type: str | None
    doc_date: date | None
    profile_id: uuid.UUID
    profile_name: str
    page_no: int
    snippet: str
    score: float
    matched_by: list[str]


class SearchResponse(BaseModel):
    query: str
    # "keyword" when the query couldn't be embedded (e.g. Voyage rate limit) and only full-text ran.
    mode: Literal["hybrid", "keyword"]
    results: list[SearchHitOut]


class AskTurn(BaseModel):
    question: str = Field(max_length=1000)
    answer: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    profile_id: uuid.UUID | None = None
    # Earlier turns of this conversation, oldest first; only the last few are used.
    history: list[AskTurn] = Field(default_factory=list, max_length=20)


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    document_id: uuid.UUID | None
    document_name: str | None = None
    page_no: int | None
    code: str
    group: str | None = None
    name: str
    value_num: float | None
    value_text: str
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_text: str | None
    flag: str | None
    observed_at: date | None
    source: str
    verified: bool
    review_note: str | None


class ObservationCreate(BaseModel):
    code: str
    value: float
    unit: str | None = None  # defaults to the metric's canonical unit
    observed_at: date


class ObservationUpdate(BaseModel):
    value: float | None = None
    unit: str | None = None
    observed_at: date | None = None
    confirm: bool = False  # "this value is right as extracted"


class MedicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    document_name: str | None = None
    page_no: int | None
    name: str
    dose: str | None
    frequency: str | None
    duration: str | None
    prescribed_on: date | None


class ConditionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    document_name: str | None = None
    page_no: int | None
    name: str
    kind: str
    noted_on: date | None


class MetricSnapshot(BaseModel):
    code: str
    name: str
    unit: str
    group: str
    count: int
    latest: ObservationOut
    previous: ObservationOut | None


class ProfileSummaryOut(BaseModel):
    content_md: str
    citations: list[dict]
    model: str
    created_at: datetime


class HealthOverview(BaseModel):
    summary: ProfileSummaryOut | None
    metrics: list[MetricSnapshot]
    medications: list[MedicationOut]
    conditions: list[ConditionOut]
    outdated_documents: int
    unverified_count: int
    summary_pending: bool


class DocumentFacts(BaseModel):
    observations: list[ObservationOut]
    medications: list[MedicationOut]
    conditions: list[ConditionOut]
