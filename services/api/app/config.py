import base64
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://vault:vault@localhost:5432/vault"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000"

    clerk_publishable_key: str = ""
    clerk_secret_key: str = ""
    # Optional override; derived from the publishable key when empty.
    clerk_jwks_url: str = ""
    clerk_authorized_parties: str = "http://localhost:3000"

    s3_endpoint_url: str = "http://localhost:8333"
    s3_public_endpoint_url: str = "http://localhost:8333"
    s3_access_key: str = "vaultadmin"
    s3_secret_key: str = "vaultsecret"
    s3_bucket: str = "medical-vault"
    s3_region: str = "us-east-1"

    max_upload_bytes: int = 25 * 1024 * 1024
    presign_expiry_seconds: int = 900

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    # Tried in order when the main model is overloaded (503) or rate limited (429).
    gemini_fallback_models: str = "gemini-3-flash-preview,gemini-3.1-flash-lite"
    # Interactive answers ("Ask") need low latency; the pipeline models above are the fallbacks.
    gemini_ask_model: str = "gemini-3.1-flash-lite"
    # Scanned pages Tesseract reads with less than this confidence are re-read by Gemini vision.
    vision_ocr_enabled: bool = True
    vision_ocr_threshold: float = 0.75
    # Keeps a single summarize call inside free-tier tokens-per-minute limits.
    llm_max_input_chars: int = 150_000

    voyage_api_key: str = ""
    voyage_model: str = "voyage-3.5"
    # Voyage's no-payment-method tier allows ~10K tokens/minute, so keep each request under that.
    voyage_batch_tokens: int = 8_000

    # Retrieval
    search_candidates: int = 30  # per method (vector / keyword) before fusion
    ask_top_k: int = 8  # sources given to the LLM
    # Vector-only hits below this cosine similarity are dropped (unrelated text still scores ~0.3).
    min_vector_similarity: float = 0.4

    @property
    def cors_origin_list(self) -> list[str]:
        return _split(self.cors_origins)

    @property
    def authorized_party_list(self) -> list[str]:
        return _split(self.clerk_authorized_parties)

    @property
    def jwks_url(self) -> str:
        if self.clerk_jwks_url:
            return self.clerk_jwks_url
        if not self.clerk_publishable_key:
            raise RuntimeError("Set CLERK_PUBLISHABLE_KEY or CLERK_JWKS_URL")
        return f"https://{clerk_frontend_api(self.clerk_publishable_key)}/.well-known/jwks.json"


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def clerk_frontend_api(publishable_key: str) -> str:
    """Clerk publishable keys are `pk_<env>_<base64("<frontend-api-host>$")>`."""
    encoded = publishable_key.split("_", 2)[2]
    decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    return decoded.rstrip("$")


@lru_cache
def get_settings() -> Settings:
    return Settings()
