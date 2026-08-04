"""Regression: WriterToolset schemas must be a subset of the ContextPlan agentic_writer loadout.

根因（U1 大纲引入）：`read_outline` 加进了 `WriterToolset.schemas()`，但未加进
`tool_registry` 的 agentic_writer 路由 → `ContextPlan.validate_request` 视其为
disallowed tool 抛 `PermissionError("context_plan_disallowed_tools:read_outline")`，
经 `safe_error_code` 归一为 `permission_denied` → 每次撰写「本轮执行失败：permission_denied」。

本测试锁死不变量：writer 暴露的每个工具都必须在 agentic_writer loadout 中被允许，
防止今后再有工具 schema/loadout 漂移导致整轮写作被权限拒绝。
"""

from __future__ import annotations

from app.agents.tools import WriterToolset
from app.agents.writing_actions import WritingActionToolset
from app.context_engine.tool_registry import tool_loadout_for_route


def _allowed_writer_tools() -> set[str]:
    return {str(item.get("name")) for item in tool_loadout_for_route("agentic_writer") if item.get("name")}


def test_writer_retrieval_tools_are_all_allowed_in_agentic_writer_loadout():
    allowed = _allowed_writer_tools()
    # 大纲启用时 read_outline 也在 schema 里——必须被 loadout 允许。
    retrieval = WriterToolset("p", None, None, outline_enabled=True)
    exposed = {s["function"]["name"] for s in retrieval.schemas()}
    assert "read_outline" in exposed
    missing = exposed - allowed
    assert not missing, f"writer 检索工具不在 agentic_writer loadout 中（会触发 permission_denied）: {missing}"


def test_full_writing_toolset_is_subset_of_loadout():
    allowed = _allowed_writer_tools()
    retrieval = WriterToolset("p", None, None, outline_enabled=True)
    writing = WritingActionToolset("", retrieval_toolset=retrieval)
    exposed = {s["function"]["name"] for s in writing.schemas()}
    # write_content / edit_lines 也必须在 loadout。
    assert {"write_content", "edit_lines", "read_outline"} <= exposed
    missing = exposed - allowed
    assert not missing, f"写作工具集不在 agentic_writer loadout 中: {missing}"


def test_read_outline_permission_is_allow_not_ask():
    # read_outline 是只读工具，应为 allow（与其它读工具一致），否则后台 actor 下 ask→deny。
    from app.utils.permissions import permission_for

    assert permission_for("read_outline") == "allow"
