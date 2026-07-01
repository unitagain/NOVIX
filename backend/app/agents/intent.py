# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  写作意图判定器（Phase 5）—— "vibe writing" 的后端骨干。
  给定用户在 chat 里的一句话 + 上下文信号（是否选中正文、是否已有草稿），
  判定本轮意图：write（另写新内容）/ edit（修改已有草稿）。让前端只需一个输入框，
  由 AI/规则自行判定撰写 vs 编辑（对标 AI coding），而非手动模式开关。

  策略：确定性快路径优先（选中→编辑、无草稿→撰写，零成本零延迟）；仅"已有草稿且无选中"
  这种真正含糊的情形才（可选）调用 LLM 结构化判定，失败安全降级。
  Intent classifier for vibe writing: heuristic fast-paths first, optional LLM
  structured classification only for the genuinely ambiguous case, with safe fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

_VALID_ACTIONS = {"write", "edit"}
_VALID_SCOPES = {"document", "selection"}

# Phase 11：plan 意图启发式——明显的多步/多章复杂指令 → 走 Plan 编排引擎（串行，红线 1）。
_PLAN_HINTS = ("逐章", "逐节", "依次写", "分别写", "先写", "再写", "规划一下", "计划安排", "几章", "多章一起")
_MULTI_CHAPTER_RE = re.compile(r"\d+\s*[-—~到至]\s*\d+\s*章")


def _detect_plan(message: str) -> bool:
    """明显的多步/多章复杂指令 → plan（保守：要求多章范围或明确多步关键词）。"""
    m = str(message or "")
    if _MULTI_CHAPTER_RE.search(m):
        return True
    return any(hint in m for hint in _PLAN_HINTS)


# Phase 12：continue 意图——在已有草稿后续写（接着写），路由到编辑能力的 append 处理。
_CONTINUE_HINTS = ("续写", "接着写", "继续写", "往后写", "接下去", "往下写", "补完")


def _detect_continue(message: str) -> bool:
    m = str(message or "")
    return any(hint in m for hint in _CONTINUE_HINTS)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
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


def _heuristic(has_selection: bool, has_draft: bool) -> Optional[Dict[str, Any]]:
    """确定性快路径；返回 None 表示含糊、需进一步判定。"""
    if has_selection:
        return {"action": "edit", "scope": "selection", "reason": "已选中正文片段", "via": "heuristic"}
    if not has_draft:
        return {"action": "write", "scope": None, "reason": "本章尚无草稿", "via": "heuristic"}
    return None


async def _llm_classify(gateway, provider: str, message: str) -> Optional[Dict[str, Any]]:
    """对含糊情形用 LLM 结构化判定（write vs edit）。任何异常由调用方兜底。"""
    system = (
        "你是写作助手的意图判定器。判断用户这句话是想：\n"
        "- write：另写一段全新内容（如新场景、续写下一段剧情）\n"
        "- edit：修改/润色已有草稿（如调整措辞、改情节、压缩）\n"
        '只输出 JSON：{"action":"write|edit","scope":"document","reason":"≤20字理由"}，不要其它文字。'
    )
    user = f"用户输入：{str(message or '').strip()}\n本章已有草稿。请判定 action。"
    resp = await gateway.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        provider=provider,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    data = _extract_json(resp.get("content") or "")
    if not data:
        return None
    action = str(data.get("action") or "").strip().lower()
    if action not in _VALID_ACTIONS:
        return None
    scope = str(data.get("scope") or "").strip().lower()
    if scope not in _VALID_SCOPES:
        scope = "document"
    return {"action": action, "scope": scope, "reason": str(data.get("reason") or "")[:40], "via": "llm"}


async def classify_writing_intent(
    message: str,
    *,
    has_selection: bool = False,
    has_draft: bool = False,
    gateway=None,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """判定写作意图，返回 ``{action, scope, reason, via}``。

    - action: "write"（另写新内容） | "edit"（修改已有草稿）
    - scope: "selection" | "document" | None
    - via: "heuristic" | "llm"（来源，便于可观测）

    决策树：选中正文 → edit/selection；无草稿 → write；已有草稿且无选中 → LLM 判定，
    失败/无网关则安全降级为 edit/document（已有草稿时默认按修改处理最稳）。
    """
    if not has_selection and _detect_plan(message):
        return {"action": "plan", "scope": None, "reason": "复杂多步/多章指令", "via": "heuristic"}

    if has_draft and not has_selection and _detect_continue(message):
        return {"action": "continue", "scope": "document", "reason": "续写指令", "via": "heuristic"}

    fast = _heuristic(has_selection, has_draft)
    if fast is not None:
        return fast

    if gateway is not None and provider:
        try:
            decision = await _llm_classify(gateway, provider, message)
            if decision is not None:
                return decision
        except Exception as exc:
            logger.warning("Intent LLM classification failed; using heuristic fallback: %s", exc)

    return {"action": "edit", "scope": "document", "reason": "已有草稿，默认按修改处理", "via": "heuristic"}
