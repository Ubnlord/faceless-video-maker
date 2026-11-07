"""API tests. These exercise the real FastAPI app in-process."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ffmpeg" in body
    assert body["resolution"]


def test_index_serves_the_ui(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Faceless" in r.text
    assert "/api/videos" in r.text


def test_options(client: TestClient) -> None:
    body = client.get("/api/options").json()
    assert "Motivation" in body["niches"]
    assert "60 seconds" in body["lengths"]
    assert "bold" in body["caption_styles"]


def test_backgrounds(client: TestClient) -> None:
    body = client.get("/api/backgrounds").json()
    assert "aurora" in body["backgrounds"]


def test_script_endpoint(client: TestClient) -> None:
    r = client.post(
        "/api/script",
        json={"topic": "how to build a daily reading habit", "length": "30 seconds"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["script"]
    assert body["engine"]
    assert len(body["scenes"]) == 4
    assert all(s["caption"] for s in body["scenes"])


def test_script_endpoint_rejects_short_topic(client: TestClient) -> None:
    r = client.post("/api/script", json={"topic": "ab"})
    assert r.status_code == 422


def test_script_endpoint_accepts_user_script(client: TestClient) -> None:
    r = client.post(
        "/api/script",
        json={
            "topic": "anything at all",
            "script": "First line of my own script here.\nSecond line of my own script here.",
        },
    )
    assert r.status_code == 200
    assert r.json()["engine"] == "user-supplied"


def test_video_listing_starts_empty(client: TestClient) -> None:
    body = client.get("/api/videos").json()
    assert "videos" in body


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/api/videos/doesnotexist").status_code == 404
    assert client.get("/api/videos/doesnotexist/video").status_code == 404
    assert client.delete("/api/videos/doesnotexist").status_code == 404


def test_create_video_validates_topic(client: TestClient) -> None:
    r = client.post("/api/videos", json={"topic": "x"})
    assert r.status_code == 422
