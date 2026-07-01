# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Phase 11 · 写作任务规划器（Plan 编排引擎的"生成"环节）。
  把作者的复杂指令拆成**可串行执行**的最小步骤（research/write/edit/analyze），
  正文主线单线程、不并行（设计红线 1）。用 response_format 结构化输出，失败安全降级为 []。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

_VALID_ACTIONS = {"research", "write", "edit", "analyze"}


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    """从可能含代码块/前后文的 LLM 文本中稳健抽取首个 JSON 对象。"""
    s = str(text or "").strip()
    if not s:
        return None
    if "```" in s:
        try:
            start = s.find("```")
            newline = s.find("\n", start)
            end = s.find("```", newline + 1)
            if newline != -1 and end != -1:
                s = s[newline + 1 : end].strip()
        except Exception:
            pass
    left, right = s.find("{"), s.rfind("}")
    if left != -1 and right != -1 and right > left:
        s = s[left : right + 1]
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def generate_plan(
    gateway, provider: str, goal: str, context_hint: str = "", chapters: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """把复杂写作指令拆成串行 todo 步骤。

    Args:
        chapters: 项目已存在章节 ID 列表（grounding：约束 edit/analyze 只能用已存在章节，
            禁止凭空捏造；新章 write 留空 chapter）。

    Returns:
        steps 列表 [{id, action, description, chapter, title, status="pending"}]；
        指令为空 / LLM 失败 / 解析失败时返回 []（安全降级，调用方决定是否回退到普通 write/edit）。
    """
    goal = str(goal or "").strip()
    if not goal:
        return []

    existing = [str(c).strip() for c in (chapters or []) if str(c).strip()]
    chapters_line = (
        "项目已存在章节（edit/analyze 的 chapter 必须从中选，勿捏造；write 新章留空）：" + ", ".join(existing[:60])
        if existing
        else "（项目暂无已存在章节；勿凭空编造章节 ID，新章 chapter 一律留空）"
    )

    system = (
        "你是小说写作任务规划器。把作者的复杂指令拆成**可串行执行**的最小步骤"
        "（正文主线单线程、不并行）。每步动作是 research(查证设定/伏笔)、write(写某章)、"
        "edit(改某章)、analyze(定稿分析) 之一，按执行顺序排列。"
    )
    user = (
        f"指令：{goal}\n"
        f"上下文：{context_hint or '（无）'}\n"
        f"{chapters_line}\n"
        '只输出 JSON：{"steps":[{"action":"research|write|edit|analyze","description":"该步具体做什么",'
        '"chapter":"章节ID（edit/analyze 必须用已存在章节；write 新章留空）","title":"可选标题"}]}，不要其它文字。'
    )
    try:
        resp = await gateway.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            provider=provider,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("generate_plan LLM call failed: %s", exc)
        return []

    data = _extract_json_obj(resp.get("content") or "")
    if not isinstance(data, dict):
        return []
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        return []

    steps: List[Dict[str, Any]] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower()
        if action not in _VALID_ACTIONS:
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        steps.append(
            {
                "id": len(steps) + 1,
                "action": action,
                "description": description,
                "chapter": str(item.get("chapter") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "status": "pending",
            }
        )
    return steps
