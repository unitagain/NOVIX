# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  写作动作工具集（Phase B）—— 把"写正文"与"改正文"统一为 agent 可自主调用的两个工具，
  彻底对齐行业 AI coding 的文件编辑形态（Claude Code 的 Write / Edit）：
    - write_content(content[, mode])  ≈ Write(file, content)   —— 覆盖 / 追加整章·整段
    - edit_lines(old_text, new_text)  ≈ Edit(old_string, new_string) —— 精确替换一处（唯一校验）
  关键范式：**agent（主循环里的 LLM）才是内容生成者**，工具只对"正文工作副本"做纯字符串操作、
  不内部调用 LLM。这样写/改是同一个功能的两种手段，由 agent 看着当前正文自主选择，
  完成后由调用方对「原文 vs 工作副本」求 diff 交付审阅采纳（与 writer/editor 的产物同质）。

  Writing-action toolset: unifies "write prose" and "edit prose" into two agent-callable tools
  mirroring AI-coding's Write/Edit. The agent itself authors the content; the tools only mutate a
  plain-text working copy (no nested LLM calls). A final diff(original vs working copy) is handed to
  the human for accept/reject — identical product shape to the writer/editor path it replaces.
"""

import json
from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


def writing_action_schemas() -> List[Dict[str, Any]]:
    """返回 OpenAI function-tool 风格的写作动作工具定义（write_content / edit_lines）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "write_content",
                "description": (
                    "写入正文：覆盖或追加整章/整段。当需要从空白写新章、或大段重写时调用。"
                    "content 必须是你直接生成的小说正文本身（不要写解释、不要带标记）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "要写入的完整正文（你生成的小说正文，纯文本，不含解释或 markdown 标记）",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                            "description": "replace=覆盖全文（默认）；append=追加到现有正文末尾（续写）",
                        },
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_lines",
                "description": (
                    "精确替换正文中的一处文本，用于局部修改/润色（改措辞、调情节、删冗余、扩写一段）。"
                    "old_text 必须与正文逐字一致且在正文中唯一出现；new_text 为替换后的文本（删除则留空字符串）。"
                    "若 old_text 不唯一，请提供更长、含上下文的片段以精确定位。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "old_text": {
                            "type": "string",
                            "description": "要被替换的原文片段（必须与正文完全一致且唯一出现）",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "替换后的新文本（删除该片段则传空字符串）",
                        },
                    },
                    "required": ["old_text", "new_text"],
                },
            },
        },
    ]


class WritingActionToolset:
    """把"写正文/改正文"封装为 agent 可调用的写作工具，操作一份正文工作副本（纯字符串）。

    可选组合一个只读检索 toolset（retrieval_toolset，如 WriterToolset），让 agent 在同一主循环里
    "先检索设定、再写/改"——schemas() 会把检索工具与写作工具合并暴露，execute() 把未知工具名委托给它。

    Attributes:
        original_text: 流式/编辑前的原正文（diff 基线）。
        working_text: 当前工作副本（写/改累积结果）。
        actions: 写作动作记录（观测用，不参与逻辑）。
    """

    def __init__(self, original_text: str = "", *, retrieval_toolset: Any = None):
        self.original_text = str(original_text or "")
        self.working_text = self.original_text
        self.retrieval = retrieval_toolset
        self.actions: List[Dict[str, Any]] = []

    def schemas(self) -> List[Dict[str, Any]]:
        """写作工具（+ 可选检索工具）的合并 schema 列表。检索工具在前，便于 agent 先查后写。"""
        tools: List[Dict[str, Any]] = []
        if self.retrieval is not None:
            try:
                tools.extend(self.retrieval.schemas())
            except Exception as exc:  # 检索工具异常不应阻断写作能力
                logger.warning("retrieval toolset schemas() failed: %s", exc)
        tools.extend(writing_action_schemas())
        return tools

    async def execute(self, name: str, arguments: Any) -> str:
        """分发执行写作动作；未知名委托给检索工具。任何异常转为可读文本，避免中断 agentic 循环。"""
        args = self._parse_args(arguments)
        try:
            if name == "write_content":
                return self._write_content(str(args.get("content") or ""), str(args.get("mode") or "replace"))
            if name == "edit_lines":
                return self._edit_lines(str(args.get("old_text") or ""), str(args.get("new_text") or ""))
        except Exception as exc:
            logger.warning("Writing action %s failed: %s", name, exc)
            return f"[写作工具 {name} 执行出错：{exc}]"

        if self.retrieval is not None:
            return await self.retrieval.execute(name, arguments)
        return f"[未知工具：{name}]"

    def _write_content(self, content: str, mode: str) -> str:
        content = str(content or "")
        if not content.strip():
            return "[write_content 需要非空 content]"
        if mode == "append" and self.working_text.strip():
            self.working_text = self.working_text.rstrip() + "\n\n" + content
            verb = "追加"
        else:
            mode = "replace"
            self.working_text = content
            verb = "写入"
        self.actions.append({"action": "write", "mode": mode, "chars": len(content)})
        return f"已{verb} {len(content)} 字（mode={mode}）。当前正文共 {len(self.working_text)} 字。"

    def _edit_lines(self, old_text: str, new_text: str) -> str:
        old_text = str(old_text or "")
        new_text = str(new_text or "")
        if not old_text:
            return "[edit_lines 需要 old_text]"
        count = self.working_text.count(old_text)
        if count == 0:
            return "未找到要替换的文本：old_text 未在当前正文中出现。请逐字核对原文，或改用 write_content 覆盖。"
        if count > 1:
            return (
                f"old_text 在正文中出现 {count} 次、不唯一，无法安全定位。"
                "请提供更长、包含上下文的唯一片段后重试。"
            )
        self.working_text = self.working_text.replace(old_text, new_text, 1)
        self.actions.append({"action": "edit", "old_chars": len(old_text), "new_chars": len(new_text)})
        delta = "删除" if not new_text else f"-{len(old_text)} +{len(new_text)} 字"
        return f"已替换 1 处（{delta}）。当前正文共 {len(self.working_text)} 字。"

    @property
    def changed(self) -> bool:
        """工作副本是否相对原文发生变化（决定是否需要交付 diff）。"""
        return self.working_text != self.original_text

    @staticmethod
    def _parse_args(arguments: Any) -> Dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        try:
            data = json.loads(arguments or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
