# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  编排器共享类型 - 在独立模块中定义SessionStatus以避免循环导入
  Shared Types for Orchestrator - Define SessionStatus in separate module to avoid circular imports.

设计说明 / Design Note:
  SessionStatus 放在独立模块中，避免 Mixin 与 orchestrator 之间的循环导入。
  Separating types prevents circular import issues between mixins and main orchestrator.
"""

from app.orchestrator.contracts import SessionStatus

__all__ = ["SessionStatus"]
