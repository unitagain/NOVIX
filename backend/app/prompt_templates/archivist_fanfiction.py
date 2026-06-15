"""
Fanfiction-related Archivist prompt templates.
"""

from __future__ import annotations

import json

from .shared import (
    PromptPair,
    P0_MARKER,
    P1_MARKER,
    _json_only_rules,
    smart_truncate,
)
from .archivist_core import get_archivist_system_prompt


def archivist_fanfiction_card_prompt(title: str, content: str, language: str = "zh") -> PromptPair:
    """
    生成同人/百科页面转设定卡的提示词。

    设计目标：
    - 将百科内容转化为写作可用的设定卡
    - 区分 Character 和 World 两种类型
    - 确保描述具体、可用于写作参考
    """
    if language == "en":
        payload = {
            "title": str(title or "").strip(),
            "content": str(content or "").strip()[:42000],
        }
        critical = "\n".join(
            [
                "### Card Extraction Task",
                "",
                "Convert the wiki/encyclopedia page into one writing-ready setting card.",
                "",
                "### Output Schema (strict JSON object)",
                "",
                '{"name": "entity name", "type": "Character|World", "description": "setting description"}',
                "",
                "### Type Rules",
                "",
                "- Character: a person/creature/agent with independent will and recurring behavior logic",
                "- World: a place/organization/object/concept/rule/system used as narrative infrastructure",
                "",
                "### Description Rules (writing-ready, not plot recap)",
                "",
                "[P0-MUST] `description` must be written in English only.",
                "[P0-MUST] Use multi-paragraph formatting, with one blank line between paragraphs.",
                "[P0-MUST] Each paragraph must start with one allowed label:",
                "  Identity: / Alias: / Appearance: / Personality: / Ability: / Relations: / Writing Notes:",
                "[P0-MUST] Minimum labeled paragraphs:",
                "  - Character: Identity + Appearance + Personality + Ability + Relations + Writing Notes (Alias optional)",
                "  - World: Identity + Ability + Relations + Writing Notes",
                "[P0-MUST] Remove citation marks such as [1], [2], [3].",
                "[P0-MUST] Do not output raw infobox credits/cast lists.",
                "[P0-MUST] Rewrite copied spans; do not reproduce long verbatim fragments from source.",
                "[P0-MUST] Do not output labels like Title:/Summary:/Table/RawText.",
                "[P0-MUST] Do not output prompt/meta text and do not use markdown code fences.",
                "",
                "[P1-SHOULD] Character cards follow this order:",
                "  1) Identity (role/faction/duty in canon)",
                "  2) Alias (if evidenced)",
                "  3) Appearance (reusable visual anchors)",
                "  4) Personality (motives/triggers/boundaries)",
                "  5) Ability (power/resources/limits/costs)",
                "  6) Relations (alliances/conflicts/dependencies)",
                "  7) Writing Notes (high-risk canon pitfalls)",
                "",
                "[P1-SHOULD] World cards follow this order:",
                "  1) Identity (definition and category)",
                "  2) Ability (rules/mechanics/operating constraints)",
                "  3) Relations (who/what it affects and conflict points)",
                "  4) Writing Notes (costs/taboos/exceptions that cannot be broken)",
                "",
                "### Formatting Example (layout only, do not copy content)",
                "",
                "Identity: ...",
                "",
                "Appearance: ...",
                "",
                "Personality: ...",
                "",
                "Writing Notes: ...",
                "",
                "### Originality Requirements",
                "",
                "[P0-MUST] Avoid plot retelling; output reusable setting constraints for writing.",
                "[P0-MUST] If evidence is missing, explicitly mark uncertainty; do not invent facts.",
                "[P1-SHOULD] Prefer high-density, reusable constraints. If evidence exists, target 120-220 words total.",
            ]
        )
        user = "\n".join(
            [
                critical,
                "",
                "### Page Payload",
                "",
                "<<<PAGE_START>>>",
                json.dumps(payload, ensure_ascii=False),
                "<<<PAGE_END>>>",
                "",
                _json_only_rules("Output must be a JSON object (not an array).", language=language),
                "",
                "### Start Output",
                "Output JSON object directly:",
                "",
                "-" * 40,
                "[Constraint Repeat - U-shaped Attention]",
                critical,
            ]
        )
        return PromptPair(system=get_archivist_system_prompt(language=language), user=user)
    critical = "\n".join(
        [
            "### 设定卡生成任务",
            "",
            "将百科/词条页面转换为「写作用设定卡」",
            "",
            "### 输出 Schema（严格 JSON 对象）",
            "",
            "```json",
            '{"name": "实体名称", "type": "Character|World", "description": "设定描述"}',
            "```",
            "",
            "### type 分类规则",
            "",
            "| type | 适用范围 |",
            "|------|---------|",
            "| Character | 人物、生物、有独立意志的个体 |",
            "| World | 地点、组织、物件、概念、规则、种族、体系等 |",
            "",
            "### description 写作规范（尽可能详细，优先可写作复现；仅排版更清晰，不改变信息）",
            "",
            f"{P0_MARKER} 排版要求：必须分段，段与段之间空一行；每段以“字段名：”开头（例如“身份定位：...”）。",
            f"{P0_MARKER} 字段名只能使用以下之一：身份定位、别名称呼、外貌特征、性格动机、能力限制、关键关系、写作注意。",
            "",
            f"{P1_MARKER} Character 类型写作顺序：",
            "  1. 身份与定位（在作品中的角色、阵营、职责）",
            "  2. 别名/称呼/头衔（如有）",
            "  3. 外貌特征（可复现的关键视觉点；如无证据则写“信息不足”）",
            "  4. 性格/行为模式/动机（触发点、底线、习惯性反应）",
            "  5. 能力/资源/限制（代价、禁忌、弱点；如有）",
            "  6. 关键关系与冲突点（与谁因何相连/相斥）",
            "  7. 写作注意事项（容易写错的点；必须避免的误设定）",
            "",
            f"{P1_MARKER} World 类型写作顺序：",
            "  1. 定义与类别（是什么、用于什么）",
            "  2. 关键规则/约束（必须遵守什么；边界是什么）",
            "  3. 代价/禁忌/例外（如有）",
            "  4. 典型要素清单（地理/组织结构/仪式/技术/名词体系等，按页面证据）",
            "  5. 常见使用方式/影响范围（在叙事中怎么用、会造成什么后果）",
            "  6. 写作注意事项（不可随意改动的设定点）",
            "",
            "### description 排版示例（仅示例排版，不要照抄内容）",
            "",
            "身份定位：……",
            "",
            "外貌特征：……",
            "",
            "性格动机：……",
            "",
            "写作注意：……",
            "",
            "### 原创性要求",
            "",
            f"{P0_MARKER} 禁止剧情复述：只写设定画像",
            f"{P0_MARKER} 禁止抄袭原文：任意 12 字以上连续片段必须改写",
            f"{P0_MARKER} 禁止输出标签字样：Title:/Summary:/Table/RawText",
        ]
    )
    payload = {
        "title": str(title or "").strip(),
        "content": str(content or "").strip()[:42000],
    }
    user = "\n".join(
        [
            critical,
            "",
            "### 页面内容",
            "",
            "<<<PAGE_START>>>",
            json.dumps(payload, ensure_ascii=False),
            "<<<PAGE_END>>>",
            "",
            _json_only_rules("输出必须是 JSON 对象（不是数组）"),
            "",
            "### 开始输出",
            "请直接输出 JSON 对象：",
            "",
            "─" * 40,
            "【任务要求重复】",
            critical,
        ]
    )
    return PromptPair(system=get_archivist_system_prompt(language=language), user=user)


