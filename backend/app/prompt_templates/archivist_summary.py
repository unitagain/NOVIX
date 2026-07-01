"""
Summary-related Archivist prompt templates.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .shared import (
    PromptPair,
    P0_MARKER,
    P1_MARKER,
    _yaml_only_rules,
    smart_truncate,
)

from .archivist_core import get_archivist_system_prompt


def archivist_canon_updates_prompt(chapter: str, final_draft: str, language: str = "zh") -> PromptPair:
    """
    生成事实更新提取提示词。

    从最终稿中提取可落库的结构化信息：
    - 事实 (facts)
    - 时间线事件 (timeline_events)
    - 角色状态 (character_states)
    """
    if language == "en":
        schema = "\n".join(
            [
                "facts:",
                "  - statement: <atomic factual statement>",
                "    confidence: <0.0-1.0>",
                "    context: <=20 chars situating prefix: scene/who/cause, for retrieval only>",
                "timeline_events:",
                "  - time: <time expression>",
                "    event: <what happened>",
                "    participants: [<character1>, <character2>]",
                "    location: <location>",
                "character_states:",
                "  - character: <character name>",
                "    goals: [<goal1>]",
                "    injuries: [<injury1>]",
                "    inventory: [<item1>]",
                "    relationships: { <other>: <relation change> }",
                "    location: <current location>",
                "    emotional_state: <emotion>",
                "relations:",
                "  - subject: <subject entity>",
                "    relation: <relation type, e.g. enemy/ally/mentor/kin/loyalty>",
                "    object: <object entity>",
                "    change: <optional: evolution, e.g. ally->enemy>",
            ]
        )
        critical = "\n".join(
            [
                "### Canon Update Extraction Task",
                "",
                f"chapter: {chapter}",
                "",
                "Extract structured updates for facts/timeline/character_states/relations from final draft.",
                "",
                "### Extraction Rules",
                "",
                "[P0-MUST] Anti-hallucination: extract only directly supported information.",
                "[P0-MUST] Language: all output text must be in English (no Chinese).",
                '[P0-MUST] Keep uncertain fields empty ([] or "").',
                "[P1-SHOULD] facts: prefer reusable, high-constraint facts over trivia.",
                "[P1-SHOULD] facts.context: a <=20-char prefix situating the fact (scene/who/cause)",
                "  for retrieval recall (Contextual Retrieval); do not restate the statement; empty if unsure.",
                "[P1-SHOULD] Contradiction handling (Last-Write-Wins):",
                "  - If this chapter's events invalidate a prior fact (relationship change, status change,",
                "    rule broken), simply extract the new fact — no need to reference or negate the old one.",
                "  - The system automatically prioritizes the most recent fact during context retrieval.",
                "[P1-SHOULD] timeline_events: key event nodes only.",
                "[P1-SHOULD] character_states: focus on major characters only.",
                "[P1-SHOULD] relations (0-8): typed entity relations established/changed this chapter",
                "  (the relation-graph edges); subject/relation/object required, evolution in change.",
            ]
        )
        user = "\n".join(
            [
                critical,
                "",
                "### Output Schema (strict YAML)",
                "",
                "```yaml",
                schema,
                "```",
                "",
                "### Draft Content",
                "",
                "<<<DRAFT_START>>>",
                str(final_draft or ""),
                "<<<DRAFT_END>>>",
                "",
                _yaml_only_rules(language=language),
                "",
                "### Start Output",
                "Output YAML directly (strict schema match):",
                "",
                "─" * 40,
                "[Schema Repeated - U-shaped Attention]",
                "```yaml",
                schema,
                "```",
            ]
        )
        return PromptPair(system=get_archivist_system_prompt(language=language), user=user)
    schema = "\n".join(
        [
            "facts:",
            "  - statement: <客观事实，精炼句子>",
            "    confidence: <0.0-1.0>",
            "    context: <≤20字情境定位：场景/涉及者/因果，仅用于检索，勿重复 statement>",
            "timeline_events:",
            "  - time: <时间描述>",
            "    event: <发生了什么>",
            "    participants: [<角色1>, <角色2>]",
            "    location: <地点>",
            "character_states:",
            "  - character: <角色名>",
            "    goals: [<目标1>]",
            "    injuries: [<伤势1>]",
            "    inventory: [<物品1>]",
            "    relationships: { <他人>: <关系描述> }",
            "    location: <当前位置>",
            "    emotional_state: <情绪>",
            "relations:",
            "  - subject: <主体实体>",
            "    relation: <关系类型，如 敌对/盟友/师承/亲属/效忠>",
            "    object: <客体实体>",
            "    change: <可选：关系演变，如 盟友→敌对>",
        ]
    )
    critical = "\n".join(
        [
            "### 事实提取任务",
            "",
            f"**章节**：{chapter}",
            "",
            "从最终稿中提取可落库的「事实 / 时间线 / 角色状态」更新",
            "",
            "### 输出 Schema（严格 YAML）",
            "",
            "```yaml",
            schema,
            "```",
            "",
            "### 抽取规范",
            "",
            f"{P0_MARKER} 反幻觉原则：",
            "  - 只提取正文中可直接推断的客观信息",
            "  - 推测不能当事实写入",
            "  - 不确定的字段留空或用空列表",
            "",
            f"{P1_MARKER} facts（建议 3-5 条，可少于 3；宁缺毋滥）：",
            "  - 只输出对后文有约束力/可反复检索复用的高价值事实；不要为了凑满数量输出常识、重复句或标题改写",
            "  - 每条是一个原子事实（单句），包含明确实体与可检索要素（人物/地点/物品/规则/行为边界/心理动机）",
            "  - 选题优先级：规则/禁忌/代价/承诺 > 关系边界与动机 > 关键资源与环境 > 事件节点",
            "  - 维度覆盖：尽量让 3-5 条覆盖不同维度；避免 5 条都在描述同一类”亲属/年龄/身份”",
            "  - 反例（不要写）：『A 是 B 的母亲/儿子』『A 住在某地』（除非本章首次披露且对后续有戏剧性约束/冲突）",
            "",
            f"{P1_MARKER} 矛盾处理（Last-Write-Wins / 最新为准）：",
            "  - 如果本章事件导致某个先前事实不再成立（如角色关系变化、状态改变、规则被打破），",
            "    直接提取新事实即可，不需要引用或否定旧事实",
            "  - 系统会自动以最新事实为准进行上下文检索",
            "",
            f"{P1_MARKER} timeline_events（建议 0-5 条）：",
            "  - 每条是一个关键事件节点",
            "  - participants 只写正文明确出现的角色名",
            "  - location/time 不确定则留空",
            "",
            f"{P1_MARKER} character_states（建议 ≤5 个角色）：",
            "  - 只写主要人物",
            "  - relationships 格式：{对方: 关系变化}",
            "  - 不确定用空对象 {}",
            "",
            f"{P1_MARKER} 每条 fact 附 context（情境前缀，用于检索召回，对标 Contextual Retrieval）：",
            "  - 一句≤20字，定位该事实的场景/涉及人物/因果（如「迷雾森林·张三揭穿李四后」）",
            "  - 只为帮助日后语义检索定位，不要复述 statement 本身；难以定位则留空",
            "",
            f"{P1_MARKER} relations（0-8 条，关系图的边，用于回答『谁与谁的恩怨/关系网』）：",
            "  - 抽取本章【确立或改变】的实体间有类型关系（人物↔人物/势力/地点）",
            "  - subject/relation/object 必填且为正文出现的实体；关系演变写到 change",
            "  - 与 character_states.relationships 可重叠；这里要的是规范化三元组",
        ]
    )
    user = "\n".join(
        [
            critical,
            "",
            "### 正文内容",
            "",
            "<<<DRAFT_START>>>",
            str(final_draft or ""),
            "<<<DRAFT_END>>>",
            "",
            _yaml_only_rules(),
            "",
            "### 开始输出",
            "请直接输出 YAML（严格匹配 schema）：",
            "",
            "─" * 40,
            "【Schema 重复 - U-shaped Attention】",
            "```yaml",
            schema,
            "```",
        ]
    )
    return PromptPair(system=get_archivist_system_prompt(language=language), user=user)


def archivist_chapter_summary_prompt(
    chapter: str, chapter_title: str, final_draft: str, language: str = "zh"
) -> PromptPair:
    """
    生成章节摘要提示词。

    输出结构化的章节摘要，用于后续检索与写作参考。
    """
    if language == "en":
        schema = "\n".join(
            [
                f"chapter: {chapter}",
                f"title: {chapter_title}",
                "word_count: <int>",
                "key_events:",
                "  - <event1>",
                "new_facts:",
                "  - <fact1>",
                "character_state_changes:",
                "  - <change1>",
                "open_loops:",
                "  - <loop1>",
                "brief_summary: <one-paragraph summary>",
            ]
        )
        critical = "\n".join(
            [
                "### Chapter Summary Task",
                "",
                f"chapter: {chapter}",
                f"title: {chapter_title}",
                "",
                "Generate a structured chapter summary for retrieval and future writing.",
                "",
                "### Field Rules",
                "",
                "[P1-SHOULD] key_events: 3-5 objective event nodes in order.",
                "[P1-SHOULD] new_facts: 3-5 reusable facts with downstream constraints.",
                "[P1-SHOULD] character_state_changes: 1-4 major state changes.",
                "[P1-SHOULD] open_loops: 1-3 unresolved hooks/questions.",
                "[P1-SHOULD] brief_summary: one paragraph (~80-180 words).",
                "[P0-MUST] No new names/events/places not present in the draft.",
            ]
        )
        user = "\n".join(
            [
                critical,
                "",
                "### Output Schema (strict YAML)",
                "",
                "```yaml",
                schema,
                "```",
                "",
                "### Draft Content",
                "",
                "<<<DRAFT_START>>>",
                str(final_draft or ""),
                "<<<DRAFT_END>>>",
                "",
                _yaml_only_rules(language=language),
                "",
                "### Start Output",
                "Output YAML directly (strict schema match):",
                "",
                "─" * 40,
                "[Schema Repeated - U-shaped Attention]",
                "```yaml",
                schema,
                "```",
            ]
        )
        return PromptPair(system=get_archivist_system_prompt(language=language), user=user)
    schema = "\n".join(
        [
            f"chapter: {chapter}",
            f"title: {chapter_title}",
            "word_count: <int>",
            "key_events:",
            "  - <event1>",
            "new_facts:",
            "  - <fact1>",
            "character_state_changes:",
            "  - <change1>",
            "open_loops:",
            "  - <loop1>",
            "brief_summary: <一段话摘要>",
        ]
    )
    critical = "\n".join(
        [
            "### 章节摘要生成任务",
            "",
            f"**章节**：{chapter}",
            f"**标题**：{chapter_title}",
            "",
            "生成结构化的「事实摘要」，用于后续检索与写作",
            "",
            "### 各字段内容要求",
            "",
            f"{P1_MARKER} key_events（3-5 条）：",
            "  - 客观剧情节点，按发生顺序排列",
            "  - 每条一句话",
            "",
            f"{P1_MARKER} new_facts（3-5 条）：",
            "  - 对后文有约束力的新事实/新设定/关键心理事实",
            "  - 必须可从正文直接推断",
            "",
            f"{P1_MARKER} character_state_changes（1-4 条）：",
            "  - 聚焦主要人物",
            "  - 具体到「变化点」：动机/情绪/关系/目标变化",
            "",
            f"{P1_MARKER} open_loops（1-3 条）：",
            "  - 未解决的悬念/伏笔/待确认问题",
            "",
            f"{P1_MARKER} brief_summary（80-180 字）：",
            "  - 一段话概括剧情与重要心理推进",
            "  - 禁止加入推测",
            "",
            f"{P0_MARKER} 反幻觉：禁止引入正文未出现的新名字/新事件/新地点",
        ]
    )
    user = "\n".join(
        [
            critical,
            "",
            "### 输出 Schema（严格 YAML）",
            "",
            "```yaml",
            schema,
            "```",
            "",
            "### 正文内容",
            "",
            "<<<DRAFT_START>>>",
            str(final_draft or ""),
            "<<<DRAFT_END>>>",
            "",
            _yaml_only_rules(),
            "",
            "### 开始输出",
            "请直接输出 YAML（严格匹配 schema）：",
            "",
            "─" * 40,
            "【Schema 重复 - U-shaped Attention】",
            "```yaml",
            schema,
            "```",
        ]
    )
    return PromptPair(system=get_archivist_system_prompt(language=language), user=user)


def archivist_focus_characters_binding_prompt(
    chapter: str, candidates: List[Dict[str, Any]], final_draft: str, limit: int = 5, language: str = "zh"
) -> PromptPair:
    """
    生成重点角色绑定提示词。

    用于识别章节中的核心角色，便于后续检索和 UI 展示。
    """
    if language == "en":
        candidate_lines = []
        for item in candidates or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            stars = item.get("stars")
            aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
            alias_text = ", ".join(aliases[:6])
            stars_text = str(int(stars)) if stars is not None else "1"
            if alias_text:
                candidate_lines.append(f"- {name} | stars: {stars_text} | aliases: {alias_text}")
            else:
                candidate_lines.append(f"- {name} | stars: {stars_text}")
        candidates_block = "\n".join(candidate_lines) if candidate_lines else "- (no candidates)"
        schema = "\n".join(
            [
                "focus_characters:",
                "  - <character name from candidate list>",
            ]
        )
        critical = "\n".join(
            [
                "### Focus Character Binding Task",
                "",
                f"chapter: {chapter}",
                "",
                "Select focus characters for retrieval and UI display.",
                "",
                "### Selection Rules",
                "",
                f"[P0-MUST] Return at most {int(limit)} characters.",
                "[P0-MUST] Choose only from candidate list; names must match exactly.",
                "[P0-MUST] Character name or alias must explicitly appear in draft.",
                "[P0-MUST] If none appears, return an empty list.",
                "[P1-SHOULD] Priority: narrative center > plot-driving roles > minor appearances.",
                "[P1-SHOULD] Higher stars have higher priority when ties exist.",
            ]
        )
        user = "\n".join(
            [
                critical,
                "",
                "### Output Schema (strict YAML)",
                "",
                "```yaml",
                schema,
                "```",
                "",
                "### Candidate Characters (choose only from this list)",
                "",
                "<<<CANDIDATES_START>>>",
                candidates_block,
                "<<<CANDIDATES_END>>>",
                "",
                "### Draft Content",
                "",
                "<<<DRAFT_START>>>",
                smart_truncate(str(final_draft or ""), max_chars=24000),
                "<<<DRAFT_END>>>",
                "",
                _yaml_only_rules(language=language),
                "",
                "### Start Output",
                "Output YAML directly:",
                "",
                "─" * 40,
                "[Schema Repeated]",
                "```yaml",
                schema,
                "```",
            ]
        )
        return PromptPair(system=get_archivist_system_prompt(language=language), user=user)
    # 构建候选角色列表
    candidate_lines = []
    for item in candidates or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        stars = item.get("stars")
        aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
        alias_text = "、".join(aliases[:6])
        stars_text = str(int(stars)) if stars is not None else "1"
        if alias_text:
            candidate_lines.append(f"- {name}｜{stars_text}星｜别名：{alias_text}")
        else:
            candidate_lines.append(f"- {name}｜{stars_text}星")

    candidates_block = "\n".join(candidate_lines) if candidate_lines else "- （无候选角色）"

    schema = "\n".join(
        [
            "focus_characters:",
            "  - <角色名（必须来自候选列表）>",
        ]
    )
    critical = "\n".join(
        [
            "### 重点角色绑定任务",
            "",
            f"**章节**：{chapter}",
            "",
            "为本章生成「重点角色绑定」，用于检索与 UI 展示",
            "",
            "### 选择规则",
            "",
            f"{P0_MARKER} 数量限制：最多 {int(limit)} 个角色",
            "",
            f"{P0_MARKER} 来源限制：",
            "  - 只能从【候选角色列表】中选择",
            "  - 必须精确匹配角色名（与候选一致）",
            "",
            f"{P0_MARKER} 出现验证：",
            "  - 角色的姓名或别名必须在正文中明确出现",
            "  - 禁止「隐式主角」：未被提及的角色不绑定",
            "  - 若无任何候选角色被提及，返回空列表",
            "",
            f"{P1_MARKER} 优先级排序：",
            "  - 叙事中心 / 视角人物 > 驱动剧情的角色 > 出场角色",
            "  - 重要度：三星 > 二星 > 一星",
        ]
    )
    user = "\n".join(
        [
            critical,
            "",
            "### 输出 Schema（严格 YAML）",
            "",
            "```yaml",
            schema,
            "```",
            "",
            "### 候选角色列表（只能从此列表选择）",
            "",
            "<<<CANDIDATES_START>>>",
            candidates_block,
            "<<<CANDIDATES_END>>>",
            "",
            "### 正文内容",
            "",
            "<<<DRAFT_START>>>",
            smart_truncate(str(final_draft or ""), max_chars=24000),
            "<<<DRAFT_END>>>",
            "",
            _yaml_only_rules(),
            "",
            "### 开始输出",
            "请直接输出 YAML：",
            "",
            "─" * 40,
            "【Schema 重复】",
            "```yaml",
            schema,
            "```",
        ]
    )
    return PromptPair(system=get_archivist_system_prompt(language=language), user=user)


def archivist_volume_summary_prompt(
    volume_id: str, chapter_items: List[Dict[str, Any]], language: str = "zh"
) -> PromptPair:
    """
    生成卷摘要提示词。

    根据章节摘要列表生成结构化的卷级摘要。
    """
    if language == "en":
        schema = "\n".join(
            [
                f"volume_id: {volume_id}",
                "brief_summary: <one paragraph linking core arc and major turns>",
                "key_themes:",
                "  - <theme word or phrase>",
                "major_events:",
                "  - <major event node>",
                f"chapter_count: {len(chapter_items)}",
            ]
        )
        critical = "\n".join(
            [
                "### Volume Summary Task",
                "",
                f"volume_id: {volume_id}",
                f"chapter_count: {len(chapter_items)}",
                "",
                "Generate a structured volume-level summary from chapter summaries.",
                "",
                "### Field Rules",
                "",
                "[P1-SHOULD] brief_summary: one coherent paragraph, not a bullet catalog.",
                "[P1-SHOULD] key_themes: 3-6 concise theme phrases.",
                "[P1-SHOULD] major_events: 3-8 major nodes in chronological order.",
                "[P0-MUST] Anti-hallucination: use only input chapter summaries.",
            ]
        )
        user = "\n".join(
            [
                critical,
                "",
                "### Input Chapter Summaries (JSON)",
                "",
                "<<<CHAPTERS_JSON_START>>>",
                json.dumps(chapter_items, ensure_ascii=False),
                "<<<CHAPTERS_JSON_END>>>",
                "",
                "### Output Schema (strict YAML)",
                "",
                "```yaml",
                schema,
                "```",
                "",
                _yaml_only_rules(language=language),
                "",
                "### Start Output",
                "Output YAML directly:",
                "",
                "─" * 40,
                "[Schema Repeated]",
                "```yaml",
                schema,
                "```",
            ]
        )
        return PromptPair(system=get_archivist_system_prompt(language=language), user=user)
    schema = "\n".join(
        [
            f"volume_id: {volume_id}",
            "brief_summary: <一段话串联卷内主线与关键转折>",
            "key_themes:",
            "  - <主题词/短语>",
            "major_events:",
            "  - <关键事件节点>",
            f"chapter_count: {len(chapter_items)}",
        ]
    )
    critical = "\n".join(
        [
            "### 卷摘要生成任务",
            "",
            f"**卷 ID**：{volume_id}",
            f"**章节数**：{len(chapter_items)}",
            "",
            "根据章节摘要列表生成结构化「卷摘要」",
            "",
            "### 各字段内容要求",
            "",
            f"{P1_MARKER} brief_summary：",
            "  - 一段话串联卷内主线与关键转折",
            "  - 不要写成目录式列举",
            "",
            f"{P1_MARKER} key_themes（3-6 个）：",
            "  - 主题词/短语（中文）",
            "  - 例如：代价、禁忌、身份暴露、救赎、背叛",
            "",
            f"{P1_MARKER} major_events（3-8 条）：",
            "  - 关键事件节点",
            "  - 按卷内顺序排列",
            "",
            f"{P0_MARKER} 反幻觉：只基于输入的章节摘要，禁止编造",
        ]
    )
    user = "\n".join(
        [
            critical,
            "",
            "### 输入：章节摘要 JSON",
            "",
            "<<<CHAPTERS_JSON_START>>>",
            json.dumps(chapter_items, ensure_ascii=False),
            "<<<CHAPTERS_JSON_END>>>",
            "",
            "### 输出 Schema（严格 YAML）",
            "",
            "```yaml",
            schema,
            "```",
            "",
            _yaml_only_rules(),
            "",
            "### 开始输出",
            "请直接输出 YAML：",
            "",
            "─" * 40,
            "【Schema 重复】",
            "```yaml",
            schema,
            "```",
        ]
    )
    return PromptPair(system=get_archivist_system_prompt(language=language), user=user)
