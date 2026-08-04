"""U1 大纲（Outline）回归：读写/revision、安全红线（不进事实提取）、AI 查阅与推送、禁用。

安全红线（plan.md §4/§7.2）：大纲是规划意图，永不进入 Canon/Summary 事实提取。
本测试用「结构性隔离」证据锁死——大纲根本不是章节，不进 list_chapters/分析管线。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.control_plane.store import RevisionConflict
from app.services.outline_settings import resolve_outline_settings
from app.storage.outline import OutlineStorage


def test_outline_save_and_read_roundtrip(tmp_path: Path):
    storage = OutlineStorage(str(tmp_path))
    empty = asyncio.run(storage.get_outline("p"))
    assert empty["content"] == "" and empty["exists"] is False and empty["revision"] == 0

    saved = asyncio.run(storage.save_outline("p", "第一卷：主角觉醒。第二卷：反派登场。"))
    assert saved["revision"] == 1 and saved["word_count"] > 0
    reloaded = asyncio.run(storage.get_outline("p"))
    assert reloaded["content"] == "第一卷：主角觉醒。第二卷：反派登场。"
    assert reloaded["exists"] is True and reloaded["revision"] == 1


def test_outline_revision_conflict(tmp_path: Path):
    storage = OutlineStorage(str(tmp_path))
    asyncio.run(storage.save_outline("p", "v1"))  # revision -> 1

    async def _conflict():
        try:
            await storage.save_outline("p", "v2", expected_revision=0)  # stale
            return False
        except RevisionConflict:
            return True

    assert asyncio.run(_conflict()) is True
    # 正确 expected_revision 可写入
    ok = asyncio.run(storage.save_outline("p", "v2", expected_revision=1))
    assert ok["revision"] == 2


def test_outline_is_not_a_chapter_never_enters_fact_pipeline(tmp_path: Path):
    """安全红线：大纲落 outline/ 目录，不是 draft 章节，不进 list_chapters/事实提取。"""
    from app.storage.drafts import DraftStorage

    outline = OutlineStorage(str(tmp_path))
    asyncio.run(outline.save_outline("p", "未来剧情：第十章主角会死而复生（这是规划，不是已发生事实）。"))

    drafts = DraftStorage(str(tmp_path))
    chapters = asyncio.run(drafts.list_chapters("p"))
    # 大纲不产生任何章节；事实提取遍历的是章节，因此结构上不可能把大纲当事实抽取。
    assert chapters == []
    # 大纲文件落在 outline/ 而非 drafts/
    assert (tmp_path / "p" / "outline" / "outline.md").is_file()
    assert not (tmp_path / "p" / "drafts").exists() or not any((tmp_path / "p" / "drafts").glob("*/final.md"))


def test_resolve_outline_settings_global_and_override():
    # 全局默认（config.yaml：enabled=true, require_consult=false）
    base = resolve_outline_settings({})
    assert base["enabled"] is True and base["require_consult"] is False
    # project.yaml 覆盖
    overridden = resolve_outline_settings({"outline": {"enabled": False, "require_consult": True}})
    assert overridden["enabled"] is False and overridden["require_consult"] is True


def test_read_outline_tool_respects_enabled_flag(tmp_path: Path):
    from app.agents.tools import WriterToolset

    outline = OutlineStorage(str(tmp_path))
    asyncio.run(outline.save_outline("p", "大纲：三幕结构。"))

    class _Adapter:
        def __init__(self, outline_storage):
            self.outline = outline_storage

    # 启用：schema 含 read_outline，工具返回大纲内容
    enabled = WriterToolset("p", _Adapter(outline), None, outline_enabled=True)
    assert any(s["function"]["name"] == "read_outline" for s in enabled.schemas())
    result = asyncio.run(enabled.execute("read_outline", {}))
    assert "三幕结构" in result

    # 禁用：schema 不含 read_outline，工具返回已禁用
    disabled = WriterToolset("p", _Adapter(outline), None, outline_enabled=False)
    assert not any(s["function"]["name"] == "read_outline" for s in disabled.schemas())
    assert "禁用" in asyncio.run(disabled.execute("read_outline", {}))


# --------------------------------------------------------- edit_outline 工具 --


class _OutlineAdapter:
    def __init__(self, outline_storage):
        self.outline = outline_storage


def _writer(tmp_path: Path, *, enabled: bool = True):
    from app.agents.tools import WriterToolset

    return WriterToolset("p", _OutlineAdapter(OutlineStorage(str(tmp_path))), None, outline_enabled=enabled)


def test_edit_outline_append_and_replace(tmp_path: Path):
    storage = OutlineStorage(str(tmp_path))
    asyncio.run(storage.save_outline("p", "第一卷：主角觉醒。"))
    toolset = _writer(tmp_path)

    appended = asyncio.run(toolset.execute("edit_outline", {"mode": "append", "content": "第二卷：反派登场。"}))
    assert "已更新大纲" in appended
    assert asyncio.run(storage.get_outline("p"))["content"] == "第一卷：主角觉醒。\n\n第二卷：反派登场。"

    replaced = asyncio.run(toolset.execute("edit_outline", {"mode": "replace", "content": "全新大纲。"}))
    assert "已更新大纲" in replaced
    document = asyncio.run(storage.get_outline("p"))
    assert document["content"] == "全新大纲。" and document["revision"] == 3  # 每次写入都 bump revision


def test_edit_outline_precise_replacement_requires_unique_match(tmp_path: Path):
    storage = OutlineStorage(str(tmp_path))
    asyncio.run(storage.save_outline("p", "第一卷：伏笔 A。\n第二卷：伏笔 A。"))
    toolset = _writer(tmp_path)

    ambiguous = asyncio.run(
        toolset.execute("edit_outline", {"mode": "edit", "old_text": "伏笔 A。", "new_text": "伏笔 B。"})
    )
    assert "不唯一" in ambiguous
    assert "伏笔 B" not in asyncio.run(storage.get_outline("p"))["content"]  # 歧义时不写入

    missing = asyncio.run(
        toolset.execute("edit_outline", {"mode": "edit", "old_text": "不存在的段落", "new_text": "X"})
    )
    assert "未找到" in missing

    ok = asyncio.run(
        toolset.execute(
            "edit_outline", {"mode": "edit", "old_text": "第二卷：伏笔 A。", "new_text": "第二卷：伏笔 B。"}
        )
    )
    assert "已更新大纲" in ok
    assert asyncio.run(storage.get_outline("p"))["content"] == "第一卷：伏笔 A。\n第二卷：伏笔 B。"


def test_edit_outline_rejects_bad_mode_empty_content_and_noop(tmp_path: Path):
    asyncio.run(OutlineStorage(str(tmp_path)).save_outline("p", "原大纲。"))
    toolset = _writer(tmp_path)

    assert "mode 无效" in asyncio.run(toolset.execute("edit_outline", {"mode": "delete_all"}))
    assert "需要非空 content" in asyncio.run(toolset.execute("edit_outline", {"mode": "replace", "content": "  "}))
    assert "需要 old_text" in asyncio.run(toolset.execute("edit_outline", {"mode": "edit", "new_text": "X"}))
    # 内容未变化时不写入（避免无意义 revision bump）
    assert "未发生变化" in asyncio.run(toolset.execute("edit_outline", {"mode": "replace", "content": "原大纲。"}))
    assert asyncio.run(OutlineStorage(str(tmp_path)).get_outline("p"))["revision"] == 1


def test_edit_outline_blocked_when_outline_disabled(tmp_path: Path):
    storage = OutlineStorage(str(tmp_path))
    asyncio.run(storage.save_outline("p", "原大纲。"))
    disabled = _writer(tmp_path, enabled=False)

    assert not any(s["function"]["name"] == "edit_outline" for s in disabled.schemas())
    assert "禁用" in asyncio.run(disabled.execute("edit_outline", {"mode": "replace", "content": "改写"}))
    assert asyncio.run(storage.get_outline("p"))["content"] == "原大纲。"


def test_edit_outline_is_allowed_and_in_writer_loadout():
    """写工具必须在 agentic_writer loadout 中，且 permission=allow（ask 对 agent actor 会降级为 deny）。"""
    from app.context_engine.tool_registry import tool_loadout_for_route
    from app.utils.permissions import permission_for

    assert permission_for("edit_outline") == "allow"
    loadout = {str(item.get("name")): item for item in tool_loadout_for_route("agentic_writer")}
    assert "edit_outline" in loadout
    assert loadout["edit_outline"]["read_only"] is False


def test_edit_outline_refuses_write_when_permission_not_allow(tmp_path: Path, monkeypatch):
    """权限在副作用执行点消费：策略收紧为 deny 时必须拒绝写入，而不是静默落盘。"""
    from app.utils import permissions

    storage = OutlineStorage(str(tmp_path))
    asyncio.run(storage.save_outline("p", "原大纲。"))
    monkeypatch.setitem(permissions.PERMISSION_POLICY, "edit_outline", "deny")

    result = asyncio.run(_writer(tmp_path).execute("edit_outline", {"mode": "replace", "content": "越权改写"}))

    assert "permission_deny" in result
    assert asyncio.run(storage.get_outline("p"))["content"] == "原大纲。"


def test_edit_outline_reports_revision_conflict(tmp_path: Path):
    """并发保护：读到的 revision 过期时交回模型重读，不覆盖作者改动。"""
    storage = OutlineStorage(str(tmp_path))
    asyncio.run(storage.save_outline("p", "原大纲。"))
    toolset = _writer(tmp_path)

    original_get = toolset.adapter.outline.get_outline

    async def _stale_read(project_id: str):
        document = dict(await original_get(project_id))
        document["revision"] = 0  # 模拟读到旧 revision 后作者又改了一次
        return document

    toolset.adapter.outline.get_outline = _stale_read
    result = asyncio.run(toolset.execute("edit_outline", {"mode": "replace", "content": "覆盖写"}))

    assert "outline_revision_conflict" in result
    toolset.adapter.outline.get_outline = original_get
    assert asyncio.run(storage.get_outline("p"))["content"] == "原大纲。"
