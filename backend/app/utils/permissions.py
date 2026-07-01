# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  P0/P3 · 权限分级（声明式策略）。
  把操作按风险分级：allow（只读，直接放行）| ask（改稿/写入，需确认）| deny（高危 AI 自主操作直接阻断）。
  策略集中声明，供工具调用 / 路由在各写入点门控（"越权写入被拦截"）。
  单用户本地工具语境下，"权限"= 防 AI 自主执行危险操作（删章/覆盖 canon）的安全闸。
"""

from __future__ import annotations

# 操作 → 风险等级。未列出的操作默认 ask（保守：未知操作不直接放行）。
PERMISSION_POLICY = {
    # 只读检索 / 评审：直接放行
    "lookup_card": "allow",
    "query_canon": "allow",
    "query_relations": "allow",
    "read_chapter": "allow",
    "search_prose": "allow",
    "review": "allow",
    # 改稿 / canon 写入：需确认
    "write_chapter": "ask",
    "edit_chapter": "ask",
    "add_fact": "ask",
    "confirm_facts": "ask",
    "update_card": "ask",
    "save_draft": "ask",
    "write_memory": "ask",
    # 删除 / 覆盖：高危 AI 自主操作默认阻断；显式人工流程应走单独 API/确认闸。
    "delete_project": "deny",
    "delete_chapter": "deny",
    "delete_fact": "deny",
    "overwrite_canon": "deny",
    "bulk_update_canon": "deny",
}

_VALID_LEVELS = {"allow", "ask", "deny"}


def permission_for(operation: str) -> str:
    """返回操作的权限等级：allow | ask | deny。未知操作保守默认 ask。"""
    return PERMISSION_POLICY.get(str(operation or ""), "ask")


def is_allowed(operation: str) -> bool:
    """只读操作（allow）可直接放行，无需确认。"""
    return permission_for(operation) == "allow"


def requires_confirmation(operation: str) -> bool:
    """ask 操作必须显式确认才能执行；deny 操作应直接阻断。"""
    return permission_for(operation) == "ask"


def is_denied(operation: str) -> bool:
    """deny 操作不应由 AI 自主执行。"""
    return permission_for(operation) == "deny"
