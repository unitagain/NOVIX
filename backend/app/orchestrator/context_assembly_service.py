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
        existing_chapters: Optional[List[str]] = None,
        outline_push: str = "",
        relations_push: str = "",
        outline_enabled: bool = True,
        clarification_policy: str = "",
    ) -> WriterRequest:
        system = self.build_writer_system(
            has_draft=bool(str(current_text or "").strip()),
            has_chapter=bool(str(chapter or "").strip()),
            target_word_count=target_word_count,
            outline_enabled=outline_enabled,
        )
        # require_consult 开启时把大纲推入 system 稳定前缀（缓存友好、高信号）：AI 须遵循整体规划。
        # 大纲是规划意图，不是已发生事实——不进 Canon/Summary。
        outline_text = str(outline_push or "").strip()
        if outline_text:
            system = (
                f"{system}\n\n【全文规划大纲（本作整体结构与走向，撰写本章须遵循，不得偏离主线）】\n{outline_text}"
            )
        # 卡片层作者设定的人物关系与称呼（U4）：规模有界且决定每句对白的称呼，
        # 因此与风格卡同属确定性必选项，默认推入稳定前缀，不依赖模型主动调用 query_relations。
        relations_text = str(relations_push or "").strip()
        if relations_text:
            system = (
                f"{system}\n\n【人物关系与称呼（作者设定，写对白必须据此称呼，不得自造昵称）】\n"
                f"读法：`A —[关系]→ B` 表示 A 是 B 的该关系；括号内为双方当面的称呼。\n"
                f"{relations_text}"
            )
        policy_text = str(clarification_policy or "").strip()
        if policy_text:
            system = f"{system}\n\n【反问工具策略】\n{policy_text}"
        input_budget = int((getattr(context_plan, "budget", {}) or {}).get("input_tokens") or 0)
        draft_budget = min(12_000, max(3_000, int(input_budget * 0.60))) if input_budget else 6_000
        user, draft_projection = self._build_writer_user(
            message=message,
            chapter=chapter,
            current_text=current_text,
            has_selection=has_selection,
            target_word_count=target_word_count,
            draft_budget_tokens=draft_budget,
            existing_chapters=existing_chapters,
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
                    "source_id": "project.volume_order",
                    "asset_type": "volume_order",
                    "content": list(existing_chapters or []),
                    "selection_reason": "chapter_target_resolution",
                    "artifact_ref": "draft_storage:list_chapters",
                },
                {
                    "source_id": "prompt.writer.user",
                    "asset_type": "prompt",
                    "content": user,
                    "selection_reason": "writer_user_prompt_projection",
                    "artifact_ref": "ContextAssemblyService.build_writer_user",
                },
            ]
            if chapter:
                source_rows.append(
                    {
                        "source_id": "target.chapter",
                        "asset_type": "chapter",
                        "content": str(chapter),
                        "selection_reason": "target_chapter",
                        "artifact_ref": "session_chat:chapter",
                    }
                )
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
            if relations_text:
                source_rows.append(
                    {
                        "source_id": "cards.relations",
                        "asset_type": "relations",
                        "content": relations_text,
                        "selection_reason": "authored_relation_edges_push",
                        "artifact_ref": "cards/relations.yaml",
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
        if relations_text:
            # 关系边是卡片层设定：按 card 归桶上报，供给可观测（不与 canon 抽取关系混为一谈）。
            available.append("card")
            pushed.append("card")
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

    def build_writer_system(
        self,
        *,
        has_draft: bool,
        has_chapter: bool = True,
        target_word_count: int = 3000,
        outline_enabled: bool = True,
    ) -> str:
        lang = "中文" if self.language == "zh" else "英文"
        base = (
            "你是小说撰稿 agent，工作方式与 AI 编程助手一致：先理解已装配的本轮上下文，必要时用检索工具核对设定，再用写作工具落笔。"
            "『写新内容』与『改旧文』不是两件事，而是你的两个工具，由你看着当前正文自主选择：\n"
            "- create_chapter(chapter_id?, title): 为新章节建立规范化目标；完整写作并 finish_turn 成功后由系统可靠保存。\n"
            "- write_content(content[, mode]): 写入整章/整段正文（mode=replace 覆盖 / append 续写），"
            "content 是你直接创作的小说正文本身。\n"
            "- edit_lines(old_text, new_text): 精确替换正文中唯一出现的一处片段，用于局部修改/润色/删减"
            "（old_text 须与正文逐字一致且唯一）。\n"
            "- ask_clarification(questions): 在当前上下文已注入、必要检索完成后，若仍存在会实质影响结果的作者决策缺口，提出 1-3 个由你自行选择的问题；这是可选工具，每轮最多调用一次，调用后本轮暂停等待作者回答。\n"
            "检索工具（lookup_card/query_canon/query_relations/read_chapter/search_prose）供你按需"
            "核对人物设定、关系、伏笔与已确立事实，避免前后矛盾。\n\n"
        )
        if outline_enabled:
            base += (
                "大纲工具：read_outline 查阅全文规划；edit_outline 维护大纲"
                "（mode=edit 精确替换一处 / append 追加 / replace 整体重写），写入立即生效。"
                "只有作者要求调整规划时才调用 edit_outline，不要因为写完本章就顺手改写作者的大纲。\n\n"
            )
        base += (
            "工作原则：\n"
            "1) 先利用已注入上下文作判断，只有必要时再检索；检索要克制：通常查证 1–3 次关键设定/事实即应作出决定，切勿反复检索或空转——"
            "工具轮次有限，迟迟不调用 write_content/edit_lines 会导致本轮无正文产出。\n"
            "2) 选对工具：用户要求新建章节或当前没有章节 → 先 create_chapter；正文为空或需大段新内容 → write_content；只改局部 → edit_lines。\n"
            "3) 若上下文仍不足以确定关键走向，再由你决定是否调用 ask_clarification；问题必须具体、与本轮写作直接相关，不能泛问。调用后不得再调用写作工具或 finish_turn。\n"
            "4) content 只含小说正文，不夹带解释、标题或标记。\n"
            "5) 无论本轮是否修改正文，最后都必须调用 finish_turn，不能直接用自然语言结束。"
            "由你自行判断 change_type：普通交流=conversation；只改措辞/节奏=prose_edit；"
            "写新章或整体重写=chapter_write；改变事件、关系、人物状态或设定=plot_edit。\n"
            "6) 只有 chapter_write/plot_edit 才能提交章节摘要和事实候选。每条事实必须提供最终正文中逐字存在的 evidence；"
            "纯润色和普通交流必须 fact_operation=none。完成说明写入 finish_turn.message。\n"
            "7) 工具调用前的可见说明整轮最多一句，只在确有用户价值时说明当前目标。不要逐步复述“开始检索、开始写入、"
            "写作完成、提交收尾”等工具生命周期；这些动作由界面的工具轨迹展示。\n"
        )
        if not has_chapter:
            base += (
                "当前没有选中章节。普通交流可直接 finish_turn；若用户要求写作或新建章节，必须先调用 "
                "create_chapter 建立章节 ID 和标题，再调用 write_content，禁止在无目标章节时直接写正文。\n"
            )
        elif has_draft:
            base += (
                "本章已有正文（见用户消息）。这是『编辑』场景：优先用 edit_lines 做针对性的局部修改/润色"
                "（按需改写，不必长篇）；仅当用户明确要求重写整章时才 write_content(replace)。\n"
            )
        else:
            base += (
                f"本章暂无正文。这是『撰写整章』场景：必须用 write_content **一次写出完整的一整章**，"
                f"目标约 {target_word_count} 字（这是最低完成基线，不是上限；若情节需要可自然写到 {int(target_word_count * 1.5)} 字），"
                "要有起承转合、场景与对白充分展开，写到本章自然收束——绝不能只写开头、提纲或片段就停。"
                "必须投入篇幅描写感官细节、人物动作与心理、环境氛围、对白潜台词和因果转折；避免概述、流水账、重复句式和仓促收尾。"
                "先在内部规划场景节拍，再一次性写出完整正文，直到冲突和情绪完成释放。\n"
            )
        return f"{base}\n请用{lang}创作。"

    @staticmethod
    def build_writer_user(
        *,
        message: str,
        chapter: str,
        current_text: str,
        has_selection: bool,
        target_word_count: int = 3000,
        existing_chapters: Optional[List[str]] = None,
    ) -> str:
        user, _projection = ContextAssemblyService._build_writer_user(
            message=message,
            chapter=chapter,
            current_text=current_text,
            has_selection=has_selection,
            target_word_count=target_word_count,
            draft_budget_tokens=6_000,
            existing_chapters=existing_chapters,
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
        existing_chapters: Optional[List[str]] = None,
    ) -> tuple[str, Dict[str, Any]]:
        parts = [f"当前章节 ID：{chapter or '未选择'}"]
        chapters = [str(item) for item in (existing_chapters or []) if str(item).strip()]
        parts.append(f"现有章节：{', '.join(chapters) if chapters else '暂无'}")
        body = str(current_text or "")
        original_accounting = count_text_tokens(body)
        projected = False
        if body.strip():
            body, projected = ContextAssemblyService.project_draft_to_tokens(
                body,
                budget_tokens=max(1, int(draft_budget_tokens)),
            )
            parts.append(f"【当前正文】\n{body}")
            if projected:
                parts.append("【上下文完整性】当前正文因预算仅展示首尾；任何续写/修改前必须调用 read_chapter 获取真实最新正文，禁止依据省略段落臆写或覆盖旧内容。")
        else:
            parts.append("【当前正文】（空）")
        if has_selection:
            parts.append("（用户在编辑器中有选中片段，优先聚焦该处修改。）")
        parts.append(f"\n用户指令：{str(message or '').strip()}")
        if not chapter:
            parts.append(
                "若本轮需要写作，先调用 create_chapter 建立新章节目标，再调用 write_content；普通交流则直接 finish_turn。"
            )
        elif not body.strip():
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