def archivist_fanfiction_card_repair_prompt(
    title: str, content: str, hint: str = "", language: str = "zh"
) -> PromptPair:
    """
    生成设定卡修复提示词。

    用于修复格式不正确或内容不完整的设定卡。
    """
    if language == "en":
        extra_hint = f"\nAdditional hint: {hint}\n" if hint else ""
        critical = "\n".join(
            [
                "### Card Repair Task",
                "",
                "Repair the extraction result into a strict, writing-ready JSON object.",
                "",
                "### Output Schema",
                "",
                '{"name": "...", "type": "Character|World", "description": "..."}',
                "",
                "### Rules",
                "",
                "[P0-MUST] JSON only, no extra text.",
                "[P0-MUST] type must be Character or World.",
                "[P0-MUST] description must be written in English only.",
                "[P0-MUST] description must use multi-paragraph formatting with one blank line between paragraphs.",
                "[P0-MUST] Each paragraph must start with one allowed label:",
                "  Identity: / Alias: / Appearance: / Personality: / Ability: / Relations: / Writing Notes:",
                "[P0-MUST] Minimum labeled paragraphs:",
                "  - Character: Identity + Appearance + Personality + Ability + Relations + Writing Notes (Alias optional)",
                "  - World: Identity + Ability + Relations + Writing Notes",
                "[P0-MUST] description must not contain citation marks like [1], [2], [3].",
                "[P0-MUST] description must not be raw credits/cast list text.",
                "[P0-MUST] Do not output Title:/Summary:/Table/RawText tags.",
                "[P0-MUST] Rewrite copied long fragments from source; no long verbatim spans.",
                "",
                "[P1-SHOULD] Character cards prioritize: identity -> alias -> appearance -> personality -> ability -> relations -> writing notes.",
                "[P1-SHOULD] World cards prioritize: identity -> ability (rules/limits) -> relations (impact scope) -> writing notes.",
                "[P1-SHOULD] Keep statements concrete and reusable for drafting, instead of plot recap.",
                "[P1-SHOULD] If evidence exists, target 120-220 words total; prefer constraints over trivia.",
                "",
                "### Formatting Example (layout only)",
                "",
                "Identity: ...",
                "",
                "Ability: ...",
                "",
                "Writing Notes: ...",
            ]
        )
        user = "\n".join(
            [
                critical,
                extra_hint.strip() if extra_hint.strip() else "",
                "",
                f"page_title: {str(title or '').strip()}",
                "",
                "### Page Content",
                "",
                "<<<PAGE_START>>>",
                smart_truncate(str(content or ""), max_chars=24000),
                "<<<PAGE_END>>>",
                "",
                _json_only_rules("Output must be a JSON object (not an array).", language=language),
                "",
                "### Start Output",
                "Output repaired JSON object directly:",
                "",
                "-" * 40,
                "[Constraint Repeat - U-shaped Attention]",
                critical,
            ]
        )
        return PromptPair(system=get_archivist_system_prompt(language=language), user=user)
    critical = "\n".join(
        [
            "### 设定卡修复任务",
            "",
            "将页面内容修复为严格格式的 JSON 对象",
            "",
            "### 输出 Schema",
            "",
            '{"name": "...", "type": "Character|World", "description": "..."}',
            "",
            "### 修复规则",
            "",
            f"{P0_MARKER} 格式要求：",
            "  - 仅输出 JSON（无多余文本）",
            "  - type 只能是 Character 或 World",
            "",
            f"{P0_MARKER} description 要求：",
            "  - 中文 3-6 句",
            "  - 覆盖：身份/外貌（如有）/性格/能力或限制/角色功能",
            "",
            f"{P0_MARKER} 原创性：",
            "  - 禁止输出 Title:/Summary:/Table/RawText 等标签",
            "  - 禁止复用 12 字以上连续片段（必须改写）",
        ]
    )
    extra_hint = f"\n**额外提示**：{hint}\n" if hint else ""
    user = "\n".join(
        [
            critical,
            extra_hint.strip() if extra_hint.strip() else "",
            "",
            f"**页面标题**：{str(title or '').strip()}",
            "",
            "### 页面内容",
            "",
            "<<<PAGE_START>>>",
            smart_truncate(str(content or ""), max_chars=24000),
            "<<<PAGE_END>>>",
            "",
            _json_only_rules("输出必须是 JSON 对象（不是数组）"),
            "",
            "### 开始输出",
            "请直接输出修复后的 JSON 对象：",
            "",
            "─" * 40,
            "【修复规则重复】",
            critical,
        ]
    )
    return PromptPair(system=get_archivist_system_prompt(language=language), user=user)
