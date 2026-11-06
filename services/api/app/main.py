import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import documents, health, profiles, search
from app.storage.s3 import get_storage

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        get_storage().ensure_bucket()
    except Exception:
        log.exception("Could not ensure storage bucket exists")
    yield


app = FastAPI(title="Smart Medical Vault API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(profiles.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(health.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
