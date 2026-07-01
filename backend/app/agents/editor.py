# -*- coding: utf-8 -*-
"""
WenShape Editor Agent.

负责根据用户反馈修订草稿，支持：
- 全文分段定位后的定点替换
- 显式选区编辑
- patch ops 兜底
- 续写型请求的末尾追加
"""

import re
from typing import Dict, Any, List, Optional
from app.agents.base import BaseAgent
from app.utils.text import normalize_newlines, normalize_for_compare
from app.prompts import (
    EDITOR_REJECTED_CONCEPTS_INSTRUCTION,
    get_editor_system_prompt,
    EDITOR_PATCH_END_ANCHOR,
    editor_append_only_prompt,
    editor_locate_blocks_prompt,
    editor_patch_ops_prompt,
    editor_selection_replace_prompt,
)
from app.utils.logger import get_logger
from app.utils.version import increment_version

logger = get_logger(__name__)


class EditorAgent(BaseAgent):
    """
    编辑智能体。

    默认优先采用“全文分段定位 -> 定点替换”方案；
    当定位失败时，再回退到 patch ops 或追加续写模式。
    """

    def get_agent_name(self) -> str:
        """返回智能体名称。"""
        return "editor"

    def get_system_prompt(self) -> str:
        """返回编辑智能体的系统提示词。"""
        return get_editor_system_prompt(language=self.language)

    async def execute(self, project_id: str, chapter: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行修订流程并保存新版本草稿。
        """
        draft_version = context.get("draft_version", "v1")
        draft = await self.draft_storage.get_draft(project_id, chapter, draft_version)
        if not draft:
            return {"success": False, "error": f"Draft {draft_version} not found"}
        user_feedback = context.get("user_feedback", "")
        if not user_feedback:
            return {"success": False, "error": "User feedback is required"}
        style_card = await self.card_storage.get_style_card(project_id)
        rejected_entities = context.get("rejected_entities", [])
        memory_pack = context.get("memory_pack")
        revised_content = await self._generate_revision_from_feedback(
            original_draft=draft.content,
            user_feedback=user_feedback,
            style_card=style_card,
            rejected_entities=rejected_entities,
            memory_pack=memory_pack,
        )
        new_version = increment_version(draft_version)
        word_count = len(revised_content)
        revised_draft = await self.draft_storage.save_draft(
            project_id=project_id,
            chapter=chapter,
            version=new_version,
            content=revised_content,
            word_count=word_count,
            pending_confirmations=[],
        )
        return {"success": True, "draft": revised_draft, "version": new_version, "word_count": word_count}

    async def _generate_revision_from_feedback(
        self,
        original_draft: str,
        user_feedback: str,
        style_card: Any = None,
        rejected_entities: List[str] = None,
        memory_pack: Optional[Dict[str, Any]] = None,
        config_agent: str = None,
    ) -> str:
        """
        对外主入口：根据用户反馈生成修订稿。

        当前项目已完整采用新方案，因此这里直接转入新的核心链路，
        不再保留旧的未选区 patch-first 主流程。
        """
        return await self._generate_revision_from_feedback_core(
            original_draft=original_draft,
            user_feedback=user_feedback,
            style_card=style_card,
            rejected_entities=rejected_entities,
            memory_pack=memory_pack,
            config_agent=config_agent,
        )

    def _build_editor_context_items(
        self,
        style_card: Any = None,
        rejected_entities: Optional[List[str]] = None,
        memory_pack: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        context_items: List[str] = []
        if style_card:
            try:
                context_items.append(f"Style: {style_card.style}")
            except (AttributeError, TypeError) as exc:
                logger.warning("Failed to add style guidance: %s", exc)
        if rejected_entities:
            context_items.append(
                "拒绝概念：" + ", ".join(rejected_entities) + "\n" + EDITOR_REJECTED_CONCEPTS_INSTRUCTION
            )
        if memory_pack:
            context_items.extend(self._format_memory_pack_context(memory_pack))
        return context_items

    def _has_append_intent(self, feedback: str) -> bool:
        fb = str(feedback or "").strip()
        if not fb:
            return False
        keywords = (
            "续写",
            "接着写",
            "继续写",
            "往后写",
            "补完",
            "补全结尾",
            "补充结尾",
            "扩写结尾",
            "continue",
            "append",
            "continue writing",
        )
        return any(key in fb for key in keywords)

    def _requires_change(self, feedback: str) -> bool:
        fb = str(feedback or "").strip()
        if not fb:
            return False
        negatives = (
            "不用改",
            "无需修改",
            "不用修改",
            "保持不变",
            "维持原样",
            "只分析",
            "先分析",
            "帮我看看",
            "点评一下",
            "解释一下",
        )
        if any(key in fb for key in negatives):
            return False
        positives = (
            "修改",
            "改成",
            "改为",
            "删掉",
            "删除",
            "去掉",
            "替换",
            "润色",
            "重写",
            "精简",
            "压缩",
            "扩写",
            "补充",
            "补全",
            "调整",
            "优化",
            "强化",
            "弱化",
            "更",
            "让",
            "使",
        )
        return any(key in fb for key in positives)

    def _extract_quoted_candidates(self, feedback: str) -> List[str]:
        fb = str(feedback or "")
        candidates: List[str] = []
        patterns = (
            r"“([^”]{2,240})”",
            r"\"([^\"]{2,240})\"",
            r"「([^」]{2,240})」",
            r"'([^']{2,240})'",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, fb):
                value = str(match.group(1) or "").strip()
                if value and value not in candidates:
                    candidates.append(value)
        candidates.sort(key=len, reverse=True)
        return candidates

    def _extract_terms(self, feedback: str) -> List[str]:
        fb = str(feedback or "")
        raw = re.findall(r"[\u4e00-\u9fff]{2,8}|[a-zA-Z0-9_]{3,}", fb)
        stop = {
            "请帮我",
            "帮我把",
            "这一段",
            "这段话",
            "这里的",
            "开头",
            "结尾",
            "中间",
            "内容",
            "文字",
            "语气",
            "风格",
            "修改",
            "改成",
            "改为",
            "调整",
            "优化",
            "润色",
            "扩写",
            "补充",
            "删除",
        }
        terms: List[str] = []
        for token in raw:
            value = str(token or "").strip()
            if not value or value in stop:
                continue
            if value not in terms:
                terms.append(value)
            if len(terms) >= 12:
                break
        return terms

    def _iter_paragraph_spans(self, text: str) -> List[Dict[str, int]]:
        spans: List[Dict[str, int]] = []
        start = 0
        total = len(text)
        while start < total:
            while start < total and text[start] in ("\n", "\r", " ", "\t"):
                start += 1
            if start >= total:
                break
            match = re.search(r"\n\s*\n", text[start:])
            if not match:
                spans.append({"start": start, "end": total})
                break
            end = start + match.start()
            spans.append({"start": start, "end": end})
            start = start + match.end()
        return spans

    def _score_span(self, text: str, start: int, end: int, terms: List[str]) -> float:
        segment = text[start:end]
        score = 0.0
        for term in terms:
            count = segment.count(term)
            if not count:
                continue
            score += min(4, count) * (1.0 + min(5, len(term)) * 0.15)
        return score

    def _has_strong_edit_intent(self, feedback: str) -> bool:
        fb = str(feedback or "").strip()
        if not fb:
            return False
        keywords = (
            "删除",
            "删掉",
            "去掉",
            "移除",
            "改为",
            "改成",
            "替换",
            "修改",
            "精简",
            "压缩",
            "扩写",
            "补全",
            "补充",
            "续写",
            "完善",
            "润色",
            "重写",
            "调整",
            "优化",
        )
        return any(key in fb for key in keywords)

    def _auto_locate_selection(self, text: str, feedback: str) -> Optional[Dict[str, Any]]:
        if not str(text or "").strip():
            return None

        for candidate in self._extract_quoted_candidates(feedback):
            pos = text.find(candidate)
            if pos >= 0:
                return {"start": pos, "end": pos + len(candidate), "selection_text": candidate}

        spans = self._iter_paragraph_spans(text)
        if not spans:
            return None

        fb = str(feedback or "")
        if any(key in fb for key in ("结尾", "末尾", "最后")):
            span = spans[-1]
            return {
                "start": span["start"],
                "end": span["end"],
                "selection_text": text[span["start"] : span["end"]],
            }
        if any(key in fb for key in ("开头", "开篇", "第一段")):
            span = spans[0]
            return {
                "start": span["start"],
                "end": span["end"],
                "selection_text": text[span["start"] : span["end"]],
            }

        match = re.search(r"第\s*(\d{1,3})\s*段", fb)
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(spans):
                span = spans[idx]
                return {
                    "start": span["start"],
                    "end": span["end"],
                    "selection_text": text[span["start"] : span["end"]],
                }

        terms = self._extract_terms(feedback)
        if not terms:
            return None

        best_span: Optional[Dict[str, int]] = None
        best_score = 0.0
        for span in spans:
            score = self._score_span(text, span["start"], span["end"], terms)
            if score > best_score:
                best_score = score
                best_span = span
        if not best_span or best_score <= 0:
            return None

        start = int(best_span["start"])
        end = int(best_span["end"])
        if end - start > 1400:
            segment = text[start:end]
            anchor_pos = None
            for term in sorted(terms, key=len, reverse=True):
                local_pos = segment.find(term)
                if local_pos >= 0:
                    anchor_pos = start + local_pos
                    break
            anchor_pos = start if anchor_pos is None else anchor_pos
            start = max(start, anchor_pos - 260)
            end = min(end, anchor_pos + 980)

        return {"start": start, "end": end, "selection_text": text[start:end]}

    def _split_paragraph_blocks(self, text: str) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        normalized = normalize_newlines(text or "")
        for idx, span in enumerate(self._iter_paragraph_spans(normalized), start=1):
            block_text = normalized[span["start"] : span["end"]]
            if not block_text.strip():
                continue
            blocks.append(
                {
                    "id": f"P{idx}",
                    "index": idx,
                    "start": span["start"],
                    "end": span["end"],
                    "text": block_text,
                }
            )
        return blocks

    def _clip_block_text(self, text: str, max_chars: Optional[int]) -> str:
        value = str(text or "").strip()
        if not max_chars or len(value) <= max_chars:
            return value
        keep = max(40, max_chars - 24)
        return value[:keep].rstrip() + "\n...[已截断]..."

    def _format_indexed_document(
        self,
        blocks: List[Dict[str, Any]],
        max_chars_per_block: Optional[int],
        max_total_chars: int,
    ) -> str:
        parts: List[str] = []
        used = 0
        total_blocks = len(blocks)
        for block in blocks:
            rendered = self._clip_block_text(block["text"], max_chars_per_block)
            part = f"[{block['id']}]\n{rendered}"
            if used and used + len(part) > max_total_chars:
                remaining = max(total_blocks - len(parts), 0)
                if remaining:
                    parts.append(f"...[其余 {remaining} 个段落已省略]...")
                break
            parts.append(part)
            used += len(part)
        return "\n\n".join(parts).strip()

    def _resolve_block_ids(
        self,
        blocks: List[Dict[str, Any]],
        block_ids: List[str],
        max_rewrite_chars: int = 6000,
    ) -> Optional[Dict[str, Any]]:
        if not blocks or not block_ids:
            return None
        block_map = {str(block["id"]).upper(): block for block in blocks}
        selected = [block_map[block_id.upper()] for block_id in block_ids if block_id.upper() in block_map]
        if not selected:
            return None
        selected.sort(key=lambda item: int(item["index"]))
        start_block = selected[0]
        end_block = selected[-1]
        merged = [
            block for block in blocks if int(start_block["index"]) <= int(block["index"]) <= int(end_block["index"])
        ]
        start = int(merged[0]["start"])
        end = int(merged[-1]["end"])
        if end <= start or end - start > max_rewrite_chars:
            return None
        selection_text = normalize_newlines(
            "\n\n".join(block["text"].strip() for block in merged if block["text"].strip())
        )
        return {
            "start": start,
            "end": end,
            "selection_text": selection_text,
            "block_ids": [block["id"] for block in merged],
        }

    async def _locate_selection_from_document(
        self,
        original_draft: str,
        user_feedback: str,
        context_items: List[str],
        config_agent: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        blocks = self._split_paragraph_blocks(original_draft)
        if not blocks:
            return None

        short_document = len(original_draft) <= 16000
        indexed_document = self._format_indexed_document(
            blocks=blocks,
            max_chars_per_block=None if short_document else 260,
            max_total_chars=18000,
        )
        prompt = editor_locate_blocks_prompt(
            indexed_document=indexed_document,
            user_feedback=user_feedback,
            language=self.language,
        )
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=context_items,
        )
        data, err, raw = await self.call_llm_json(messages, expected_type=dict, config_agent=config_agent)
        raw = str(raw or "").strip()
        if err or not isinstance(data, dict):
            logger.info("Document locate parse failed: err=%s raw=%s", err, raw[:160])
            return None

        raw_ids = data.get("block_ids")
        if not isinstance(raw_ids, list):
            return None
        block_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
        located = self._resolve_block_ids(blocks, block_ids)
        if located:
            return located

        logger.info("Document locate returned invalid or oversized blocks: %s", block_ids)
        return None

    async def _selection_replace_with_context(
        self,
        original: str,
        start: int,
        end: int,
        selection_text: Optional[str],
        feedback: str,
        context_items: List[str],
        agent: Optional[str],
    ) -> str:
        original_norm = normalize_newlines(original or "")
        start = max(0, min(int(start), len(original_norm)))
        end = max(0, min(int(end), len(original_norm)))
        if end < start:
            start, end = end, start
        if end <= start:
            raise ValueError("selection_replace_invalid_range")

        selected = original_norm[start:end]
        provided = normalize_newlines(selection_text or "").strip("\n")
        if provided and normalize_for_compare(provided) != normalize_for_compare(selected):
            raise ValueError("selection_replace_mismatch")

        prefix_hint = original_norm[max(0, start - 220) : start]
        suffix_hint = original_norm[end : min(len(original_norm), end + 220)]
        prompt = editor_selection_replace_prompt(
            selection_text=selected,
            user_feedback=feedback,
            prefix_hint=prefix_hint,
            suffix_hint=suffix_hint,
            language=self.language,
        )
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=context_items,
        )
        response = await self.call_llm(messages, config_agent=agent, return_meta=True)
        replacement = normalize_newlines(str(response.get("content") or "")).strip("\n")
        if not replacement.strip():
            raise ValueError("selection_replace_empty")

        revised = (original_norm[:start] + replacement + original_norm[end:]).rstrip()
        if normalize_for_compare(revised) != normalize_for_compare(original_norm):
            return revised

        retry_messages = list(messages)
        retry_messages.append({"role": "assistant", "content": str(response.get("content") or "")})
        retry_messages.append(
            {
                "role": "user",
                "content": "\n".join(
                    [
                        "你刚才的替换没有产生可见变更。",
                        "请重新输出替换后的选区文本，并确保与原选区不同。",
                        "- 必须真实执行用户修改要求",
                        "- 只能返回替换文本，不要解释",
                    ]
                ),
            }
        )
        response2 = await self.call_llm(retry_messages, config_agent=agent, return_meta=True)
        replacement2 = normalize_newlines(str(response2.get("content") or "")).strip("\n")
        if not replacement2.strip():
            raise ValueError("selection_replace_empty_retry")

        revised2 = (original_norm[:start] + replacement2 + original_norm[end:]).rstrip()
        if normalize_for_compare(revised2) == normalize_for_compare(original_norm):
            raise ValueError("selection_replace_no_effect")
        return revised2

    async def _append_only_revision(
        self,
        original_draft: str,
        user_feedback: str,
        context_items: List[str],
        config_agent: Optional[str],
    ) -> str:
        tail_excerpt = normalize_newlines(original_draft)[-1800:]
        prompt = editor_append_only_prompt(
            tail_excerpt=tail_excerpt,
            user_feedback=user_feedback,
            language=self.language,
        )
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=context_items,
        )
        response = await self.call_llm(messages, config_agent=config_agent, return_meta=True)
        append_text = normalize_newlines(str(response.get("content") or "")).strip()
        if not append_text:
            return normalize_newlines(original_draft).rstrip()

        base = normalize_newlines(original_draft).rstrip()
        separator = "\n\n" if base else ""
        return (base + separator + append_text).rstrip()

    async def _attempt_patch_revision(
        self,
        original_draft: str,
        user_feedback: str,
        context_items: List[str],
        config_agent: Optional[str],
        memory_pack: Optional[Dict[str, Any]],
        strong_intent: bool,
        append_intent: bool,
    ) -> str:
        excerpts = self._build_patch_excerpts(
            original_draft=original_draft,
            user_feedback=user_feedback,
            memory_pack=memory_pack,
        )
        prompt = editor_patch_ops_prompt(excerpts=excerpts, user_feedback=user_feedback, language=self.language)
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=context_items,
        )

        raw = ""
        try:
            data, err, raw = await self.call_llm_json(messages, expected_type=dict, config_agent=config_agent)
            raw = str(raw or "").strip()
            if err or not isinstance(data, dict):
                logger.warning("Patch ops parse failed: err=%s raw=%s", err, raw[:200] if raw else "empty")
                raise ValueError(f"patch_ops_parse_failed: {err}")

            ops = data.get("ops")
            if not isinstance(ops, list):
                raise ValueError("patch_ops_invalid_schema: missing ops list")

            revised, _ = self._apply_patch_ops(original_draft, ops)
            if normalize_for_compare(revised) != normalize_for_compare(original_draft):
                return revised

            if strong_intent and not append_intent:
                located = self._auto_locate_selection(original_draft, user_feedback)
                if located:
                    revised_auto = await self._selection_replace_with_context(
                        original=original_draft,
                        start=int(located["start"]),
                        end=int(located["end"]),
                        selection_text=str(located.get("selection_text") or ""),
                        feedback=user_feedback,
                        context_items=context_items,
                        agent=config_agent,
                    )
                    if normalize_for_compare(revised_auto) != normalize_for_compare(original_draft):
                        return revised_auto
                raise ValueError("patch_ops_no_effect")

            if append_intent:
                return await self._append_only_revision(
                    original_draft=original_draft,
                    user_feedback=user_feedback,
                    context_items=context_items,
                    config_agent=config_agent,
                )
            return revised
        except Exception as exc:
            retry_excerpts = self._build_patch_excerpts(
                original_draft=original_draft,
                user_feedback=user_feedback,
                memory_pack=memory_pack,
                head_chars=1600,
                tail_chars=2600,
                window=240,
                max_excerpts=10,
            )
            retry_messages = list(messages)
            retry_messages.append({"role": "assistant", "content": raw})
            retry_messages.append(
                {
                    "role": "user",
                    "content": "\n".join(
                        [
                            "你刚才输出的 patch ops 无法应用到原文。",
                            f"错误：{exc}",
                            "请重新输出 JSON，并确保：",
                            "- replace/delete 的 before 能在摘录中逐字匹配",
                            "- insert_* 的 anchor 能在摘录中逐字匹配",
                            "- 尽量减少 op 数量，只改必要位置",
                            "- 应用后必须产生可见变更",
                            "",
                            "原文摘录：",
                            "<<<EXCERPTS_START>>>",
                            retry_excerpts or "",
                            "<<<EXCERPTS_END>>>",
                        ]
                    ),
                }
            )
            data2, err2, raw2 = await self.call_llm_json(retry_messages, expected_type=dict, config_agent=config_agent)
            raw2 = str(raw2 or "").strip()
            if err2 or not isinstance(data2, dict):
                logger.error("Patch ops retry parse failed: err=%s raw=%s", err2, raw2[:200] if raw2 else "empty")
                raise ValueError(f"patch_ops_retry_parse_failed: {err2}") from exc

            ops2 = data2.get("ops")
            if not isinstance(ops2, list):
                raise ValueError("patch_ops_retry_invalid_schema: missing ops list") from exc

            revised2, _ = self._apply_patch_ops(original_draft, ops2)
            if normalize_for_compare(revised2) != normalize_for_compare(original_draft):
                return revised2

            if append_intent:
                return await self._append_only_revision(
                    original_draft=original_draft,
                    user_feedback=user_feedback,
                    context_items=context_items,
                    config_agent=config_agent,
                )

            if strong_intent:
                located = self._auto_locate_selection(original_draft, user_feedback)
                if located:
                    try:
                        revised_auto = await self._selection_replace_with_context(
                            original=original_draft,
                            start=int(located["start"]),
                            end=int(located["end"]),
                            selection_text=str(located.get("selection_text") or ""),
                            feedback=user_feedback,
                            context_items=context_items,
                            agent=config_agent,
                        )
                        if normalize_for_compare(revised_auto) != normalize_for_compare(original_draft):
                            return revised_auto
                    except Exception as auto_exc:
                        logger.info("Auto-selection fallback failed: %s", auto_exc)

                raise ValueError("未能生成可应用的修改：请直接引用要修改的原句，或使用选区编辑进行精确定位。") from exc

            return revised2

    async def _generate_revision_from_feedback_core(
        self,
        original_draft: str,
        user_feedback: str,
        style_card: Any = None,
        rejected_entities: List[str] = None,
        memory_pack: Optional[Dict[str, Any]] = None,
        config_agent: str = None,
    ) -> str:
        original_draft = normalize_newlines(original_draft or "")
        user_feedback = str(user_feedback or "").strip()

        context_items = self._build_editor_context_items(
            style_card=style_card,
            rejected_entities=rejected_entities or [],
            memory_pack=memory_pack,
        )
        append_intent = self._has_append_intent(user_feedback)
        strong_intent = self._has_strong_edit_intent(user_feedback) or self._requires_change(user_feedback)

        if strong_intent and not append_intent:
            located = await self._locate_selection_from_document(
                original_draft=original_draft,
                user_feedback=user_feedback,
                context_items=context_items,
                config_agent=config_agent,
            )
            if located:
                try:
                    revised = await self._selection_replace_with_context(
                        original=original_draft,
                        start=int(located["start"]),
                        end=int(located["end"]),
                        selection_text=str(located.get("selection_text") or ""),
                        feedback=user_feedback,
                        context_items=context_items,
                        agent=config_agent,
                    )
                    if normalize_for_compare(revised) != normalize_for_compare(original_draft):
                        return revised
                except Exception as exc:
                    logger.info("Document-locate replace failed, falling back to patch mode: %s", exc)

        return await self._attempt_patch_revision(
            original_draft=original_draft,
            user_feedback=user_feedback,
            context_items=context_items,
            config_agent=config_agent,
            memory_pack=memory_pack,
            strong_intent=strong_intent,
            append_intent=append_intent,
        )

    def _build_patch_excerpts(
        self,
        original_draft: str,
        user_feedback: str,
        memory_pack: Optional[Dict[str, Any]] = None,
        head_chars: int = 900,
        tail_chars: int = 1400,
        window: int = 180,
        max_excerpts: int = 8,
    ) -> str:
        """
        构建 patch ops 使用的紧凑摘录上下文。
        """
        text = normalize_newlines(original_draft)
        if not text.strip():
            return ""
        head = text[:head_chars].strip()
        tail = text[-tail_chars:].strip() if len(text) > tail_chars else text.strip()
        terms: List[str] = []
        # Extract keywords from user feedback and memory for context matching
        for part in re.findall(r"[\u4e00-\u9fff]{2,8}|[a-zA-Z0-9_]{3,}", str(user_feedback or "")):
            part = part.strip()
            if part and part not in terms:
                terms.append(part)
            if len(terms) >= 10:
                break
        if isinstance(memory_pack, dict):
            digest = memory_pack.get("chapter_digest")
            if isinstance(digest, dict):
                for name in (digest.get("top_characters") or [])[:6]:
                    n = str(name or "").strip()
                    if n and n not in terms:
                        terms.append(n)
            snapshot = memory_pack.get("card_snapshot")
            if isinstance(snapshot, dict):
                for item in (snapshot.get("characters") or [])[:6]:
                    if isinstance(item, dict):
                        n = str(item.get("name") or "").strip()
                        if n and n not in terms:
                            terms.append(n)
        # Find local context windows around feedback keywords.
        ranges: List[tuple[int, int]] = []
        for term in terms[:10]:
            start = 0
            hits = 0
            while hits < 2:
                pos = text.find(term, start)
                if pos < 0:
                    break
                left = max(0, pos - window)
                right = min(len(text), pos + len(term) + window)
                ranges.append((left, right))
                start = pos + len(term)
                hits += 1
        ranges.sort()
        # Merge overlapping ranges to avoid duplication
        merged: List[tuple[int, int]] = []
        for left, right in ranges:
            if not merged:
                merged.append((left, right))
                continue
            prev_left, prev_right = merged[-1]
            if left <= prev_right:
                merged[-1] = (prev_left, max(prev_right, right))
            else:
                merged.append((left, right))
        excerpt_blocks: List[str] = []
        if head:
            excerpt_blocks.append("[Head Excerpt]\n" + head)
        picked = 0
        for left, right in merged:
            if picked >= max_excerpts:
                break
            frag = text[left:right].strip()
            if not frag:
                continue
            excerpt_blocks.append("[Relevant Excerpt]\n" + frag)
            picked += 1
        if tail:
            excerpt_blocks.append("[Tail Excerpt]\n" + tail)
        return "\n\n".join(excerpt_blocks).strip()

    def _apply_patch_ops(self, original_draft: str, raw_ops: List[Any]) -> tuple[str, int]:
        """
        Apply patch ops to the full draft.
        Safety strategy:
        - Position matching relies on exact before/anchor occurrences.
        - Resolve locations first, then apply from back to front.
        """
        text = normalize_newlines(original_draft)
        if not isinstance(raw_ops, list) or not raw_ops:
            return text.rstrip(), 0

        def find_nth(haystack: str, needle: str, occurrence: int) -> int:
            if not needle:
                return -1
            occurrence = max(int(occurrence or 1), 1)
            start = 0
            for _ in range(occurrence):
                pos = haystack.find(needle, start)
                if pos < 0:
                    return -1
                start = pos + len(needle)
            return pos

        resolved: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_ops[:12]):
            if not isinstance(item, dict):
                continue
            op = str(item.get("op") or "").strip()
            occurrence = item.get("occurrence") or 1
            if op in {"replace", "delete"}:
                before = str(item.get("before") or "")
                if not before.strip():
                    continue
                pos = find_nth(text, before, occurrence)
                if pos < 0:
                    raise ValueError(f"patch_op_failed: before_not_found op#{idx + 1}")
                resolved.append(
                    {
                        "op": op,
                        "start": pos,
                        "end": pos + len(before),
                        "after": "" if op == "delete" else str(item.get("after") or ""),
                    }
                )
                continue
            if op in {"insert_before", "insert_after"}:
                anchor = str(item.get("anchor") or "")
                content = str(item.get("content") or "")
                if not anchor.strip() or not content:
                    continue
                if anchor == EDITOR_PATCH_END_ANCHOR:
                    insert_at = len(text)
                else:
                    pos = find_nth(text, anchor, occurrence)
                    if pos < 0:
                        raise ValueError(f"patch_op_failed: anchor_not_found op#{idx + 1}")
                    insert_at = pos if op == "insert_before" else pos + len(anchor)
                resolved.append(
                    {
                        "op": op,
                        "start": insert_at,
                        "end": insert_at,
                        "after": content,
                    }
                )
                continue
        resolved.sort(key=lambda r: r["start"], reverse=True)
        for item in resolved:
            start = int(item["start"])
            end = int(item["end"])
            after = str(item.get("after") or "")
            text = text[:start] + after + text[end:]
        return text.rstrip(), len(resolved)

    async def suggest_revision(
        self,
        project_id: str,
        original_draft: str,
        user_feedback: str,
        rejected_entities: List[str] = None,
        memory_pack: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Suggest a revision without persisting it.
        Generate a revised draft for diff preview only (no persistence).
        """

        if original_draft is None:
            original_draft = ""
        if not user_feedback:
            raise ValueError("User feedback is required")
        style_card = await self.card_storage.get_style_card(project_id)
        return await self._generate_revision_from_feedback(
            original_draft=original_draft,
            user_feedback=user_feedback,
            style_card=style_card,
            rejected_entities=rejected_entities or [],
            memory_pack=memory_pack,
            config_agent="writer",
        )

    async def suggest_revision_selection(
        self,
        project_id: str,
        original_draft: str,
        selection_text: str,
        selection_occurrence: int,
        user_feedback: str,
        rejected_entities: List[str] = None,
        memory_pack: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Suggest a revision scoped to an explicit selection (exact substring).
        Only edit the selected text and return full revised content.
        """
        # Backward compatible method: locate by substring matching.
        original = normalize_newlines(original_draft)
        sel = normalize_newlines(selection_text)
        if not sel.strip():
            raise ValueError("选区编辑失败：选区为空")
        occurrence = max(int(selection_occurrence or 1), 1)
        start_cursor = 0
        pos = -1
        for _ in range(occurrence):
            pos = original.find(sel, start_cursor)
            if pos < 0:
                raise ValueError("选区编辑失败：未在正文中找到选区文本，请重新选择后重试")
            start_cursor = pos + len(sel)
        return await self.suggest_revision_selection_range(
            project_id=project_id,
            original_draft=original,
            selection_start=pos,
            selection_end=pos + len(sel),
            selection_text=sel,
            user_feedback=user_feedback,
            rejected_entities=rejected_entities,
            memory_pack=memory_pack,
        )

    async def suggest_revision_selection_range(
        self,
        project_id: str,
        original_draft: str,
        selection_start: int,
        selection_end: int,
        selection_text: Optional[str],
        user_feedback: str,
        rejected_entities: List[str] = None,
        memory_pack: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Selection editing by explicit index range (most reliable).
        Frontend passes start/end, backend only replaces that span.
        """
        original = normalize_newlines(original_draft)
        start = int(selection_start or 0)
        end = int(selection_end or 0)
        start = max(0, min(start, len(original)))
        end = max(0, min(end, len(original)))
        if end <= start:
            raise ValueError("选区编辑失败：选区为空或范围无效")
        # Hard limit: avoid prompting with overly long selection which hurts reliability.
        if end - start > 3200:
            raise ValueError("选区编辑失败：选区过长（建议 <= 1200 字符），请缩小后重试")
        sel = original[start:end]
        if selection_text is not None:
            provided = normalize_newlines(selection_text)
            if provided and provided != sel:
                raise ValueError("选区编辑失败：选区内容已变化，请重新选中后再试")
        style_card = await self.card_storage.get_style_card(project_id)
        context_items = []
        if style_card:
            try:
                context_items.append(f"Style: {style_card.style}")
            except (AttributeError, TypeError) as e:
                logger.warning("Failed to add style guidance: %s", e)
        if rejected_entities:
            context_items.append(
                "拒绝概念：" + ", ".join(rejected_entities) + "\n" + EDITOR_REJECTED_CONCEPTS_INSTRUCTION
            )
        if memory_pack:
            context_items.extend(self._format_memory_pack_context(memory_pack))
        prefix_hint = original[max(0, start - 220) : start]
        suffix_hint = original[end : min(len(original), end + 220)]
        prompt = editor_selection_replace_prompt(
            selection_text=sel,
            user_feedback=user_feedback,
            prefix_hint=prefix_hint,
            suffix_hint=suffix_hint,
            language=self.language,
        )

        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=context_items,
        )
        response = await self.call_llm(messages, config_agent="writer", return_meta=True)
        replacement = normalize_newlines(str(response.get("content") or "")).strip("\n")
        if not replacement.strip():
            raise ValueError("选区编辑失败：模型未生成替换文本，请缩小选区并细化要求")
        revised = (original[:start] + replacement + original[end:]).rstrip()
        if normalize_for_compare(revised) == normalize_for_compare(original):
            retry_messages = list(messages)
            retry_messages.append({"role": "assistant", "content": str(response.get("content") or "")})
            retry_messages.append(
                {
                    "role": "user",
                    "content": "\n".join(
                        [
                            "你刚才的替换没有产生可见变更。",
                            "请重新输出替换后的选区文本，并确保与原选区不同。",
                            "仅返回替换文本，不要解释。",
                        ]
                    ),
                }
            )
            response2 = await self.call_llm(retry_messages, config_agent="writer", return_meta=True)
            replacement2 = normalize_newlines(str(response2.get("content") or "")).strip("\n")
            if not replacement2.strip():
                raise ValueError("选区编辑失败：重试后仍未生成替换文本")
            revised2 = (original[:start] + replacement2 + original[end:]).rstrip()
            if normalize_for_compare(revised2) == normalize_for_compare(original):
                raise ValueError("选区编辑失败：未产生有效差异")
            return revised2
        return revised

    def _format_memory_pack_context(self, memory_pack: Dict[str, Any]) -> List[str]:
        """Format memory pack into compact context items for the editor."""
        payload: Any = {}
        if isinstance(memory_pack, dict):
            payload = memory_pack.get("payload") or memory_pack.get("working_memory_payload") or {}
            if not payload and any(
                key in memory_pack for key in ("working_memory", "evidence_pack", "unresolved_gaps")
            ):
                payload = memory_pack
        if not isinstance(payload, dict):
            payload = {}

        context_items: List[str] = []

        digest = memory_pack.get("chapter_digest") if isinstance(memory_pack, dict) else None
        if isinstance(digest, dict):
            parts: List[str] = []
            summary = str(digest.get("summary") or "").strip()
            if summary:
                parts.append(f"摘要：{summary}")
            top_chars = [str(x).strip() for x in (digest.get("top_characters") or []) if str(x).strip()]
            if top_chars:
                parts.append("人物：" + "、".join(top_chars[:8]))
            top_world = [str(x).strip() for x in (digest.get("top_world") or []) if str(x).strip()]
            if top_world:
                parts.append("设定：" + "、".join(top_world[:8]))
            tail = str(digest.get("tail_excerpt") or "").strip()
            if tail:
                parts.append("结尾片段：\n" + tail[:900])
            if parts:
                context_items.append("本章内容概览：\n" + "\n".join(parts))

        working_memory = payload.get("working_memory")
        if working_memory:
            context_items.append("工作记忆：\n" + str(working_memory).strip())

        evidence_pack = payload.get("evidence_pack") or {}
        evidence_items = evidence_pack.get("items") or []
        if evidence_items:

            def _score(item: Dict[str, Any]) -> float:
                try:
                    return float(item.get("score") or 0)
                except Exception:
                    return 0.0

            ordered = [item for item in evidence_items if isinstance(item, dict)]
            ordered.sort(key=_score, reverse=True)
            lines: List[str] = []
            for item in ordered:
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                item_type = str(item.get("type") or "evidence")
                source = item.get("source") or {}
                source_parts = [
                    source.get("chapter"),
                    source.get("draft"),
                    source.get("path"),
                    source.get("field"),
                    source.get("fact_id"),
                    source.get("card"),
                    source.get("introduced_in"),
                ]
                source_label = " / ".join([str(part) for part in source_parts if part])
                line = f"[{item_type}] {text}"
                if source_label:
                    line += f" ({source_label})"
                lines.append(line)
                if len(lines) >= 6:
                    break
            if lines:
                context_items.append("证据摘录：\n" + "\n".join(lines))

        unresolved_gaps = payload.get("unresolved_gaps") or []
        if unresolved_gaps:
            gap_lines: List[str] = []
            for gap in unresolved_gaps[:6]:
                text = str((gap.get("text") if isinstance(gap, dict) else gap) or "").strip()
                if text:
                    gap_lines.append(f"- {text}")
            if gap_lines:
                context_items.append("未解决缺口（勿编造）：\n" + "\n".join(gap_lines))

        snapshot = memory_pack.get("card_snapshot") if isinstance(memory_pack, dict) else None
        if isinstance(snapshot, dict):
            context_items.extend(self._format_card_snapshot(snapshot))
        return context_items

    def _format_card_snapshot(self, snapshot: Dict[str, Any]) -> List[str]:
        characters = snapshot.get("characters") or []
        world = snapshot.get("world") or []
        style = snapshot.get("style")
        context_items: List[str] = []

        if characters:
            lines: List[str] = []
            for item in characters[:8]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                stars = item.get("stars")
                star_label = f"★{stars}" if stars else ""
                appearance = str(item.get("appearance") or "").strip()
                identity = str(item.get("identity") or "").strip()
                aliases = item.get("aliases") or []
                alias_text = "、".join([str(a).strip() for a in aliases if str(a).strip()][:4])
                parts: List[str] = []
                if identity:
                    parts.append(f"身份：{identity}")
                if appearance:
                    parts.append(f"外貌：{appearance}")
                if alias_text:
                    parts.append(f"别名：{alias_text}")
                line = f"- {name}{star_label}"
                if parts:
                    line += "（" + "；".join(parts) + "）"
                lines.append(line)
            if lines:
                context_items.append("角色设定（快照）：\n" + "\n".join(lines))

        if world:
            lines = []
            for item in world[:8]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                stars = item.get("stars")
                star_label = f"★{stars}" if stars else ""
                category = str(item.get("category") or "").strip()
                description = str(item.get("description") or "").strip()
                parts = []
                if category:
                    parts.append(f"类别：{category}")
                if description:
                    parts.append(f"描述：{description[:100]}")
                line = f"- {name}{star_label}"
                if parts:
                    line += "（" + "；".join(parts) + "）"
                lines.append(line)
            if lines:
                context_items.append("世界设定（快照）：\n" + "\n".join(lines))

        if isinstance(style, dict):
            style_text = str(style.get("style") or "").strip()
            if style_text:
                context_items.append("文风卡（快照）：\n" + style_text[:800])
        return context_items
