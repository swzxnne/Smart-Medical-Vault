import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth.clerk import get_current_user
from app.config import clerk_frontend_api
from app.main import app

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(sub="user_real", azp="http://localhost:3000", key=KEY, exp_in=60):
    now = int(time.time())
    claims = {"sub": sub, "iat": now, "nbf": now, "exp": now + exp_in, "azp": azp}
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture
def real_auth_client(client: TestClient, monkeypatch):
    del app.dependency_overrides[get_current_user]
    monkeypatch.setattr("app.auth.clerk._signing_key", lambda _: KEY.public_key())
    return client


def get(client, tok):
    return client.get("/profiles", headers={"Authorization": f"Bearer {tok}"})


def test_valid_token_creates_user_once(real_auth_client):
    tok = token()
    created = real_auth_client.post(
        "/profiles", json={"name": "Me"}, headers={"Authorization": f"Bearer {tok}"}
    )
    assert created.status_code == 201
    assert [p["name"] for p in get(real_auth_client, tok).json()] == ["Me"]


@pytest.mark.parametrize(
    "tok",
    [
        token(key=OTHER_KEY),
        token(azp="https://evil.example"),
        token(exp_in=-60),
        "not-a-jwt",
    ],
    ids=["bad-signature", "wrong-azp", "expired", "garbage"],
)
def test_rejects_invalid_tokens(real_auth_client, tok):
    assert get(real_auth_client, tok).status_code == 401


def test_missing_token(real_auth_client):
    assert real_auth_client.get("/profiles").status_code == 401


def test_frontend_api_from_publishable_key():
    assert clerk_frontend_api("pk_test_Y2xlcmsuZXhhbXBsZS5jb20k") == "clerk.example.com"
