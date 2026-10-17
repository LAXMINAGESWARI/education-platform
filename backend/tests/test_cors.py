"""CORS for same-origin deploys and random Cloudflare quick-tunnel hosts."""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from education_platform.core.config import Settings
from education_platform.main import cors_origin_regex

QUICK_TUNNEL_REGEX = r"https://[A-Za-z0-9-]+\.trycloudflare\.com"
QUICK_ORIGIN = "https://demo-name.trycloudflare.com"


def test_blank_origin_regex_is_omitted() -> None:
    assert cors_origin_regex("") is None
    assert cors_origin_regex("   ") is None


def test_origin_regex_is_stripped() -> None:
    assert cors_origin_regex(f"  {QUICK_TUNNEL_REGEX}  ") == QUICK_TUNNEL_REGEX


def test_settings_read_cors_origin_regex_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGIN_REGEX", QUICK_TUNNEL_REGEX)
    settings = Settings(_env_file=None)
    assert settings.cors_origin_regex == QUICK_TUNNEL_REGEX


def test_default_app_rejects_a_trycloudflare_origin(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": QUICK_ORIGIN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_quick_tunnel_regex_allows_that_host_only() -> None:
    demo = FastAPI()

    @demo.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    demo.add_middleware(
        CORSMiddleware,
        allow_origins=["https://school.example.com"],
        allow_origin_regex=QUICK_TUNNEL_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    http = TestClient(demo)

    allowed = http.get("/health", headers={"Origin": QUICK_ORIGIN})
    assert allowed.headers["access-control-allow-origin"] == QUICK_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"

    blocked = http.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in blocked.headers

    insecure = http.get(
        "/health",
        headers={"Origin": "http://demo-name.trycloudflare.com"},
    )
    assert "access-control-allow-origin" not in insecure.headers
