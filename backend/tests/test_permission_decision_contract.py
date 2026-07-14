from __future__ import annotations

import pytest

from app.agents.agent_task import AgentTask, AgentTaskRunner
from app.context_engine.tool_registry import tool_loadout
from app.storage.pending_actions import PendingActionStorage
from app.utils.permissions import PermissionLevel, decide_permission, strictest


def test_permission_precedence_and_unknown_operation():
    assert strictest("allow", "ask", "deny") is PermissionLevel.DENY
    assert strictest("allow", "ask") is PermissionLevel.ASK
    assert decide_permission("unknown_operation").level is PermissionLevel.ASK


def test_background_actor_cannot_upgrade_ask_or_parent_deny():
    assert decide_permission("save_draft", actor="worker").level is PermissionLevel.DENY
    decision = decide_permission(
        "query_canon",
        actor="worker",
        parent_restrictions={"level": "deny"},
    )
    assert decision.level is PermissionLevel.DENY


def test_untrusted_write_and_tool_metadata_share_decision_owner():
    direct = decide_permission(
        "write_content",
        trust_context={"trust_label": "untrusted"},
    )
    metadata = tool_loadout(["write_content"], consumed_untrusted=True)[0]
    assert metadata["permission"] == direct.level.value == "ask"
    assert metadata["permission_decision"]["operation"] == "write_content"


@pytest.mark.asyncio
async def test_pending_approval_binds_payload_resource_trust_and_is_single_use(tmp_path):
    storage = PendingActionStorage(str(tmp_path))
    action = await storage.create_action(
        "project",
        operation="confirm_memory",
        target={"slug": "one"},
        payload={"status": "active"},
        trust_context={"trust_label": "untrusted"},
    )
    with pytest.raises(ValueError, match="target_hash_mismatch"):
        await storage.consume_action(
            "project",
            action_id=action["id"],
            token=action["token"],
            operation="confirm_memory",
            target={"slug": "one"},
            payload={"status": "rejected"},
            trust_context={"trust_label": "untrusted"},
        )
    approved = await storage.consume_action(
        "project",
        action_id=action["id"],
        token=action["token"],
        operation="confirm_memory",
        target={"slug": "one"},
        payload={"status": "active"},
        trust_context={"trust_label": "untrusted"},
    )
    assert approved["decision_fingerprint"] == action["decision_fingerprint"]
    with pytest.raises(ValueError, match="action_not_pending"):
        await storage.consume_action(
            "project",
            action_id=action["id"],
            token=action["token"],
            operation="confirm_memory",
            target={"slug": "one"},
            payload={"status": "active"},
            trust_context={"trust_label": "untrusted"},
        )


@pytest.mark.asyncio
async def test_worker_inherits_parent_restrictions():
    task = AgentTask(
        id="worker-permission",
        kind="review",
        permissions=["query_canon"],
        parent_restrictions={"level": "deny"},
    )
    result = await AgentTaskRunner().run(task)
    assert result.error == "permission_denied"
