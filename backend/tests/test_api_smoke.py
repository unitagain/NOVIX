"""Smoke tests for FastAPI endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_list_projects(client):
    resp = await client.get("/projects")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_unknown_route_404(client):
    resp = await client.get("/api/nonexistent")
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_cards_requires_project(client):
    resp = await client.get("/projects/__nonexistent__/cards/characters")
    # Should return 200 with empty list or 404, not 500
    assert resp.status_code in (200, 404)
