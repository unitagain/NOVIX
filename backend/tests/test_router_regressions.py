"""Regression tests for critical router interactions."""

from __future__ import annotations


import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import fanfiction as fanfiction_router
from app.routers import session as session_router


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_providers_meta_exposes_supported_providers(client) -> None:
    response = await client.get("/config/llm/providers_meta")
    assert response.status_code == 200

    items = {item["id"]: item for item in response.json()}
    assert items["wenxin"]["fields"] == ["api_key", "model", "base_url"]
    assert items["aistudio"]["label"] == "AI Studio 飞桨"
    assert items["custom"]["label"] == "Custom (OpenAI Format)"


@pytest.mark.asyncio
async def test_fanfiction_preview_rejects_non_http_url(client) -> None:
    response = await client.post("/fanfiction/preview", json={"url": "file:///tmp/demo.html"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert "http/https" in payload["error"]


@pytest.mark.asyncio
async def test_fanfiction_preview_uses_crawler_result(monkeypatch, client) -> None:
    monkeypatch.setattr(
        fanfiction_router.crawler_service,
        "scrape_page",
        lambda url: {
            "success": True,
            "title": "鸣潮",
            "content": "页面内容",
            "links": [{"title": "角色A", "url": "https://example.com/a"}],
            "is_list_page": True,
        },
    )

    response = await client.post("/fanfiction/preview", json={"url": "https://example.com/wiki"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["title"] == "鸣潮"
    assert payload["links"][0]["title"] == "角色A"


@pytest.mark.asyncio
async def test_test_model_rejects_empty_model(client) -> None:
    response = await client.post(
        "/proxy/test-model",
        json={"provider": "openai", "api_key": "test-key", "model": "", "base_url": "https://example.com/v1"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Model is required."


@pytest.mark.asyncio
async def test_edit_suggest_returns_user_facing_error_when_revision_unchanged(monkeypatch, client) -> None:
    class FakeEditor:
        async def suggest_revision(self, **kwargs):
            return kwargs["original_draft"]

    class FakeOrchestrator:
        editor = FakeEditor()

        class Application:
            class Context:
                async def ensure_memory_pack(self, **kwargs):
                    return {"summary": "cached"}

            context = Context()

        application = Application()

    monkeypatch.setattr(
        session_router, "get_orchestrator", lambda project_id, request_language=None: FakeOrchestrator()
    )

    response = await client.post(
        "/projects/demo/session/edit-suggest",
        json={
            "chapter": "1",
            "content": "原文内容",
            "instruction": "优化开头",
            "context_mode": "quick",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert "未能生成可应用的差异修改" in payload["error"]
