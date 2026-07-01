# -*- coding: utf-8 -*-
"""Phase 8 动作② · 近事实常驻回归测试（自审补实现）。

验证：agentic（compact）模式下 facts 仍注入近 N 条作为确定性兜底（resident_facts），
非 compact 模式装配完整 Canon Facts（行为不变）。无网络 / 无 key。
"""

from app.agents.writer import WriterAgent


class _GW:
    def get_model_for_agent(self, agent):
        return "gpt-4o"

    def get_profile_for_agent(self, agent):
        return {"max_tokens": 8000}

    def get_provider_for_agent(self, agent):
        return "fake"

    def get_temperature_for_agent(self, agent):
        return 0.7


def _writer():
    return WriterAgent(_GW(), None, None, None)


def test_resident_facts_injected_in_compact_mode():
    """agentic（compact）模式下，facts 仍注入近 N 条作为兜底（Phase 8 动作② 真正落地）。"""
    msgs = _writer()._build_draft_messages(
        scene_brief={"chapter": "V1C001", "title": "标题", "goal": "目标"},
        target_word_count=1000,
        previous_summaries=[],
        facts=["张三是主角", "李四是反派"],
        working_memory="agentic 写作须知",  # 非空 → 触发 compact 模式
    )
    joined = "\n".join(m.get("content", "") for m in msgs)
    assert "近期关键事实" in joined  # 常驻兜底块
    assert "张三是主角" in joined


def test_full_facts_in_non_compact_mode():
    """非 compact 模式：装配完整 Canon Facts（行为不变）。"""
    msgs = _writer()._build_draft_messages(
        scene_brief={"chapter": "V1C001", "title": "标题", "goal": "目标"},
        target_word_count=1000,
        previous_summaries=[],
        facts=["张三是主角"],
        working_memory=None,  # compact 关闭
    )
    joined = "\n".join(m.get("content", "") for m in msgs)
    assert "Canon Facts" in joined
    assert "张三是主角" in joined
