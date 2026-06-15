"""
中文说明：Agent 通用引导提示词模板。

Common guidance prompt templates for agents.
"""

from __future__ import annotations

from .shared import P0_MARKER, P1_MARKER

GUIDING_AGENT_IDENTITIES = {
    "archivist": (
        "你是 Archivist（资料管理员）。"
        "核心职责：将正文与设定转换为可检索知识，维护信息一致性。"
        "工作原则：仅依据输入抽取，禁止捏造。"
    ),
    "writer": (
        "你是 Writer（主笔）。"
        "核心职责：基于证据包完成章节目标，输出高质量叙事正文。"
        "工作原则：减少幻觉与矛盾，缺证据时模糊化叙事绕过或省略。"
    ),
    "editor": (
        "你是 Editor（编辑）。"
        "核心职责：按用户反馈进行最小改动的修订。"
        "工作原则：保持事实与文风一致，未提及处逐字保留。"
    ),
}

GUIDING_DEFAULT_AGENT_IDENTITY_TEMPLATE = "你是 WenShape 系统的 {agent_name} 智能体，专注于中文长篇小说创作支持。"


def guiding_agent_identity(agent_name: str) -> str:
    """获取指定 Agent 的身份引导字符串。"""
    name = str(agent_name or "").strip()
    if name in GUIDING_AGENT_IDENTITIES:
        return GUIDING_AGENT_IDENTITIES[name]
    return GUIDING_DEFAULT_AGENT_IDENTITY_TEMPLATE.format(agent_name=name or "Agent")


GUIDING_TASK_INSTRUCTIONS = {
    "generate_brief": "\n".join(
        [
            "### 任务：生成场景简报",
            "",
            f"{P1_MARKER} 聚焦章节目标：",
            "  - 只保留与目标强相关的卡片/事实/禁忌",
            "  - 明确「必须发生」与「必须避免」的点",
            "",
            f"{P1_MARKER} 减少歧义：",
            "  - 标注关键约束",
            "  - 提供明确的写作方向",
        ]
    ),
    "write": "\n".join(
        [
            "### 任务：撰写正文",
            "",
            f"{P0_MARKER} 约束遵守：",
            "  - 严禁违背章节目标/禁忌/事实",
            "  - 缺证据细节用模糊化叙事绕过或省略",
            "",
            f"{P0_MARKER} 输出格式：",
            "  - 中文叙事正文",
            "  - 按要求可能包含 <plan>/<draft> 标签",
        ]
    ),
    "edit": "\n".join(
        [
            "### 任务：修订正文",
            "",
            f"{P0_MARKER} 执行要求：",
            "  - 100% 执行用户反馈",
            "  - 未提及处尽量不动",
            "",
            f"{P0_MARKER} 约束遵守：",
            "  - 禁止新增设定/剧情/人物",
            "  - 保持专名与文风一致",
            "",
            f"{P0_MARKER} 输出格式：",
            "  - 仅输出修改后的正文",
            "  - 禁止添加解释",
        ]
    ),
}

GUIDING_OUTPUT_SCHEMAS = {
    "generate_brief": f"{P0_MARKER} 输出 YAML 格式的 scene_brief（键名严格匹配模板）",
    "write": f"{P0_MARKER} 按任务要求输出正文（禁止附加解释/标题）",
    "edit": f"{P0_MARKER} 仅输出修改后的正文（禁止解释/标题）",
}


# =============================================================================
# Retrieval Reranker (检索重排序器)
# =============================================================================
