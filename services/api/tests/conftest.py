import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi import Depends, Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth.clerk import get_current_user, get_or_create_user
from app.config import get_settings
from app.db import base
from app.db.base import Base, get_db
from app.main import app
from app.pipeline.enqueue import get_pipeline
from app.routers.search import get_answer_llm, get_query_embedder
from app.storage.s3 import ObjectInfo, get_storage


def _test_database_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database})
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


TEST_URL = _test_database_url()
_ensure_database(TEST_URL)
engine = create_engine(TEST_URL)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

with engine.begin() as conn:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, ObjectInfo] = {}
        self.contents: dict[str, bytes] = {}

    def put(self, key: str, size: int = 1024, content_type: str = "application/pdf", etag: str | None = None) -> None:
        self.objects[key] = ObjectInfo(size=size, content_type=content_type, etag=etag or uuid.uuid4().hex)

    def presign_put(self, key: str, content_type: str, expires: int) -> str:
        return f"http://storage.test/put/{key}"

    def presign_get(self, key: str, filename: str, content_type: str, expires: int) -> str:
        return f"http://storage.test/get/{key}"

    def head(self, key: str) -> ObjectInfo | None:
        return self.objects.get(key)

    def download(self, key: str, path) -> None:
        with open(path, "wb") as f:
            f.write(self.contents.get(key, b""))

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self.objects if k.startswith(prefix)]:
            del self.objects[key]


class FakePipeline:
    def __init__(self) -> None:
        self.started: list[uuid.UUID] = []
        self.stages: list[tuple[uuid.UUID, str]] = []
        self.refreshed: list[uuid.UUID] = []
        self.fail = False

    def start(self, document_id: uuid.UUID, from_stage: str = "pipeline.parse") -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.started.append(document_id)
        self.stages.append((document_id, from_stage))

    def refresh_profile(self, profile_id: uuid.UUID, delay: int = 30) -> None:
        self.refreshed.append(profile_id)

    def summary_pending(self, profile_id: uuid.UUID) -> bool:
        return profile_id in self.refreshed


def override_db() -> Iterator[Session]:
    with TestingSession() as session:
        yield session


def fake_user(x_test_user: str = Header(...), db: Session = Depends(get_db)):
    return get_or_create_user(db, x_test_user)


@pytest.fixture(autouse=True)
def no_vision_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.pipeline.providers.parser.get_vision_transcriber", lambda: None)


@pytest.fixture(autouse=True)
def clean_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Pipeline tasks open their own sessions via base.SessionLocal.
    monkeypatch.setattr(base, "SessionLocal", TestingSession)
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def pipeline() -> FakePipeline:
    return FakePipeline()


@pytest.fixture
def client(storage: FakeStorage, pipeline: FakePipeline, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr("app.auth.clerk.fetch_email", lambda clerk_id: None)
    monkeypatch.setattr("app.pipeline.tasks.get_storage", lambda: storage)
    monkeypatch.setattr("app.pipeline.tasks.get_pipeline", lambda: pipeline)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_pipeline] = lambda: pipeline
    app.dependency_overrides[get_current_user] = fake_user
    # Never call real AI services from tests; search tests install fakes explicitly.
    app.dependency_overrides[get_query_embedder] = lambda: None
    app.dependency_overrides[get_answer_llm] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def as_user(clerk_id: str) -> dict[str, str]:
    return {"X-Test-User": clerk_id}


ALICE = as_user("user_alice")
BOB = as_user("user_bob")
