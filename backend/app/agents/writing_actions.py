# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  写作动作工具集（Phase B）—— 把反问、写正文、改正文与收尾统一为同一个 Writer 工具集，
  彻底对齐行业 AI coding 的文件编辑形态（Claude Code 的 Write / Edit）：
    - ask_clarification(questions) —— Writer 根据本轮上下文自主提出 1-3 个问题并暂停
    - write_content(content[, mode])  ≈ Write(file, content)   —— 覆盖 / 追加整章·整段
    - edit_lines(old_text, new_text)  ≈ Edit(old_string, new_string) —— 精确替换一处（唯一校验）
  关键范式：**agent（主循环里的 LLM）才是内容生成者**，工具只对"正文工作副本"做纯字符串操作、
  不内部调用 LLM。这样写/改是同一个功能的两种手段，由 agent 看着当前正文自主选择，
  完成后由调用方对「原文 vs 工作副本」求 diff 交付审阅采纳（与 writer/editor 的产物同质）。

  Writing-action toolset: keeps clarification, prose writing, editing, chapter targeting and turn
  completion in one agent-callable tool surface. The agent itself authors content and questions;
  tools only normalize the request or mutate a plain-text working copy (no nested LLM calls).
  A final diff(original vs working copy) is handed to the human for accept/reject.
