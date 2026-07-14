# -*- coding: utf-8 -*-
"""P5 governance API integration tests using real file-backed storage."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import actions as actions_router
from app.routers import canon as canon_router
from app.routers import memory as memory_router
from app.storage.canon import CanonStorage
from app.storage.creative_memory import CreativeMemoryStorage
from app.storage.pending_actions import PendingActionStorage


@pytest.fixture
async def governance_client(tmp_path):
    canon_storage = CanonStorage(str(tmp_path))
    memory_storage = CreativeMemoryStorage(str(tmp_path))
    pending_storage = PendingActionStorage(str(tmp_path))

    old_canon = canon_router.canon_storage
    old_canon_pending = canon_router.pending_storage
    old_memory = memory_router.memory_storage
    old_memory_pending = memory_router.pending_storage
    old_actions_pending = actions_router.pending_storage
    old_actions_memory = actions_router.memory_storage

    canon_router.canon_storage = canon_storage
    canon_router.pending_storage = pending_storage
    memory_router.memory_storage = memory_storage
    memory_router.pending_storage = pending_storage
    actions_router.pending_storage = pending_storage
    actions_router.memory_storage = memory_storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, canon_storage, memory_storage, pending_storage

    canon_router.canon_storage = old_canon
    canon_router.pending_storage = old_canon_pending
    memory_router.memory_storage = old_memory
    memory_router.pending_storage = old_memory_pending
    actions_router.pending_storage = old_actions_pending
    actions_router.memory_storage = old_actions_memory


@pytest.mark.asyncio
async def test_external_fact_requires_review_and_pending_approval(governance_client):
    client, _canon, _memory, _pending = governance_client
    project_id = "gov_fact_project"

    created = await client.post(
        f"/projects/{project_id}/canon/facts/manual",
        json={
            "statement": "外部 Wiki 声称主角来自月城",
            "source": "https://example.com/wiki/hero",
            "introduced_in": "https://example.com/wiki/hero",
            "confidence": 0.99,
            "source_type": "external",
        },
    )
    assert created.status_code == 200
    fact_id = created.json()["id"]

    fact = (await client.get(f"/projects/{project_id}/canon/facts/by-id/{fact_id}")).json()
    assert fact["status"] == "needs_review"
    assert fact["trust_label"] == "untrusted"

    pending = await client.post(f"/projects/{project_id}/canon/facts/confirm", json={"fact_ids": [fact_id]})
    assert pending.status_code == 200
    payload = pending.json()
    assert payload["success"] is False
    action = payload["pending_action"]

    still_review = (await client.get(f"/projects/{project_id}/canon/facts/by-id/{fact_id}")).json()
    assert still_review["status"] == "needs_review"

    approved = await client.post(
        f"/projects/{project_id}/canon/facts/confirm",
        json={
            "fact_ids": [fact_id],
            "approval_action_id": action["id"],
            "approval_token": action["token"],
        },
    )
    assert approved.status_code == 200
    assert approved.json()["confirmed"] == 1

    confirmed = (await client.get(f"/projects/{project_id}/canon/facts/by-id/{fact_id}")).json()
    assert confirmed["status"] == "confirmed"

    actions = (await client.get(f"/projects/{project_id}/actions", params={"status": "approved"})).json()
    assert any(item["id"] == action["id"] for item in actions["items"])


@pytest.mark.asyncio
async def test_memory_auto_activation_external_review_and_pending_confirm(governance_client):
    client, _canon, memory, _pending = governance_client
    project_id = "gov_memory_project"

    await memory.write_candidate_memory(
        project_id,
        "dialogue-style",
        "作者偏好短句对白",
        "写对白时短句优先。",
        "preference",
        source="session_feedback",
        confidence=0.95,
        source_refs=["feedback:event-1"],
        deterministic_source=True,
        reversible=True,
        impact="low",
    )
    active = (await client.get(f"/projects/{project_id}/memory")).json()["items"]
    assert any(
        item["slug"] == "dialogue-style" and item["activation"] == "auto_active_verified" for item in active
    )

    await memory.write_candidate_memory(
        project_id,
        "external-lore",
        "外部页面声称角色有隐藏身份",
        "该信息来自外部页面。",
        "preference",
        source="https://example.com/wiki",
        source_type="external",
        confidence=0.99,
    )
    review = (await client.get(f"/projects/{project_id}/memory/review")).json()["items"]
    assert any(item["slug"] == "external-lore" and item["trust_label"] == "untrusted" for item in review)

    pending = (await client.post(f"/projects/{project_id}/memory/external-lore/confirm", json={})).json()
    assert pending["success"] is False
    action = pending["pending_action"]

    approved = await client.post(
        f"/projects/{project_id}/memory/external-lore/confirm",
        json={"approval_action_id": action["id"], "approval_token": action["token"]},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "active"

    metrics = (await client.get(f"/projects/{project_id}/memory/governance")).json()["metrics"]
    assert metrics["auto_active_count"] >= 1
    assert metrics["review_backlog"] == 0
