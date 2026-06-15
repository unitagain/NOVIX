"""
中文说明：检索与重排提示词模板。

Prompt templates for retrieval and reranking.
"""

from __future__ import annotations

import json
from typing import Dict, List

from .shared import PromptPair, P0_MARKER, _json_only_rules, _u_shape


def text_chunk_rerank_prompt(query: str, payload: List[Dict[str, str]]) -> PromptPair:
    """
    生成检索重排序提示词。

    设计目标：
    - 基于语义相关性对候选片段进行精准评分
    - 避免高分泛化（氛围相似但缺乏实质证据）
    - 确保输出覆盖所有候选 ID
    """
    schema = '[{"id": "片段ID", "score": 0-5}]'

    critical = "\n".join(
        [
            "### 角色定位",
            "你是 WenShape 系统的「检索重排序器」，负责评估文本片段与查询的相关性。",
            "",
            "### 核心任务",
            "根据【目标 query】对【候选片段】进行相关性评分",
            "",
            "### 输出 Schema（严格 JSON 数组）",
            "",
            "```json",
            schema,
            "```",
            "",
            f"{P0_MARKER} 必须覆盖输入中的每个 id",
            f"{P0_MARKER} 每个 id 仅出现一次",
            "",
            _json_only_rules("输出 JSON 数组"),
        ]
    )
    system = _u_shape(
        critical,
        "\n".join(
            [
                "### 评分标准（0-5 分制）",
                "",
                "| 分数 | 相关性描述 |",
                "|-----|-----------|",
                "| 5 | 直接提供 query 所需的关键证据（明确提到核心实体/事件/关系/原因） |",
                "| 4 | 高度相关，能补齐重要细节或强约束，略缺关键一句 |",
                "| 3 | 相关，有可用信息，但不够直接或只覆盖部分要点 |",
                "| 2 | 弱相关，仅为背景信息或轻微提及 |",
                "| 1 | 几乎无关，仅关键词巧合或极弱关联 |",
                "| 0 | 完全无关 |",
                "",
                "### 评分原则（避免高分泛化）",
                "",
                f"{P0_MARKER} 证据导向：",
                "  - 只基于片段实际内容评分",
                "  - 禁止「猜测全文可能相关」",
                "",
                f"{P0_MARKER} 锚点要求：",
                "  - 缺少明确实体/事件锚点时，即使氛围相似也不给高分",
                "",
                f"{P0_MARKER} 输出纯净：",
                "  - 禁止输出解释与理由",
            ]
        ),
    )
    user = "\n".join(
        [
            "### 目标 Query",
            "",
            f"**{str(query or '').strip()}**",
            "",
            "### 候选片段（每项含 id, text）",
            "",
            "<<<CANDIDATES_START>>>",
            json.dumps(payload, ensure_ascii=False),
            "<<<CANDIDATES_END>>>",
            "",
            "### 输出示例（学习格式，不要照抄）",
            "",
            "```json",
            '[{"id": "c1", "score": 4}, {"id": "c2", "score": 1}]',
            "```",
            "",
            "### 开始输出",
            "请直接输出 JSON 数组：",
        ]
    )
    return PromptPair(system=system, user=user)