"""

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from app.agents.turn_effects import normalize_turn_effect
from app.error_contract import safe_error_code, tool_error_text
from app.utils.chapter_id import ChapterIDValidator, normalize_chapter_id
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_CLARIFICATION_QUESTIONS = 3
_MAX_CLARIFICATION_TEXT = 1000
_MAX_CLARIFICATION_REASON = 400
_MAX_CLARIFICATION_OPTIONS = 8


def normalize_clarification_questions(value: Any) -> List[Dict[str, Any]]:
    """Normalize model-provided questions at the tool boundary.

    The Writer decides whether to ask and writes the question text. This
    helper only enforces the one-to-three item protocol and bounds optional
    fields; it never invents a question.
    """

    raw = value.get("questions") if isinstance(value, Mapping) else value
    if not isinstance(raw, (list, tuple)):
        return []
    normalized: List[Dict[str, Any]] = []
    seen_texts = set()
    for item in raw:
        if isinstance(item, Mapping):
            text = str(item.get("text") or item.get("question") or "").strip()
            item_type = str(item.get("type") or "").strip()
            reason = str(item.get("reason") or "").strip()
            options_value = item.get("options")
            default = str(item.get("default") or "").strip()
        else:
            text = str(item or "").strip()
            item_type = ""
            reason = ""
            options_value = None
            default = ""
        if not text:
            continue
        text = text[:_MAX_CLARIFICATION_TEXT]
        dedupe_key = text.casefold()
        if dedupe_key in seen_texts:
            continue
        seen_texts.add(dedupe_key)
        question: Dict[str, Any] = {"text": text}
        if item_type:
            question["type"] = item_type[:80]
        if reason:
            question["reason"] = reason[:_MAX_CLARIFICATION_REASON]
        if isinstance(options_value, (list, tuple)):
            options: List[str] = []
            for option in options_value:
                option_text = str(option or "").strip()
                if option_text and option_text not in options:
                    options.append(option_text[:200])
                if len(options) >= _MAX_CLARIFICATION_OPTIONS:
                    break
            if options:
                question["options"] = options
        if default:
            question["default"] = default[:200]
        normalized.append(question)
        if len(normalized) >= _MAX_CLARIFICATION_QUESTIONS:
            break
    return normalized


def writing_action_schemas() -> List[Dict[str, Any]]:
    """返回写作动作与强制收尾工具定义。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "ask_clarification",
                "description": (
                    "完成必要检索后，如果仍缺少会实质改变写作结果的作者决定，向作者提出具体问题。"
                    "问题数量由你按当前上下文自行决定，每次只能提出 1-3 个；调用后本轮立即暂停，"
                    "不得继续写正文、修改正文或调用 finish_turn。不要提出泛化问题，也不要重复已有上下文。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "description": "由你根据当前写作缺口选择的一个到三个问题",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string", "minLength": 1, "description": "给作者的问题原文"},
                                    "type": {"type": "string", "description": "可选的问题类型"},
                                    "reason": {"type": "string", "description": "可选：该问题为何影响本轮写作"},
                                    "options": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "maxItems": 8,
                                        "description": "可选答案选项",
                                    },
                                    "default": {"type": "string", "description": "可选默认答案"},
                                },
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["questions"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_chapter",
                "description": (
                    "为本轮写作建立一个新章节目标。用户要求新建章节、当前没有选中章节，或正文应写入新章时，"
                    "必须先调用此工具，再调用 write_content。完整写作并 finish_turn 成功后由系统自动保存章节。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chapter_id": {
                            "type": "string",
                            "description": "新章节 ID，如 V1C4；留空则在当前卷自动选择下一个可用编号",
                        },
                        "title": {
                            "type": "string",
                            "description": "简洁章节标题；用户未指定时由你根据本章内容拟定",
                        },
                    },
                    "required": ["title"],
                },
            },
        },
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
        {
            "type": "function",
            "function": {
                "name": "finish_turn",
                "description": (
                    "结束本轮并提交收尾判断。无论本轮是交流、润色、写章还是修改剧情，都必须最后调用一次。"
                    "不要用自然语言代替此工具。事实候选必须引用最终正文中真实存在的 evidence 原句。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "change_type": {
                            "type": "string",
                            "enum": ["conversation", "prose_edit", "chapter_write", "plot_edit"],
                            "description": "conversation=交流；prose_edit=措辞润色；chapter_write=写新章/整体重写；plot_edit=剧情或设定发生变化",
                        },
                        "fact_operation": {
                            "type": "string",
                            "enum": ["none", "merge", "replace_chapter"],
                            "description": "none=不更新事实；merge=合并新增事实；replace_chapter=替换本章尚未确认的自动事实",
                        },
                        "chapter_summary": {
                            "type": "string",
                            "description": "写章或剧情修改后的简洁章节摘要；普通交流或纯润色可为空",
                        },
                        "fact_candidates": {
                            "type": "array",
                            "maxItems": 5,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "statement": {"type": "string", "description": "以后需要遵守的故事事实"},
                                    "evidence": {"type": "string", "description": "最终正文中逐字存在的证据原句"},
                                    "category": {"type": "string", "description": "事件、关系、人物状态、物品、地点或世界规则"},
                                },
                                "required": ["statement", "evidence", "category"],
                            },
                        },
                        "message": {"type": "string", "description": "给用户的自然、简短完成说明"},
                    },
                    "required": ["change_type", "fact_operation", "chapter_summary", "fact_candidates", "message"],
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

    def __init__(
        self,
        original_text: str = "",
        *,
        retrieval_toolset: Any = None,
        active_chapter: str = "",
        existing_chapters: Optional[List[str]] = None,
        require_chapter_target: bool = False,
    ):
        self.original_text = str(original_text or "")
        self.working_text = self.original_text
        self.retrieval = retrieval_toolset
        self.actions: List[Dict[str, Any]] = []
        self._turn_effect_raw: Dict[str, Any] | None = None
        self._clarification: Dict[str, Any] | None = None
        self.active_chapter = normalize_chapter_id(active_chapter) if active_chapter else ""
        self.existing_chapters = {
            normalize_chapter_id(chapter)
            for chapter in (existing_chapters or [])
            if normalize_chapter_id(chapter)
        }
        self.target_chapter = self.active_chapter
        self.chapter_title = ""
        self.create_chapter_requested = False
        self.require_chapter_target = bool(require_chapter_target)

    def schemas(self) -> List[Dict[str, Any]]:
        """写作工具（+ 可选检索工具）的合并 schema 列表。检索工具在前，便于 agent 先查后写。"""
        tools: List[Dict[str, Any]] = []
        if self.retrieval is not None:
            try:
                tools.extend(self.retrieval.schemas())
            except Exception as exc:  # 检索工具异常不应阻断写作能力
                logger.warning("retrieval toolset schemas() failed: %s", safe_error_code(exc), exc_info=True)
        tools.extend(writing_action_schemas())
        return tools

    def is_result_recoverable(self, name: str) -> bool:
        if name in {"ask_clarification", "create_chapter", "write_content", "edit_lines", "finish_turn"}:
            return False
        checker = getattr(self.retrieval, "is_result_recoverable", None)
        return bool(checker(name)) if callable(checker) else False

    @staticmethod
    def is_input_tool(name: str) -> bool:
        """Identify the tool whose request must dominate a provider batch."""

        return str(name or "") == "ask_clarification"

    async def execute(self, name: str, arguments: Any) -> str:
        """分发执行写作动作；未知名委托给检索工具。任何异常转为可读文本，避免中断 agentic 循环。"""
        args = self._parse_args(arguments)
        try:
            if name == "ask_clarification":
                return self._ask_clarification(args.get("questions"))
            if name == "create_chapter":
                return self._create_chapter(
                    str(args.get("chapter_id") or ""),
                    str(args.get("title") or ""),
                )
            if name == "write_content":
                return self._write_content(str(args.get("content") or ""), str(args.get("mode") or "replace"))
            if name == "edit_lines":
                return self._edit_lines(str(args.get("old_text") or ""), str(args.get("new_text") or ""))
            if name == "finish_turn":
                if self._clarification is not None:
                    return "[tool_error code=clarification_pending] ask_clarification 已暂停本轮，不能提交 finish_turn"
                self._turn_effect_raw = dict(args)
                effect = self.terminal_payload()
                return f"本轮已收尾（change_type={effect['change_type']}, fact_operation={effect['fact_operation']}）。"
        except Exception as exc:
            logger.warning("Writing action %s failed: %s", name, safe_error_code(exc), exc_info=True)
            return tool_error_text(name, exc)

        if self.retrieval is not None:
            try:
                return await self.retrieval.execute(name, arguments)
            except Exception as exc:
                logger.warning("Retrieval tool %s failed: %s", name, safe_error_code(exc), exc_info=True)
                return tool_error_text(name, exc)
        return f"[未知工具：{name}]"

    def _ask_clarification(self, raw_questions: Any) -> str:
        """Record a Writer-authored input request and pause the current turn."""

        if self._turn_effect_raw is not None or any(
            action.get("action") in {"create_chapter", "write", "edit"} for action in self.actions
        ):
            return "[tool_error code=clarification_must_precede_writing] ask_clarification 必须在写入或修改正文前调用"
        if self._clarification is not None:
            return "[tool_error code=clarification_already_requested] 本轮已经提出反问"
        questions = normalize_clarification_questions(raw_questions)
        if not questions:
            return "[tool_error code=clarification_questions_required] questions 至少包含一个有效问题"
        reason = next(
            (
                str(question.get("reason") or "").strip()
                for question in questions
                if str(question.get("reason") or "").strip()
            ),
            "writer_requested_clarification",
        )
        self._clarification = {
            "decision": "ask",
            "reason": reason[:_MAX_CLARIFICATION_REASON],
            "questions": questions,
            "question_count": len(questions),
        }
        self.actions.append({"action": "ask_clarification", "question_count": len(questions)})
        self._register_clarification_source(self._clarification)
        return f"已提出 {len(questions)} 个反问；本轮已暂停，等待作者回答。"

    @property
    def input_required(self) -> bool:
        return self._clarification is not None

    def input_required_payload(self) -> Optional[Dict[str, Any]]:
        if self._clarification is None:
            return None
        return dict(self._clarification)

    @staticmethod
    def _register_clarification_source(payload: Dict[str, Any]) -> None:
        try:
            from app.context_engine.turn_scope import current_turn_scope

            scope = current_turn_scope()
            if scope is None or not scope.source_closure_required:
                return
            identity = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            # Source ids are deterministic within a process while keeping the
            # question text out of trace labels and metric dimensions.
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            source_id = f"writer.clarification.{digest}"
            scope.register_source_content(
                source_id=source_id,
                asset_type="clarification_request",
                content=payload,
                selection_reason="writer_tool_ask_clarification",
                artifact_ref="WritingActionToolset.ask_clarification",
            )
        except Exception as exc:
            logger.warning("Clarification source registration failed: %s", safe_error_code(exc))

    def _create_chapter(self, chapter_id: str, title: str) -> str:
        if self._clarification is not None:
            return "[tool_error code=clarification_pending] ask_clarification 已暂停本轮，不能建立章节"
        if any(action.get("action") in {"write", "edit"} for action in self.actions):
            return "[create_chapter 必须在 write_content/edit_lines 之前调用]"
        clean_title = str(title or "").strip()
        if not clean_title:
            return "[create_chapter 需要非空 title]"
        default_volume = ChapterIDValidator.extract_volume_id(self.active_chapter) or self._default_volume()
        target = normalize_chapter_id(chapter_id, default_volume=default_volume) if chapter_id.strip() else ""
        if not target:
            target = self._suggest_next_chapter(default_volume)
        if not ChapterIDValidator.validate(target):
            return f"[create_chapter 的 chapter_id 无效：{chapter_id}]"
        if target in self.existing_chapters:
            return f"[章节 {target} 已存在，不能重复创建；如需修改请使用现有章节]"

        self.target_chapter = target
        self.chapter_title = clean_title[:120]
        self.create_chapter_requested = True
        self.original_text = ""
        self.working_text = ""
        self.actions.append(
            {
                "action": "create_chapter",
                "chapter": target,
                "title": self.chapter_title,
            }
        )
        return f"已建立新章节目标：{target}《{self.chapter_title}》。请继续调用 write_content 写入正文。"

    def _default_volume(self) -> str:
        volumes = [ChapterIDValidator.extract_volume_id(chapter) for chapter in self.existing_chapters]
        valid = sorted((volume for volume in volumes if volume), key=lambda value: int(value[1:]))
        return valid[-1] if valid else "V1"

    def _suggest_next_chapter(self, volume_id: str) -> str:
        max_number = 0
        for chapter in self.existing_chapters:
            parsed = ChapterIDValidator.parse(chapter)
            if not parsed or parsed["type"]:
                continue
            chapter_volume = f"V{parsed['volume'] or 1}"
            if chapter_volume == volume_id:
                max_number = max(max_number, int(parsed["chapter"]))
        return f"{volume_id}C{max_number + 1}"

    def _write_content(self, content: str, mode: str) -> str:
        if self._clarification is not None:
            return "[tool_error code=clarification_pending] ask_clarification 已暂停本轮，不能写入正文"
        if self.require_chapter_target and not self.target_chapter:
            return "[当前没有目标章节；请先调用 create_chapter，再写入正文]"
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
        if self._clarification is not None:
            return "[tool_error code=clarification_pending] ask_clarification 已暂停本轮，不能修改正文"
        if self.require_chapter_target and not self.target_chapter:
            return "[当前没有目标章节；请先选择章节，或调用 create_chapter 新建章节]"
        old_text = str(old_text or "")
        new_text = str(new_text or "")
        if not old_text:
            return "[edit_lines 需要 old_text]"
        count = self.working_text.count(old_text)
        if count != 1:
            try:
                from app.observability.usage_diagnostics import record_edit_miss

                record_edit_miss()
            except Exception as exc:
                logger.warning("Edit diagnostics failed: %s", type(exc).__name__)
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

    @property
    def requires_terminal_tool(self) -> bool:
        return True

    def is_terminal_tool(self, name: str) -> bool:
        return str(name or "") == "finish_turn"

    @property
    def has_terminal_payload(self) -> bool:
        return self._turn_effect_raw is not None

    def terminal_payload(self) -> Dict[str, Any]:
        return normalize_turn_effect(
            self._turn_effect_raw,
            changed=self.changed,
            had_draft=bool(self.original_text.strip()),
        )

    def chapter_target(self) -> Optional[Dict[str, Any]]:
        if not self.create_chapter_requested or not self.target_chapter:
            return None
        return {
            "chapter": self.target_chapter,
            "title": self.chapter_title,
            "create": True,
        }

    @staticmethod
    def _parse_args(arguments: Any) -> Dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        try:
            data = json.loads(arguments or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
