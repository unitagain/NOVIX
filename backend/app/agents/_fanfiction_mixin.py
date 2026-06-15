# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  同人创作 Mixin - 从档案员智能体中提取的同人卡片提取、验证和修复方法。
  Fanfiction extraction mixin - Methods for fanfiction Wiki card extraction, validation, sanitization, and LLM repair fallbacks.
"""

import re
from typing import Any, Dict, List

from app.prompts import (
    FANFICTION_CARD_REPAIR_HINT_ENRICH_DESCRIPTION,
    FANFICTION_CARD_REPAIR_HINT_ENRICH_DESCRIPTION_EN,
    FANFICTION_CARD_REPAIR_HINT_STRICT_JSON,
    FANFICTION_CARD_REPAIR_HINT_STRICT_JSON_EN,
    archivist_fanfiction_card_prompt,
    archivist_fanfiction_card_repair_prompt,
)
from app.services.llm_config_service import llm_config_service
from app.utils.llm_output import parse_json_payload
from app.utils.logger import get_logger
from app.utils.text import normalize_newlines

logger = get_logger(__name__)


class FanfictionMixin:
    """
    同人创作卡片提取 Mixin。

    Methods for fanfiction Wiki card extraction and processing.
    Handles JSON parsing, validation, repair, and type inference.
    """

    async def extract_fanfiction_card(self, title: str, content: str) -> Dict[str, str]:
        """Extract a single card summary for fanfiction import."""
        clean_title = str(title or "").strip()
        clean_content = str(content or "").strip()
        if not clean_content:
            return {
                "name": clean_title or "Unknown",
                "type": self._infer_card_type_from_title(clean_title),
                "description": "",
            }

        # 当输入内容明显为中文，但项目语言为英文时，先用中文高质量提取，再单独翻译成英文。
        # 这样可以避免让 LLM 同时承担“信息提取 + 翻译”两项任务导致的降质（见 wiki.md 评估结论）。
        if self.language == "en" and self._looks_cjk_heavy_source(clean_content):
            bridged = await self._extract_fanfiction_card_via_zh_then_translate(clean_title, clean_content)
            if bridged:
                return bridged

        prompt = archivist_fanfiction_card_prompt(title=clean_title, content=clean_content, language=self.language)

        provider_id = self.gateway.get_provider_for_agent(self.get_agent_name())
        profile = llm_config_service.get_profile_by_id(provider_id) or {}
        logger.info(
            "Fanfiction extraction using provider=%s model=%s content_chars=%s",
            provider_id,
            profile.get("model"),
            len(clean_content),
        )

        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=None,
        )

        max_attempts = 5
        last_length = 0
        strict_json_hint = (
            FANFICTION_CARD_REPAIR_HINT_STRICT_JSON_EN
            if self.language == "en"
            else FANFICTION_CARD_REPAIR_HINT_STRICT_JSON
        )
        enrich_description_hint = (
            FANFICTION_CARD_REPAIR_HINT_ENRICH_DESCRIPTION_EN
            if self.language == "en"
            else FANFICTION_CARD_REPAIR_HINT_ENRICH_DESCRIPTION
        )
        for attempt in range(1, max_attempts + 1):
            response = await self.call_llm(messages, max_tokens=2600)
            logger.info("Fanfiction extraction response_chars=%s", len(response or ""))
            parsed = self._parse_json_object(response)
            if not self._is_valid_fanfiction_payload(parsed, clean_content):
                logger.warning("Fanfiction extraction JSON parse failed, retrying with strict prompt")
                parsed = await self._extract_fanfiction_json_from_content(
                    clean_title,
                    clean_content,
                    hint=strict_json_hint,
                )
            if self.language == "en":
                bridged_payload = await self._bridge_payload_to_english(
                    payload=parsed,
                    fallback_title=clean_title,
                    source=clean_content,
                )
                if self._is_valid_fanfiction_payload(bridged_payload, clean_content):
                    parsed = bridged_payload

            if not self._is_valid_fanfiction_payload(parsed, clean_content):
                continue

            name = str(parsed.get("name") or clean_title or "Unknown").strip()
            card_type = self._normalize_fanfiction_card_type(parsed.get("type")) or self._infer_card_type_from_title(
                name
            )
            description = self._sanitize_fanfiction_description(str(parsed.get("description") or "").strip())
            last_length = len(description)

            copied = bool(description) and self._is_copied_from_source(description, clean_content)
            low_quality = self._is_low_quality_fanfiction_description(description)
            needs_fields = self._english_needs_more_fields(description, card_type)
            needs_repair = (not description) or copied or low_quality or needs_fields
            if needs_repair:
                if self.language == "en":
                    stats = self._english_label_stats(description)
                    logger.warning(
                        "Fanfiction extraction needs repair copied=%s low_quality=%s needs_fields=%s labeled_paras=%s unique=%s desc_chars=%s",
                        copied,
                        low_quality,
                        needs_fields,
                        stats.get("labeled_paragraphs"),
                        sorted(list(stats.get("unique") or [])),
                        len(description or ""),
                    )
                parsed = await self._extract_fanfiction_json_from_content(
                    name,
                    clean_content,
                    hint=enrich_description_hint,
                )
                if self.language == "en":
                    bridged_payload = await self._bridge_payload_to_english(
                        payload=parsed,
                        fallback_title=name,
                        source=clean_content,
                    )
                    if self._is_valid_fanfiction_payload(bridged_payload, clean_content):
                        parsed = bridged_payload
                if self._is_valid_fanfiction_payload(parsed, clean_content):
                    description = self._sanitize_fanfiction_description(str(parsed.get("description") or "").strip())
                    last_length = len(description)
                copied = bool(description) and self._is_copied_from_source(description, clean_content)
                low_quality = self._is_low_quality_fanfiction_description(description)
                needs_fields = self._english_needs_more_fields(description, card_type)
                needs_repair = (not description) or copied or low_quality or needs_fields
                if needs_repair:
                    continue

            if card_type not in {"Character", "World"}:
                card_type = self._infer_card_type_from_title(name)

            return {
                "name": name,
                "type": card_type,
                "description": description,
            }

        # 兜底：尝试从原文提取基础摘要
        fallback_desc = self._build_fanfiction_fallback(clean_title, clean_content)
        fallback_desc = self._sanitize_fanfiction_description(fallback_desc)
        if fallback_desc and (
            self._is_low_quality_fanfiction_description(fallback_desc)
            or self._english_needs_more_fields(fallback_desc, self._infer_card_type_from_title(clean_title))
        ):
            repaired = await self._extract_fanfiction_json_from_content(
                clean_title,
                clean_content,
                hint=enrich_description_hint,
            )
            if self._is_valid_fanfiction_payload(repaired, clean_content):
                fallback_desc = self._sanitize_fanfiction_description(str(repaired.get("description") or "").strip())
        if fallback_desc and self.language == "en" and self._looks_non_english_description(fallback_desc):
            translated_desc = await self._translate_fanfiction_description_to_english(
                title=clean_title,
                description=fallback_desc,
                source=clean_content,
            )
            if translated_desc:
                fallback_desc = self._sanitize_fanfiction_description(translated_desc)
        if fallback_desc and self.language == "en" and self._looks_non_english_description(fallback_desc):
            translated = await self._extract_fanfiction_json_from_content(
                clean_title,
                clean_content,
                hint=enrich_description_hint,
            )
            if self._is_valid_fanfiction_payload(translated, clean_content):
                fallback_desc = self._sanitize_fanfiction_description(str(translated.get("description") or "").strip())
            if self._looks_non_english_description(fallback_desc):
                fallback_desc = (
                    f"Identity: {clean_title or 'Unknown'}\n\n"
                    "Writing Notes: The source content could not be reliably converted to English in this pass. "
                    "Please refine this card manually."
                )
        if fallback_desc and (
            self._is_low_quality_fanfiction_description(fallback_desc)
            or self._english_needs_more_fields(fallback_desc, self._infer_card_type_from_title(clean_title))
        ):
            logger.warning(
                "Fanfiction extraction falling back to safe skeleton: desc_chars=%s language=%s",
                len(fallback_desc or ""),
                self.language,
            )
            fallback_desc = self._build_safe_fanfiction_description(clean_title)
        if fallback_desc:
            return {
                "name": clean_title or "Unknown",
                "type": self._infer_card_type_from_title(clean_title),
                "description": fallback_desc,
            }
        raise ValueError(f"Fanfiction extraction failed: empty description (len={last_length})")

    async def _extract_fanfiction_json_from_content(
        self,
        title: str,
        content: str,
        hint: str = "",
        prompt_language: str = "",
    ) -> Dict[str, Any]:
        if not content:
            return {}
        lang = str(prompt_language or "").strip().lower() or self.language
        prompt = archivist_fanfiction_card_repair_prompt(title=title, content=content, hint=hint, language=lang)
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=None,
        )
        response = await self.call_llm(messages, max_tokens=2200)
        return self._parse_json_object(response)

    def _normalize_fanfiction_card_type(self, raw_type: Any) -> str:
        """
        Normalize fanfiction card type to backend schema (`Character|World`).

        说明：
        - LLM 可能输出中文（如"角色/设定/世界观"），这里统一映射。
        - 返回空字符串表示无法识别，由上层兜底推断。
        """
        t = str(raw_type or "").strip()
        if not t:
            return ""
        if t in {"Character", "World"}:
            return t
        lowered = t.lower()
        if lowered in {"character", "world"}:
            return lowered.capitalize()
        if any(x in t for x in {"角色", "人物", "主角", "配角"}):
            return "Character"
        if any(x in t for x in {"设定", "世界观", "世界", "地点", "组织", "体系", "规则"}):
            return "World"
        return ""

    def _is_valid_fanfiction_payload(self, payload: Dict[str, Any], source: str = "") -> bool:
        if not isinstance(payload, dict):
            return False
        name = str(payload.get("name") or "").strip()
        card_type = self._normalize_fanfiction_card_type(payload.get("type"))
        description = str(payload.get("description") or "").strip()
        if not name or not description:
            return False
        if card_type not in {"Character", "World"}:
            return False
        if self.language == "en" and self._looks_non_english_description(description):
            return False
        if source and self._is_copied_from_source(description, source):
            return False
        return True

    def _is_copied_from_source(self, description: str, source: str = "") -> bool:
        if not description or not source:
            return False
        text = description.strip()
        if len(text) < 20:
            return False
        if text in source:
            return True
        # 长文更容易出现零散的常见短语重合；优先用"较长片段命中"判断抄袭。
        long_window = 120 if self.language == "en" else 80
        if len(text) >= 160:
            step = 35 if self.language == "en" else 25
            for i in range(0, len(text) - long_window + 1, step):
                frag = text[i : i + long_window]
                if frag and frag in source:
                    return True

        # English tends to share more exact short substrings with the source even after rewriting.
        window = 28 if self.language == "en" else 18
        step = 14 if self.language == "en" else 8
        threshold = 5 if self.language == "en" else 3
        hits = 0
        for i in range(0, len(text) - window + 1, step):
            frag = text[i : i + window]
            if frag and frag in source:
                hits += 1
                if hits >= threshold:
                    return True
        return False

    def _parse_json_object(self, text: str) -> Dict[str, Any]:
        """
        Parse JSON object from LLM response using robust parser.
        使用稳健的解析器从 LLM 响应中提取 JSON 对象。
        """
        if not text:
            return {}
        data, err = parse_json_payload(text, expected_type=dict)
        if err:
            logger.debug("JSON parse failed: %s, response preview: %s", err, text[:200])
            return {}
        return data or {}

    def _truncate_description(self, text: str, limit: int = 800) -> str:
        if not text:
            return ""
        clean = normalize_newlines(str(text or ""))
        clean = re.sub(r"[ \t]+", " ", clean)
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        if len(clean) <= limit:
            return clean
        truncated = clean[:limit].rstrip()
        if "\n\n" in truncated:
            split_at = truncated.rfind("\n\n")
            if split_at >= int(limit * 0.6):
                truncated = truncated[:split_at].rstrip()
        else:
            sentence_cut = max(
                truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "), truncated.rfind("。")
            )
            if sentence_cut >= int(limit * 0.6):
                truncated = truncated[: sentence_cut + 1].rstrip()
        return truncated

    def _fallback_fanfiction_description(self, content: str) -> str:
        summary = ""
        summary_match = re.search(r"Summary:\s*(.+?)(?:\n\n|$)", content, re.IGNORECASE | re.DOTALL)
        if summary_match:
            summary = summary_match.group(1).strip()
        if not summary:
            summary = self._extract_bracket_section(content, "简介")
        summary = self._sanitize_fanfiction_description(summary)
        if summary:
            return self._truncate_description(summary, limit=800)
        clean = re.sub(r"\s+", " ", content).strip()
        clean = self._sanitize_fanfiction_description(clean)
        return self._truncate_description(clean, limit=800)

    def _sanitize_fanfiction_description(self, text: str) -> str:
        if not text:
            return ""
        cleaned = normalize_newlines(str(text or ""))
        cleaned = re.sub(r"【[^】]{1,12}】\s*", "", cleaned)
        cleaned = re.sub(r"\bTitle:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bSummary:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bTable\s*\d*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bRawText:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\[\s*\d{1,3}\s*\]", " ", cleaned)
        cleaned = re.sub(r"\[[A-Za-z]{1,3}\]", " ", cleaned)
        cleaned = re.sub(r"\[(?:note|ref)\s*[\dA-Za-z-]{0,8}\]", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\[\s*(?:citation needed|citation)\s*\]", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\[\s*nb\s*\d*\s*\]", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*\|\s*", " ", cleaned)
        if self.language == "en":
            cleaned = self._normalize_english_spacing(cleaned)
            cleaned = self._normalize_english_labels(cleaned)

        # 若模型把所有字段堆在同一段，用字段名做"仅排版"的自动分段（不改变内容语义）
        if "\n" not in cleaned:
            labels = ["身份定位：", "别名称呼：", "外貌特征：", "性格动机：", "能力限制：", "关键关系：", "写作注意："]
            first = True
            for lab in labels:
                idx = cleaned.find(lab)
                if idx <= 0:
                    continue
                if first:
                    first = False
                    continue
                cleaned = cleaned.replace(lab, "\n\n" + lab)
            if self.language == "en":
                en_labels = [
                    "Identity:",
                    "Alias:",
                    "Appearance:",
                    "Personality:",
                    "Ability:",
                    "Relations:",
                    "Writing Notes:",
                ]
                first_en = True
                for lab in en_labels:
                    idx = cleaned.find(lab)
                    if idx <= 0:
                        continue
                    if first_en:
                        first_en = False
                        continue
                    cleaned = cleaned.replace(lab, "\n\n" + lab)

        lines = []
        for raw_line in cleaned.split("\n"):
            line = re.sub(r"[ \t]{2,}", " ", str(raw_line or "")).strip()
            lines.append(line)

        # 折叠多余空行：最多保留 1 个空行用于分段
        out_lines = []
        blank = 0
        for line in lines:
            if not line:
                blank += 1
                if blank <= 1:
                    out_lines.append("")
                continue
            blank = 0
            out_lines.append(line)

        cleaned = "\n".join(out_lines).strip()

        # 去重相邻句子（按段落处理），减少"绕圈子"
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", cleaned) if p.strip()]
        new_paras = []
        for p in paragraphs:
            if self.language == "en":
                sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", p)
            else:
                sentences = re.split(r"(?<=[。！？!?.])", p)
            deduped = []
            seen = set()
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                key = s[:20]
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(s)
            joiner = " " if self.language == "en" else ""
            new_paras.append(joiner.join(deduped).strip())

        return "\n\n".join([p for p in new_paras if p]).strip()

    def _normalize_english_spacing(self, text: str) -> str:
        body = normalize_newlines(str(text or ""))
        if not body:
            return ""
        body = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", body)
        body = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", body)
        body = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", body)
        body = re.sub(r"(?<=[\]\)])(?=[A-Za-z])", " ", body)
        body = re.sub(r"(?<=[,;:!?])(?=[A-Za-z])", " ", body)
        body = re.sub(r"(?<=[A-Za-z0-9])\((?=[A-Za-z0-9])", " (", body)
        body = re.sub(r"\)(?=[A-Za-z0-9])", ") ", body)
        body = re.sub(r"\.\s*(?=[A-Z][a-z])", ". ", body)
        body = re.sub(r"\s+([,.;:!?])", r"\1", body)
        body = re.sub(r"\(\s+", "(", body)
        body = re.sub(r"\s+\)", ")", body)
        body = re.sub(r"[ \t]{2,}", " ", body).strip()
        return body

    def _normalize_english_labels(self, text: str) -> str:
        """
        Normalize English label lines to canonical forms used by validation:
        Identity/Alias/Appearance/Personality/Ability/Relations/Writing Notes.
        """
        body = normalize_newlines(str(text or ""))
        if not body:
            return ""

        def normalize_line(line: str) -> str:
            raw = str(line or "").strip()
            if not raw:
                return raw
            m = re.match(r"^\s*([A-Za-z][A-Za-z ]{0,30})\s*[-–—:]\s*(.*)\s*$", raw)
            if not m:
                return raw

            label_raw = re.sub(r"\s+", " ", m.group(1).strip())
            rest = m.group(2).strip()
            key = label_raw.lower().replace(".", "").replace("_", " ")
            key = re.sub(r"\s+", " ", key).strip()

            mapping = [
                ({"identity", "role", "overview", "background", "biography", "history", "profile"}, "Identity"),
                ({"alias", "aliases", "aka", "a k a", "also known as", "nickname", "codename", "names"}, "Alias"),
                ({"appearance", "physical appearance", "description", "depiction", "design", "look"}, "Appearance"),
                (
                    {
                        "personality",
                        "traits",
                        "characteristics",
                        "behavior",
                        "behaviour",
                        "characterization",
                        "characterisation",
                    },
                    "Personality",
                ),
                ({"ability", "abilities", "powers", "power", "skills", "equipment", "weapons"}, "Ability"),
                (
                    {"relations", "relationships", "relationship", "family", "allies", "enemies", "associates"},
                    "Relations",
                ),
                (
                    {"writing notes", "writing note", "notes", "writing cautions", "cautions", "writing caution"},
                    "Writing Notes",
                ),
            ]

            canonical = ""
            for keys, value in mapping:
                if key in keys:
                    canonical = value
                    break
            if not canonical:
                for keys, value in mapping:
                    if any(k in key for k in keys if len(k) >= 6):
                        canonical = value
                        break
            if not canonical:
                return raw
            return f"{canonical}: {rest}".rstrip()

        out_lines: List[str] = []
        for line in body.split("\n"):
            out_lines.append(normalize_line(line))
        return "\n".join(out_lines)

    def _english_label_stats(self, text: str) -> Dict[str, Any]:
        body = normalize_newlines(str(text or "")).strip()
        if not body:
            return {"unique": set(), "labeled_paragraphs": 0}
        labels = ("Identity", "Alias", "Appearance", "Personality", "Ability", "Relations", "Writing Notes")
        pattern = r"^(%s):\s*" % "|".join([re.escape(label) for label in labels])
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        labeled_paragraphs = sum(1 for p in paragraphs if re.match(pattern, p, flags=re.IGNORECASE))
        found = set(m.group(1).strip().lower() for m in re.finditer(pattern, body, flags=re.IGNORECASE | re.MULTILINE))
        return {"unique": found, "labeled_paragraphs": labeled_paragraphs}

    def _is_low_quality_fanfiction_description(self, text: str) -> bool:
        body = str(text or "").strip()
        if not body:
            return True
        paragraph_count = len([p for p in re.split(r"\n{2,}", body) if p.strip()])
        citation_count = len(re.findall(r"\[\d{1,3}\]", body))
        if citation_count >= 1:
            return True
        if paragraph_count <= 1 and len(body) >= 220:
            return True

        if self.language == "en":
            stats = self._english_label_stats(body)
            label_hits = int(stats.get("labeled_paragraphs") or 0)
            merge_hits = len(re.findall(r"[a-z][A-Z]", body))
            long_token_hits = len(re.findall(r"\b[A-Za-z]{22,}\b", body))
            if merge_hits >= 3:
                return True
            if long_token_hits >= 2:
                return True
            if label_hits <= 2 and len(body) >= 80:
                return True
            if label_hits < 2:
                colon_pairs = len(re.findall(r"\b[A-Za-z][A-Za-z ]{1,24}:\s*[^\n]{1,120}", body))
                if colon_pairs >= 4:
                    return True
                if paragraph_count <= 1 and len(body) >= 380:
                    return True
            if (
                label_hits == 2
                and re.search(r"(?m)^Identity:\s*", body)
                and re.search(r"(?m)^Writing Notes:\s*", body)
                and len(body) >= 200
            ):
                return True
            if re.search(
                r"\b(created by|designed by|portrayed by|voice(?:d)? by|first appearance|first game)\b",
                body,
                re.IGNORECASE,
            ):
                if label_hits < 2:
                    return True
            if len(re.findall(r"\)[A-Za-z]", body)) >= 3:
                return True
            return False

        zh_labels = ("身份定位：", "别名称呼：", "外貌特征：", "性格动机：", "能力限制：", "关键关系：", "写作注意：")
        zh_hits = sum(1 for label in zh_labels if label in body)
        if zh_hits < 2 and paragraph_count <= 1 and len(body) >= 150:
            return True
        return False

    def _english_needs_more_fields(self, description: str, card_type: str) -> bool:
        if self.language != "en":
            return False
        body = str(description or "").strip()
        if not body:
            return True
        stats = self._english_label_stats(body)
        label_hits = int(stats.get("labeled_paragraphs") or 0)
        found = stats.get("unique") or set()
        if card_type == "Character":
            # Prefer a writing-ready card shape similar to Chinese mode.
            if label_hits < 5:
                return True
            if "identity" not in found or "writing notes" not in found:
                return True
            core = {"appearance", "personality", "ability", "relations"}
            core_hits = len(core.intersection(found))
            if core_hits < 2:
                return True
            if not ({"appearance", "personality"}.intersection(found) and {"ability", "relations"}.intersection(found)):
                return True
        elif card_type == "World":
            if label_hits < 4:
                return True
            if "identity" not in found or "writing notes" not in found:
                return True
            if not ({"ability", "relations"}.intersection(found)):
                return True
        return False

    def _build_safe_fanfiction_description(self, title: str) -> str:
        entity = str(title or "Unknown").strip() or "Unknown"
        if self.language == "en":
            return "\n\n".join(
                [
                    f"Identity: {entity} is an entity extracted from the source page.",
                    "Appearance: Information not confirmed from reliable evidence.",
                    "Personality: Information not confirmed from reliable evidence.",
                    "Ability: Information not confirmed from reliable evidence.",
                    "Relations: Information not confirmed from reliable evidence.",
                    "Writing Notes: Verify key facts against the source page before narrative use.",
                ]
            )
        return "\n\n".join(
            [
                f"身份定位：{entity} 为来源页面中提取的实体。",
                "外貌特征：暂无足够证据支持。",
                "性格动机：暂无足够证据支持。",
                "能力限制：暂无足够证据支持。",
                "关键关系：暂无足够证据支持。",
                "写作注意：建议在写作前回查原始页面确认关键信息。",
            ]
        )

    def _looks_non_english_description(self, text: str) -> bool:
        if self.language != "en":
            return False
        body = str(text or "").strip()
        if not body:
            return False
        cjk_count = sum(1 for ch in body if 0x4E00 <= ord(ch) <= 0x9FFF)
        if cjk_count == 0:
            return False
        alpha_count = sum(1 for ch in body if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
        # Allow sparse non-English proper nouns but reject clearly non-English prose.
        return cjk_count >= 8 and cjk_count > max(6, alpha_count // 2)

    def _extract_bracket_section(self, content: str, label: str) -> str:
        """
        Extract sections formatted like `【标签】...`.

        用于兼容 crawler_service 对萌娘百科（Moegirlpedia）的结构化输出：
        - `【简介】`
        - `【基础资料】`
        - `【关键段落】`
        """
        if not content or not label:
            return ""
        pattern = re.compile(rf"【{re.escape(label)}】\s*(.+?)(?:\n\n【|\Z)", re.DOTALL)
        match = pattern.search(content)
        return match.group(1).strip() if match else ""

    def _extract_llm_section(self, content: str, label: str) -> str:
        if not content or not label:
            return ""
        # Extract top-level `Label:` sections from crawler-produced `llm_content`.
        #
        # Important: section bodies may themselves contain lines like `Identity:` (e.g. inside Key Paragraphs),
        # so we must stop only at the next known top-level section header, not at arbitrary `Xxx:` lines.
        headings = [
            "Summary",
            "Infobox",
            "Key Paragraphs",
            "Key Points",
            "Tables",
            "Excerpt",
            "RawText",
        ]
        stop_heads = [h for h in headings if h.lower() != str(label).strip().lower()]
        stop_re = "|".join([re.escape(h) for h in stop_heads]) or r"$a"  # never matches
        pattern = re.compile(rf"(?ims)^(?:{re.escape(label)}):\s*(.+?)(?=\n\n(?:{stop_re}):|\Z)")
        match = pattern.search(str(content or ""))
        return match.group(1).strip() if match else ""

    def _build_fanfiction_fallback(self, title: str, content: str) -> str:
        if self.language == "en":
            return self._build_english_fanfiction_fallback(title, content)
        summary = self._extract_bracket_section(content, "简介") or self._extract_llm_section(content, "Summary")
        summary = self._sanitize_fanfiction_description(summary)
        if summary and len(summary) >= 60:
            return self._truncate_description(summary, limit=800)

        infobox = self._extract_bracket_section(content, "基础资料") or self._extract_llm_section(content, "Infobox")
        infobox = self._sanitize_fanfiction_description(infobox)
        info_lines = []
        if infobox:
            for line in infobox.split("\n"):
                line = line.strip("- ").strip()
                if not line:
                    continue
                key_lower = line.split(":")[0].lower() if ":" in line else line.lower()
                if any(
                    k in key_lower
                    for k in ["姓名", "本名", "别名", "身份", "职业", "性别", "所属", "阵营", "种族", "配音"]
                ):
                    info_lines.append(line)
        if info_lines:
            combined = f"{title}，" + "，".join(info_lines)
            return self._truncate_description(self._sanitize_fanfiction_description(combined), limit=800)

        key_para = self._extract_bracket_section(content, "关键段落")
        key_para = self._sanitize_fanfiction_description(key_para)
        if key_para and len(key_para) >= 60:
            combined = f"{title}，{key_para}"
            return self._truncate_description(self._sanitize_fanfiction_description(combined), limit=800)

        return self._fallback_fanfiction_description(content)

    def _build_english_fanfiction_fallback(self, title: str, content: str) -> str:
        entity = str(title or "Unknown").strip() or "Unknown"
        summary = self._extract_llm_section(content, "Summary") or self._extract_bracket_section(content, "简介")
        if not summary:
            summary = self._extract_plain_fanfiction_summary(content)
        summary = self._sanitize_fanfiction_description(summary)
        key_para = (
            self._extract_llm_section(content, "Key Paragraphs")
            or self._extract_llm_section(content, "Key Points")
            or self._extract_bracket_section(content, "关键段落")
        )
        key_para = self._sanitize_fanfiction_description(key_para)

        infobox = self._extract_llm_section(content, "Infobox") or self._extract_bracket_section(content, "基础资料")
        if not infobox:
            infobox = self._extract_plain_infobox_block(content)
        infobox_for_parse = normalize_newlines(str(infobox or ""))
        infobox_for_parse = re.sub(r"\[\d{1,3}\]", "", infobox_for_parse)
        infobox_map = self._parse_english_infobox(infobox_for_parse)
        key_para_map = self._parse_english_key_paragraphs(key_para)

        identity_parts = []
        summary_sentence = self._first_sentence(summary)
        if summary_sentence:
            identity_parts.append(summary_sentence)
        for key in ["identity", "occupation", "affiliation", "species", "status"]:
            value = infobox_map.get(key)
            if value and value not in identity_parts:
                identity_parts.append(value)
        if key_para_map.get("identity"):
            identity_parts.append(key_para_map["identity"])

        alias = infobox_map.get("alias")
        appearance = infobox_map.get("appearance") or key_para_map.get("appearance")
        personality = infobox_map.get("personality") or key_para_map.get("personality")
        ability = infobox_map.get("ability") or key_para_map.get("ability")
        relations = infobox_map.get("relations") or key_para_map.get("relations")
        writing_note_parts = []
        key_hint = self._first_sentence(key_para)
        key_hint = re.sub(r"^[A-Za-z][A-Za-z ]{1,24}:\s*", "", key_hint).strip()
        if key_hint:
            writing_note_parts.append(key_hint)
        if summary and len(summary) >= 260:
            writing_note_parts.append("Prioritize stable traits and constraints over episodic plot recap.")

        paragraphs = []
        if not identity_parts:
            identity_parts.append(f"{entity} is an entity extracted from the source page.")
        if identity_parts:
            paragraphs.append(f"Identity: {self._merge_unique_phrases(identity_parts)}")
        if alias:
            paragraphs.append(f"Alias: {alias}")
        if appearance:
            paragraphs.append(f"Appearance: {appearance}")
        if personality:
            paragraphs.append(f"Personality: {personality}")
        if ability:
            paragraphs.append(f"Ability: {ability}")
        if relations:
            paragraphs.append(f"Relations: {relations}")

        if paragraphs:
            notes = self._merge_unique_phrases(writing_note_parts)
            if notes:
                paragraphs.append(f"Writing Notes: {notes}")
            else:
                paragraphs.append(
                    "Writing Notes: Keep characterization aligned with source evidence, and avoid adding unverifiable canon details."
                )
            # Ensure a minimally usable structure for Character cards even when evidence is sparse.
            core_labels = {"Appearance:", "Personality:", "Ability:", "Relations:"}
            core_hits = sum(1 for p in paragraphs if any(p.startswith(lab) for lab in core_labels))
            if core_hits < 2:
                if not any(p.startswith("Appearance:") for p in paragraphs):
                    paragraphs.append("Appearance: Information not confirmed from evidence.")
                if not any(p.startswith("Personality:") for p in paragraphs):
                    paragraphs.append("Personality: Information not confirmed from evidence.")
                if not any(p.startswith("Ability:") for p in paragraphs):
                    paragraphs.append("Ability: Information not confirmed from evidence.")
                if not any(p.startswith("Relations:") for p in paragraphs):
                    paragraphs.append("Relations: Information not confirmed from evidence.")
            return self._truncate_description("\n\n".join(paragraphs), limit=900)

        generic_summary = self._fallback_fanfiction_description(content)
        generic_summary = self._sanitize_fanfiction_description(generic_summary)
        generic_sentence = self._first_sentence(generic_summary)
        if generic_sentence:
            return self._truncate_description(
                "\n\n".join(
                    [
                        f"Identity: {generic_sentence}",
                        "Appearance: Information not confirmed from evidence.",
                        "Personality: Information not confirmed from evidence.",
                        "Ability: Information not confirmed from evidence.",
                        "Relations: Information not confirmed from evidence.",
                        "Writing Notes: Verify key details against the source page before narrative use.",
                    ]
                ),
                limit=900,
            )

        # Final no-evidence fallback
        return self._build_safe_fanfiction_description(entity)

    def _parse_english_key_paragraphs(self, key_para_text: str) -> Dict[str, str]:
        """
        Parse `Key Paragraphs:` blocks (from wiki_parser / crawler_service) into field hints.
        """
        body = normalize_newlines(str(key_para_text or "")).strip()
        if not body:
            return {}

        def norm(label: str) -> str:
            return re.sub(r"\s+", " ", str(label or "").strip().lower())

        buckets: Dict[str, str] = {}
        blocks = [b.strip() for b in re.split(r"\n{2,}", body) if b.strip()]
        for block in blocks[:12]:
            if ":" not in block[:60]:
                continue
            head, rest = block.split(":", 1)
            label = norm(head)
            text = self._sanitize_fanfiction_description(rest).strip()
            if not text:
                continue
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 420:
                text = text[:420].rstrip()

            if any(k in label for k in ["appearance", "design", "depiction", "description"]):
                buckets.setdefault("appearance", text)
            elif any(k in label for k in ["personality", "characteristics", "behavior"]):
                buckets.setdefault("personality", text)
            elif any(k in label for k in ["ability", "abilities", "skills", "powers", "equipment", "weapons"]):
                buckets.setdefault("ability", text)
            elif any(
                k in label
                for k in ["relations", "relationship", "relationships", "allies", "enemies", "family", "associates"]
            ):
                buckets.setdefault("relations", text)
            elif any(
                k in label for k in ["identity", "role", "background", "biography", "history", "overview", "profile"]
            ):
                buckets.setdefault("identity", text)
        return buckets

    def _parse_english_infobox(self, infobox_text: str) -> Dict[str, str]:
        if not infobox_text:
            return {}
        banned_keys = {
            "created by",
            "designed by",
            "illustrated by",
            "voiced by",
            "voice actor",
            "portrayed by",
            "played by",
            "actor",
            "first game",
            "first appearance",
            "composer",
            "publisher",
            "developer",
            "配音",
            "声优",
            "原案",
            "设计",
            "初登场",
            "首次登场",
        }
        key_aliases = {
            "identity": {
                "identity",
                "name",
                "full name",
                "real name",
                "title",
                "origin",
                "nationality",
                "姓名",
                "本名",
                "名称",
                "身份",
                "头衔",
            },
            "occupation": {"occupation", "role", "profession", "position", "职业", "职务", "定位"},
            "affiliation": {"affiliation", "organization", "faction", "team", "group", "所属", "阵营", "组织"},
            "species": {"species", "race", "种族"},
            "status": {"status", "alignment", "状态", "立场"},
            "alias": {"alias", "aliases", "nickname", "codename", "also known as", "aka", "别名", "称号", "称呼"},
            "appearance": {
                "appearance",
                "hair color",
                "eye color",
                "height",
                "build",
                "outfit",
                "gender",
                "age",
                "外貌",
                "发色",
                "瞳色",
                "身高",
                "体重",
                "性别",
                "年龄",
            },
            "personality": {"personality", "traits", "temperament", "性格", "特征", "特点"},
            "ability": {
                "ability",
                "abilities",
                "skills",
                "powers",
                "power",
                "weapon",
                "equipment",
                "magic",
                "能力",
                "技能",
                "武器",
                "装备",
                "魔法",
            },
            "relations": {
                "family",
                "partner",
                "allies",
                "enemies",
                "relationship",
                "relatives",
                "friends",
                "rivals",
                "mentor",
                "student",
                "关系",
                "家属",
                "同伴",
                "敌对",
                "师徒",
            },
        }

        result: Dict[str, str] = {}
        for raw_line in infobox_text.split("\n"):
            line = str(raw_line or "").strip("- ").strip()
            if not line:
                continue
            key = ""
            value = ""
            table_parts = [part.strip() for part in line.strip("|").split("|") if part.strip()]
            if len(table_parts) >= 2:
                key, value = table_parts[0], table_parts[1]
            else:
                pair_match = re.match(r"^\s*([^:=\uFF1A]{1,80})\s*[:=\uFF1A]\s*(.+)\s*$", line)
                if pair_match:
                    key = pair_match.group(1)
                    value = pair_match.group(2)
            if not key or not value:
                continue
            key_norm = re.sub(r"\s+", " ", key.strip().lower())
            value_norm = self._sanitize_fanfiction_description(value).strip(" -;,.")
            if not value_norm:
                continue
            if set(key_norm) == {"-"}:
                continue
            if key_norm in banned_keys:
                continue
            if len(value_norm) > 220:
                value_norm = value_norm[:220].rstrip()
            for bucket, names in key_aliases.items():
                if key_norm in names:
                    if bucket not in result:
                        result[bucket] = value_norm
                    else:
                        result[bucket] = self._merge_unique_phrases([result[bucket], value_norm])
                    break
        return result

    def _extract_plain_fanfiction_summary(self, content: str) -> str:
        body = normalize_newlines(str(content or ""))
        if not body:
            return ""
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        for para in paragraphs[:8]:
            if para.count("|") >= 4:
                continue
            if len(re.findall(r"[^:=\n]{1,40}[:=\uFF1A][^\n]{1,180}", para)) >= 2:
                continue
            text = self._sanitize_fanfiction_description(para)
            if not text:
                continue
            if len(text) < 45:
                continue
            if len(re.findall(r"[^:=\n]{1,40}[:=\uFF1A][^\n]{1,180}", text)) >= 2:
                continue
            if text.lower().startswith(("infobox", "table", "rawtext")):
                continue
            return text
        return ""

    def _extract_plain_infobox_block(self, content: str) -> str:
        body = normalize_newlines(str(content or ""))
        if not body:
            return ""
        lines = []
        for raw_line in body.split("\n")[:260]:
            line = re.sub(r"^[\-\*\u2022\s]+", "", str(raw_line or "")).strip()
            if not line:
                continue
            lower = line.lower()
            if lower in {"summary", "infobox", "key paragraphs", "key points"}:
                continue
            key = ""
            value = ""
            table_parts = [part.strip() for part in line.strip("|").split("|") if part.strip()]
            if len(table_parts) >= 2:
                key, value = table_parts[0], table_parts[1]
            else:
                pair_match = re.match(r"^\s*([^:=\uFF1A]{1,40})\s*[:=\uFF1A]\s*(.{1,220})$", line)
                if pair_match:
                    key = pair_match.group(1).strip()
                    value = pair_match.group(2).strip()
            if not key or not value:
                continue
            if len(key.split()) > 8:
                continue
            if key.lower() in {"title", "summary", "table", "rawtext"}:
                continue
            lines.append(f"{key}: {value}")
            if len(lines) >= 40:
                break
        if len(lines) >= 2:
            return "\n".join(lines)
        return ""

    def _looks_cjk_heavy_source(self, text: str) -> bool:
        body = str(text or "").strip()
        if not body:
            return False
        cjk_count = sum(1 for ch in body if 0x4E00 <= ord(ch) <= 0x9FFF)
        if cjk_count < 40:
            return False
        # When the page is mostly Chinese, extraction should happen in zh first.
        return cjk_count >= max(60, int(len(body) * 0.02))

    def _is_valid_fanfiction_payload_basic(self, payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        name = str(payload.get("name") or "").strip()
        card_type = self._normalize_fanfiction_card_type(payload.get("type"))
        description = str(payload.get("description") or "").strip()
        if not name or not description:
            return False
        if card_type not in {"Character", "World"}:
            return False
        return True

    async def _extract_fanfiction_card_via_zh_then_translate(self, title: str, content: str) -> Dict[str, str]:
        """
        Phase-1: extract a high-quality zh card from zh wiki content.
        Phase-2: translate + normalize into an English card with the same labeled paragraph style.
        """
        clean_title = str(title or "").strip()
        clean_content = str(content or "").strip()
        if not clean_content:
            return {}

        prompt = archivist_fanfiction_card_prompt(title=clean_title, content=clean_content, language="zh")
        messages = self.build_messages(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            context_items=None,
        )

        # Keep this path bounded: zh extraction is usually stable.
        parsed = self._parse_json_object(await self.call_llm(messages, max_tokens=2600))
        if not self._is_valid_fanfiction_payload_basic(parsed):
            parsed = await self._extract_fanfiction_json_from_content(
                clean_title,
                clean_content,
                hint=FANFICTION_CARD_REPAIR_HINT_STRICT_JSON,
                prompt_language="zh",
            )
        if not self._is_valid_fanfiction_payload_basic(parsed):
            return {}

        name = str(parsed.get("name") or clean_title or "Unknown").strip()
        card_type = self._normalize_fanfiction_card_type(parsed.get("type")) or self._infer_card_type_from_title(name)
        zh_desc = self._sanitize_fanfiction_description(str(parsed.get("description") or "").strip())
        if not zh_desc:
            return {}

        translated = await self._translate_fanfiction_description_to_english(
            title=name,
            description=zh_desc,
            source=clean_content,
        )
        translated = self._sanitize_fanfiction_description(translated)
        if not translated:
            return {}
        if self._looks_non_english_description(translated):
            return {}

        return {
            "name": name,
            "type": card_type,
            "description": translated,
        }

    async def _translate_fanfiction_description_to_english(self, title: str, description: str, source: str) -> str:
        if self.language != "en":
            return ""
        draft = self._sanitize_fanfiction_description(description)
        if not draft:
            return ""
        bridge_content = (
            "Structured draft to translate and normalize:\n"
            f"{draft}\n\n"
            "Source content (for disambiguation only):\n"
            f"{str(source or '')[:12000]}"
        )
        hint = (
            "Use the structured draft as primary evidence. Convert it into fluent English while preserving facts. "
            "Keep multi-paragraph structure with one blank line between paragraphs. "
            "Use only these labels: Identity/Alias/Appearance/Personality/Ability/Relations/Writing Notes. "
            "For Character cards: include Identity + Writing Notes, plus at least two of Appearance/Personality/Ability/Relations "
            "(use 'Information not confirmed from evidence.' only when the source truly lacks support). "
            "Do not invent new facts."
        )
        parsed = await self._extract_fanfiction_json_from_content(
            title=title,
            content=bridge_content,
            hint=hint,
        )
        if not isinstance(parsed, dict):
            return ""
        description_en = self._sanitize_fanfiction_description(str(parsed.get("description") or "").strip())
        if not description_en:
            return ""
        if self._looks_non_english_description(description_en):
            return ""
        return description_en

    async def _bridge_payload_to_english(
        self, payload: Dict[str, Any], fallback_title: str, source: str
    ) -> Dict[str, Any]:
        if self.language != "en":
            return {}
        if not isinstance(payload, dict):
            return {}
        raw_desc = self._sanitize_fanfiction_description(str(payload.get("description") or "").strip())
        if not raw_desc:
            return {}
        if not self._looks_non_english_description(raw_desc):
            return {}
        name = str(payload.get("name") or fallback_title or "Unknown").strip() or "Unknown"
        card_type = self._normalize_fanfiction_card_type(payload.get("type")) or self._infer_card_type_from_title(name)
        if card_type not in {"Character", "World"}:
            card_type = self._infer_card_type_from_title(name)
        translated = await self._translate_fanfiction_description_to_english(
            title=name,
            description=raw_desc,
            source=source,
        )
        translated = self._sanitize_fanfiction_description(translated)
        if not translated:
            return {}
        return {
            "name": name,
            "type": card_type,
            "description": translated,
        }

    def _merge_unique_phrases(self, parts: Any) -> str:
        values = []
        seen = set()
        for item in parts or []:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            values.append(text)
        return "; ".join(values)

    def _first_sentence(self, text: str) -> str:
        body = str(text or "").strip()
        if not body:
            return ""
        pieces = re.split(r"(?<=[.!?])\s+", body)
        first = pieces[0].strip() if pieces else body
        first = re.sub(r"\s+", " ", first).strip()
        if len(first) > 220:
            first = first[:220].rstrip()
        return first

    def _infer_card_type_from_title(self, title: str) -> str:
        text = title or ""
        world_suffixes = (
            "城",
            "国",
            "派",
            "门",
            "宗",
            "山",
            "谷",
            "镇",
            "村",
            "府",
            "馆",
            "寺",
            "宫",
            "湖",
            "河",
            "岛",
            "大陆",
            "组织",
            "学院",
        )
        if any(text.endswith(suffix) for suffix in world_suffixes):
            return "World"
        return "Character"
