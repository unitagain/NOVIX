# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Phase 11 · Plan 编排引擎的持久化层（scratchpad）。
  把「复杂指令拆出的串行 todo + 执行进度」落盘到 plans/{plan_id}.json，
  实现断点续传（设计红线 2：真相在文件、对话可弃）。正文主线单线程串行（红线 1）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.base import BaseStorage
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 合法步骤动作 / Valid plan step actions。
VALID_PLAN_ACTIONS = ("research", "write", "edit", "analyze")
_SAFE_RE = re.compile(r'[\\/:*?"<>|\s]+')


def _safe(plan_id: str) -> str:
    return _SAFE_RE.sub("-", str(plan_id or "").strip()).strip("-")[:80] or "plan"


class PlanStore(BaseStorage):
    """串行写作计划（plan）文件存储：plans/{plan_id}.json。"""

    def _plan_dir(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "plans"

    def _plan_path(self, project_id: str, plan_id: str) -> Path:
        return self._plan_dir(project_id) / f"{_safe(plan_id)}.json"

    async def write_plan(self, project_id: str, plan: Dict[str, Any]) -> None:
        """写入（覆盖）一个 plan（含步骤与进度）。"""
        payload = json.dumps(plan, ensure_ascii=False, indent=2, default=str)
        await self._atomic_write(self._plan_path(project_id, str(plan.get("id") or "plan")), payload)

    async def read_plan(self, project_id: str, plan_id: str) -> Optional[Dict[str, Any]]:
        """读取一个 plan；不存在返回 None。"""
        path = self._plan_path(project_id, plan_id)
        if not path.exists():
            return None
        try:
            return json.loads(await self.read_text(path))
        except Exception as exc:
            logger.warning("Read plan failed (%s): %s", path, exc)
            return None

    async def list_plans(self, project_id: str) -> List[Dict[str, Any]]:
        """列出所有 plan 的摘要（id/goal/status/步骤数）。"""
        directory = self._plan_dir(project_id)
        if not directory.exists():
            return []
        out: List[Dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(await self.read_text(path))
            except Exception:
                continue
            out.append(
                {
                    "id": data.get("id"),
                    "goal": data.get("goal"),
                    "status": data.get("status"),
                    "steps": len(data.get("steps") or []),
                }
            )
        return out

    async def update_step(self, project_id: str, plan_id: str, step_id: Any, status: str, **extra: Any) -> bool:
        """更新某步骤的状态（及附加字段如 result/error），并持久化（断点续传的进度记录）。"""
        plan = await self.read_plan(project_id, plan_id)
        if not plan:
            return False
        for step in plan.get("steps") or []:
            if str(step.get("id")) == str(step_id):
                step["status"] = status
                for key, value in extra.items():
                    step[key] = value
                await self.write_plan(project_id, plan)
                return True
        return False
