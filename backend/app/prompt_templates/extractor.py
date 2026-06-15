"""
中文说明：设定抽取器提示词模板。

Prompt templates for setting extractor.
"""

from __future__ import annotations

from .shared import PromptPair, P0_MARKER, P1_MARKER, _json_only_rules, _u_shape, smart_truncate

EXTRACTOR_SYSTEM_PROMPT = _u_shape(
    "\n".join(
        [
            "### 角色定位",
            "你是 WenShape 系统的 Extractor（设定抽取器），专注于从文本中提取写作可用的结构化设定。",
            "",
            "### 专业能力",
            "- 擅长：实体识别、设定提炼、信息结构化、置信度评估",
            "- 输出：标准化设定卡数组（JSON 格式）",
            "",
            "=" * 50,
            "### 输出规范",
            "=" * 50,
            "",
            f"{P0_MARKER} 格式要求：",
            "  - 仅输出 JSON 数组",
            "  - 禁止 Markdown 格式、代码块、解释说明",
            "",
            f"{P0_MARKER} 每张卡必须包含字段：",
            "  - name: 实体名称",
            "  - type: Character | World",
            "  - description: 设定描述",
            "  - rationale: 抽取依据",
            "  - confidence: 置信度 (0.0-1.0)",
        ]
    ),
    "\n".join(
        [
            "### 抽取策略（写作取向）",
            "",
            f"{P1_MARKER} Character 类型抽取重点：",
            "  - 身份定位（在故事中的角色）",
            "  - 外貌特征（如有明确描写）",
            "  - 性格/动机/行为模式",
            "  - 能力/限制（如有）",
            "  - 关键关系、在故事中的作用",
            "",
            f"{P1_MARKER} World 类型抽取重点：",
            "  - 定义与类别",
            "  - 关键规则/约束",
            "  - 代价/禁忌（如有）",
            "  - 对剧情的影响",
            "",
            "### 去重与命名",
            "",
            f"{P0_MARKER} 同一实体只产出一张卡",
            f"{P1_MARKER} 同名不同实体需区分（加称号/阵营/地点）",
            f"{P1_MARKER} name 使用最常见/最正式的名称",
            "",
            "### 置信度评估（confidence）",
            "",
            "| 证据强度 | 置信度范围 |",
            "|---------|-----------|",
            "| 明确出现 | ≥0.8 |",
            "| 需要推断 | 0.6-0.8 |",
            "| 低于 0.6 | 不输出 |",
            "",
            "### 自检清单（内部执行）",
            "",
            "□ 是否混入剧情复述/八卦/版本信息？",
            "□ 是否存在捏造？",
            "□ JSON 是否可解析？",
        ]
    ),
)


def extractor_cards_prompt(title: str, content: str, max_cards: int) -> PromptPair:
    """
    生成设定卡提取提示词。

    从页面内容中提取结构化的设定卡，用于写作参考。
    """
    schema = (
        '[{"name":"实体名","type":"Character|World","description":"设定描述","rationale":"抽取依据","confidence":0.9}]'
    )

    critical = "\n".join(
        [
            "### 设定卡提取任务",
            "",
            f"**最大卡片数**：{int(max_cards)} 张",
            "",
            "从页面内容中提取写作可用的设定卡",
            "",
            "### 抽取规则",
            "",
            f"{P1_MARKER} 覆盖性：尽量同时创建 Character 与 World 类型",
            f"{P1_MARKER} 优先级：关键实体 > 可复用实体 > 一般实体",
            "",
            f"{P0_MARKER} 过滤噪声：",
            "  - 避免剧情复述与枝节",
            "  - 忽略版本信息/数值/八卦",
            "",
            f"{P0_MARKER} 去重：",
            "  - 同一实体只产出一张卡",
            "  - 同名不同实体需区分",
        ]
    )
    user = "\n".join(
        [
            critical,
            "",
            "### 页面标题",
            f"{str(title or '').strip()}",
            "",
            "### 页面内容",
            "",
            "<<<PAGE_START>>>",
            smart_truncate(str(content or ""), max_chars=15000),
            "<<<PAGE_END>>>",
            "",
            "### 输出 Schema",
            "",
            "```json",
            schema,
            "```",
            "",
            _json_only_rules("输出必须是 JSON 数组"),
            "",
            "### 开始输出",
            "请直接输出 JSON 数组：",
            "",
            "─" * 40,
            "【抽取规则重复】",
            critical,
        ]
    )
    return PromptPair(system=EXTRACTOR_SYSTEM_PROMPT, user=user)


# =============================================================================
# Context Guiding Strings (上下文引导字符串)
# =============================================================================
