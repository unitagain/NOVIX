"""Materialize provider-facing context for writing routes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config import config
from app.context_engine.token_accounting import count_text_tokens
from app.context_engine.turn_scope import current_turn_scope


@dataclass(frozen=True)
class WriterRequest:
    messages: List[Dict[str, Any]]
    temperature: float
    max_tokens: int
    max_iterations: int
    fingerprint: str
    supply_report: "ContextSupplyReport"


@dataclass(frozen=True)
class ContextSupplyReport(Mapping[str, object]):
    available: tuple[str, ...]
    pushed: tuple[str, ...]
    retrieved: tuple[str, ...]
    used: tuple[str, ...]
    omitted: tuple[Dict[str, Any], ...]
    draft_tokens: int = 0
    draft_pushed_tokens: int = 0

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 7

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": list(self.available),
            "pushed": list(self.pushed),
            "retrieved": list(self.retrieved),
            "used": list(self.used),
            "omitted": [dict(item) for item in self.omitted],
            "draft_tokens": self.draft_tokens,
            "draft_pushed_tokens": self.draft_pushed_tokens,
        }


class ContextAssemblyService:
    """Single owner for writer prompt assembly and generation budgets."""

    def __init__(self, *, language: str = "zh"):
        self.language = language

    def set_language(self, language: str) -> None:
        self.language = str(language or "zh")

    def assemble_writer_request(
        self,
        *,
        message: str,
        chapter: str,
        current_text: str,
        has_selection: bool,
        target_word_count: int,
        context_plan: Optional[Any] = None,
    ) -> WriterRequest:
        system = self.build_writer_system(
            has_draft=bool(str(current_text or "").strip()),
            target_word_count=target_word_count,
        )
        input_budget = int((getattr(context_plan, "budget", {}) or {}).get("input_tokens") or 0)
        draft_budget = min(12_000, max(3_000, int(input_budget * 0.60))) if input_budget else 6_000
        user, draft_projection = self._build_writer_user(
            message=message,
            chapter=chapter,
            current_text=current_text,
            has_selection=has_selection,
            target_word_count=target_word_count,
            draft_budget_tokens=draft_budget,
        )
        requested_max = max(4096, int(target_word_count * 2.0))
        if context_plan is not None:
            reserve = int((getattr(context_plan, "budget", {}) or {}).get("output_reserve_tokens") or 0)
            if reserve > 0:
                requested_max = min(requested_max, reserve)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        scope = current_turn_scope()
        if scope is not None and scope.source_closure_required:
            source_rows = [
                {
                    "source_id": "prompt.writer.system",
                    "asset_type": "prompt",
                    "content": system,
                    "selection_reason": "writer_system_prompt",
                    "artifact_ref": "ContextAssemblyService.build_writer_system",
                },
                {
                    "source_id": "input.user_message",
                    "asset_type": "user_message",
                    "content": str(message or ""),
                    "selection_reason": "author_instruction",
                    "artifact_ref": "session_chat:message",
                },
                {
                    "source_id": "target.chapter",
                    "asset_type": "chapter",
                    "content": str(chapter or ""),
                    "selection_reason": "target_chapter",
                    "artifact_ref": "session_chat:chapter",
                },
                {
                    "source_id": "config.writer_runtime",
                    "asset_type": "project_config",
                    "content": {
                        "agentic_max_iterations": int(config.get("retrieval", {}).get("agentic_max_iterations", 4)),
                        "language": self.language,
                        "target_word_count": int(target_word_count),
                    },
                    "selection_reason": "writer_runtime_configuration",
                    "artifact_ref": "config.yaml:retrieval",
                },
                {
                    "source_id": "prompt.writer.user",
                    "asset_type": "prompt",
                    "content": user,
                    "selection_reason": "writer_user_prompt_projection",
                    "artifact_ref": "ContextAssemblyService.build_writer_user",
                },
            ]
            if current_text:
                source_rows.append(
                    {
                        "source_id": "draft.current",
                        "asset_type": "draft",
                        "content": current_text,
                        "selection_reason": "edit_baseline",
                        "artifact_ref": f"draft:{chapter}",
                    }
                )
            for row in source_rows:
                scope.register_source_content(**row)
            scope.register_provider_payload(
                messages,
                source_prefix="writer.initial",
                selection_reason="writer_initial_assembly",
                artifact_ref="ContextAssemblyService.assemble_writer_request",
            )
        payload = {
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": requested_max,
            "max_iterations": int(config.get("retrieval", {}).get("agentic_max_iterations", 4)) + 2,
        }
        available = ["prompt", "user_message", "chapter", "project_config"]
        pushed = list(available)
        omitted: List[Dict[str, Any]] = []
        if current_text:
            available.append("draft")
            pushed.append("draft")
            if draft_projection["projected"]:
                omitted.append(
                    {
                        "type": "draft",
                        "reason": "token_budget_projection",
                        "recoverable": True,
                        "source_ref": f"draft:{chapter}",
                    }
                )
        for source_type in self._available_source_types(context_plan):
            if source_type not in available:
                available.append(source_type)
        supply_report = ContextSupplyReport(
            available=tuple(available),
            pushed=tuple(pushed),
            retrieved=(),
            used=tuple(pushed),
            omitted=tuple(omitted),
            draft_tokens=int(draft_projection["original_tokens"]),
            draft_pushed_tokens=int(draft_projection["pushed_tokens"]),
        )
        payload["supply_report"] = supply_report
        fingerprint_payload = {**payload, "supply_report": supply_report.to_dict()}
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return WriterRequest(
            messages=messages,
            temperature=0.7,
            max_tokens=requested_max,
            max_iterations=int(config.get("retrieval", {}).get("agentic_max_iterations", 4)) + 2,
            fingerprint=fingerprint,
            supply_report=supply_report,
        )

    @staticmethod
    def _available_source_types(context_plan: Optional[Any]) -> List[str]:
        aliases = {
            "cards": "card",
            "character_card": "card",
            "world_card": "card",
            "style_card": "style",
            "summaries": "summary",
            "chapter_summary": "summary",
            "scene_brief": "summary",
            "relations": "canon",
            "fact": "canon",
            "text_chunk": "prose",
        }
        available: List[str] = []
        for row in tuple(getattr(context_plan, "snapshot", ()) or ()):
            item = dict(row)
            raw = str(item.get("asset_type") or item.get("type") or "").strip().lower()
            normalized = aliases.get(raw, raw)
            if normalized in {"style", "card", "canon", "memory", "summary", "draft", "prose"}:
                if normalized not in available:
                    available.append(normalized)
        return available

    def build_writer_system(self, *, has_draft: bool, target_word_count: int = 3000) -> str:
        lang = "中文" if self.language == "zh" else "英文"
        base = (
            "你是小说撰稿 agent，工作方式与 AI 编程助手一致：先用检索工具核对设定，再用写作工具落笔。"
            "『写新内容』与『改旧文』不是两件事，而是你的两个工具，由你看着当前正文自主选择：\n"
            "- write_content(content[, mode]): 写入整章/整段正文（mode=replace 覆盖 / append 续写），"
            "content 是你直接创作的小说正文本身。\n"
            "- edit_lines(old_text, new_text): 精确替换正文中唯一出现的一处片段，用于局部修改/润色/删减"
            "（old_text 须与正文逐字一致且唯一）。\n"
            "检索工具（lookup_card/query_canon/query_relations/read_chapter/search_prose）供你按需"
            "核对人物设定、关系、伏笔与已确立事实，避免前后矛盾。\n\n"
            "工作原则：\n"
            "1) 先检索后落笔：动笔前查全相关设定与事实，时间近的章节更相关。\n"
            "2) 选对工具：正文为空或需大段新内容 → write_content；只改局部 → edit_lines。\n"
            "3) content 只含小说正文，不夹带解释、标题或标记。\n"
            "4) 完成后用一句话说明你改了什么/为何，不要复述正文。\n"
        )
        if has_draft:
            base += (
                "本章已有正文（见用户消息）。这是『编辑』场景：优先用 edit_lines 做针对性的局部修改/润色"
                "（按需改写，不必长篇）；仅当用户明确要求重写整章时才 write_content(replace)。\n"
            )
        else:
            base += (
                f"本章暂无正文。这是『撰写整章』场景：必须用 write_content **一次写出完整的一整章**，"
                f"目标约 {target_word_count} 字（不少于 {int(target_word_count * 0.8)} 字），要有起承转合、"
                "场景与对白充分展开，写到本章自然收束——绝不能只写开头、提纲或片段就停。\n"
            )
        return f"{base}\n请用{lang}创作。"

    @staticmethod
    def build_writer_user(
        *, message: str, chapter: str, current_text: str, has_selection: bool, target_word_count: int = 3000
    ) -> str:
        user, _projection = ContextAssemblyService._build_writer_user(
            message=message,
            chapter=chapter,
            current_text=current_text,
            has_selection=has_selection,
            target_word_count=target_word_count,
            draft_budget_tokens=6_000,
        )
        return user

    @staticmethod
    def _build_writer_user(
        *,
        message: str,
        chapter: str,
        current_text: str,
        has_selection: bool,
        target_word_count: int,
        draft_budget_tokens: int,
    ) -> tuple[str, Dict[str, Any]]:
        parts = [f"本章 ID：{chapter}"]
        body = str(current_text or "")
        original_accounting = count_text_tokens(body)
        projected = False
        if body.strip():
            body, projected = ContextAssemblyService.project_draft_to_tokens(
                body,
                budget_tokens=max(1, int(draft_budget_tokens)),
            )
            parts.append(f"【当前正文】\n{body}")
        else:
            parts.append("【当前正文】（空）")
        if has_selection:
            parts.append("（用户在编辑器中有选中片段，优先聚焦该处修改。）")
        parts.append(f"\n用户指令：{str(message or '').strip()}")
        if not body.strip():
            parts.append(
                f"请先检索必要设定，再用 write_content 写出**完整一整章**（目标约 {target_word_count} 字，写足写完，勿只开头）。"
            )
        else:
            parts.append("请先检索必要设定，再用写作工具完成本轮。")
        return "\n".join(parts), {
            "projected": projected,
            "original_tokens": original_accounting.upper_bound_tokens,
            "pushed_tokens": count_text_tokens(body).upper_bound_tokens,
        }

    @staticmethod
    def project_draft_to_tokens(body: str, *, budget_tokens: int) -> tuple[str, bool]:
        text = str(body or "")
        if count_text_tokens(text).upper_bound_tokens <= budget_tokens:
            return text, False
        marker = "\n…（中段按 token 预算省略；完整正文可通过 read_chapter 恢复）…\n"
        low, high = 1, max(1, len(text) // 2)
        best = marker
        while low <= high:
            half = (low + high) // 2
            candidate = text[:half] + marker + text[-half:]
            if count_text_tokens(candidate).upper_bound_tokens <= budget_tokens:
                best = candidate
                low = half + 1
            else:
                high = half - 1
        return best, True
