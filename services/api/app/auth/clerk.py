"""Verify Clerk session tokens and map them to local users."""

import logging
from functools import lru_cache
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import get_db
from app.db.models import User

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(get_settings().jwks_url, cache_keys=True, lifespan=3600)


def _signing_key(token: str) -> Any:
    return _jwks_client().get_signing_key_from_jwt(token).key


def verify_token(token: str) -> dict[str, Any]:
    claims = jwt.decode(
        token,
        _signing_key(token),
        algorithms=["RS256"],
        options={"require": ["exp", "iat", "sub"]},
        leeway=5,
    )
    # Clerk puts the requesting origin in `azp`; reject tokens minted for other sites.
    azp = claims.get("azp")
    if azp and azp not in get_settings().authorized_party_list:
        raise jwt.InvalidTokenError(f"Unauthorized party: {azp}")
    return claims


def fetch_email(clerk_id: str) -> str | None:
    secret = get_settings().clerk_secret_key
    if not secret:
        return None
    try:
        resp = httpx.get(
            f"https://api.clerk.com/v1/users/{clerk_id}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        log.warning("Could not fetch Clerk user %s", clerk_id, exc_info=True)
        return None
    primary = data.get("primary_email_address_id")
    for entry in data.get("email_addresses", []):
        if entry.get("id") == primary:
            return entry.get("email_address")
    return None


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        claims = verify_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc

    return get_or_create_user(db, claims["sub"])


def get_or_create_user(db: Session, clerk_id: str) -> User:
    user = db.scalar(select(User).where(User.clerk_id == clerk_id))
    if user is not None:
        return user
    user = User(clerk_id=clerk_id, email=fetch_email(clerk_id))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Another request created the same user concurrently.
        db.rollback()
        user = db.scalar(select(User).where(User.clerk_id == clerk_id))
        assert user is not None
    return user
