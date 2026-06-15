# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Wiki 页面结构解析器 - 无需 LLM 的算法化 Wiki 页面解析，提取信息框、段落、表格等结构化数据。
  Wiki page structured parser - Algorithmic parser for extracting structured data (infoboxes, sections, tables) from Wiki HTML without LLM dependency.
"""

import re
from bs4 import BeautifulSoup
from typing import Dict, List


class WikiStructuredParser:
    """
    Wiki 页面结构化解析器 - 从 Wiki HTML 提取结构化信息。

    Algorithmic parser for Wiki pages to extract structured data without LLM.
    Focuses on infoboxes, key sections, tables, and introductions.
    Maps common field names and section keywords across different Wiki platforms.

    Attributes:
        SECTION_KEYWORDS: 部分关键词映射 (appearance, personality, etc.) / Section type keyword mappings
        FIELD_MAPPING: 标准字段映射 (name, gender, age, etc.) / Standard infobox field name mappings
    """

    SECTION_KEYWORDS = {
        "appearance": [
            "外貌",
            "外观",
            "Appearance",
            "physical appearance",
            "visual appearance",
            "形象",
            "造型",
            "衣着",
            "Description",
            "Physical description",
            "Depiction",
            "Design",
            "Character design",
        ],
        "personality": [
            "性格",
            "人格",
            "特点",
            "性情",
            "Personality",
            "personality and traits",
            "traits",
            "Characterization",
            "characterisation",
            "Characteristics",
            "Behavior",
            "Behaviour",
        ],
        "background": [
            "背景",
            "经历",
            "故事",
            "来历",
            "生平",
            "简介",
            "Background",
            "Biography",
            "biography and personality",
            "History",
            "Overview",
            "Role",
            "Role in",
            "Story",
        ],
        "abilities": [
            "能力",
            "技能",
            "战斗",
            "招式",
            "Abilities",
            "Skills",
            "Powers",
            "Powers and abilities",
            "Equipment",
            "Weapons",
        ],
        "relationships": [
            "关系",
            "人际",
            "羁绊",
            "相关人物",
            "Relationships",
            "relationships and affiliations",
            "Allies",
            "Enemies",
            "Family",
            "Associates",
        ],
    }

    FIELD_MAPPING = {
        "name": ["姓名", "Name", "名字", "本名"],
        "gender": ["性别", "Gender", "Sex"],
        "age": ["年龄", "Age"],
        "birthday": ["生日", "Birthday"],
        "height": ["身高", "Height"],
        "weight": ["体重", "Weight"],
        "voice": ["配音", "CV", "声优", "Voice"],
        "affiliation": ["所属", "阵营", "组织", "Affiliation"],
        "identity": ["身份", "职业", "Title", "Identity"],
    }

    def _clean_text(self, value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _map_field_key(self, key_text: str) -> str:
        key = self._clean_text(key_text)
        if not key:
            return ""
        key_lower = key.lower()
        for std_key, keywords in self.FIELD_MAPPING.items():
            for keyword in keywords:
                if str(keyword or "").lower() in key_lower:
                    return std_key
        if 1 < len(key) < 30:
            return key
        return ""

    def parse_page(self, html: str, title: str = "") -> Dict:
        """Parse full page into structured data"""
        soup = BeautifulSoup(html, "lxml")

        infobox_data = self.extract_infobox(soup)
        sections = self.extract_sections_by_header(soup)
        summary = self.extract_summary(soup)
        tables = self.extract_tables(soup)

        return {
            "title": title,
            "infobox": infobox_data,
            "sections": sections,
            "summary": summary,
            "tables": tables,
        }

    def extract_infobox(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract data from standard Wiki infobox"""
        data: Dict[str, str] = {}

        def append_field(raw_key: str, raw_value: str) -> None:
            key = self._map_field_key(raw_key)
            value = self._clean_text(raw_value)
            if not key or not value:
                return
            if key in data:
                merged = f"{data[key]}; {value}"
                if value.lower() in data[key].lower():
                    return
                data[key] = merged[:360]
            else:
                data[key] = value[:280]

        infobox = soup.find(
            "table",
            class_=lambda c: c and ("infobox" in c or "wikitable" in c or "basic-info" in c),
        )

        if infobox:
            for row in infobox.find_all("tr"):
                header = row.find("th")
                value = row.find("td")

                if not header and not value:
                    cols = row.find_all("td")
                    if len(cols) == 2:
                        header = cols[0]
                        value = cols[1]

                if header and value:
                    key_text = header.get_text(" ", strip=True)
                    val_text = value.get_text(" ", strip=True)
                    append_field(key_text, val_text)

        # Fandom / portable-infobox support
        portable_boxes = soup.find_all(
            lambda tag: tag.name in {"aside", "div", "section"}
            and tag.get("class")
            and any("portable-infobox" in c for c in tag.get("class"))
        )
        for box in portable_boxes[:2]:
            items = box.find_all(
                lambda tag: tag.name in {"div", "section"}
                and (tag.get("data-source") or (tag.get("class") and any("pi-item" in c for c in tag.get("class"))))
            )
            for item in items[:80]:
                label_node = item.find(class_=re.compile(r"(pi-data-label|label|name)", re.IGNORECASE))
                value_node = item.find(class_=re.compile(r"(pi-data-value|value)", re.IGNORECASE))
                key_text = ""
                val_text = ""
                if label_node:
                    key_text = label_node.get_text(" ", strip=True)
                elif item.get("data-source"):
                    key_text = str(item.get("data-source") or "").strip().replace("_", " ")

                if value_node:
                    val_text = value_node.get_text(" ", strip=True)
                else:
                    item_text = item.get_text(" ", strip=True)
                    if key_text and item_text.lower().startswith(key_text.lower()):
                        val_text = item_text[len(key_text) :].strip(" :")

                append_field(key_text, val_text)

        return data

    def extract_tables(self, soup: BeautifulSoup) -> List[List[str]]:
        """Extract compact table rows for LLM context"""
        tables: List[List[str]] = []
        content = soup.find("div", class_="mw-parser-output") or soup.find("body") or soup

        identity_keys = [
            "姓名",
            "本名",
            "别名",
            "身份",
            "职业",
            "性别",
            "生日",
            "身高",
            "体重",
            "所属",
            "阵营",
            "配音",
            "种族",
        ]
        skill_noise = ["攻击", "伤害", "技能", "冷却", "等级", "效果", "倍率", "命中", "普攻", "重击"]

        for table in content.find_all("table")[:3]:
            rows: List[str] = []
            raw_text = table.get_text(" ", strip=True)
            if not raw_text:
                continue

            has_identity = any(key in raw_text for key in identity_keys)
            has_skill_noise = any(key in raw_text for key in skill_noise)
            if has_skill_noise and not has_identity:
                continue

            for row in table.find_all("tr")[:12]:
                cells = row.find_all(["th", "td"])
                if not cells:
                    continue
                texts = [cell.get_text(" ", strip=True) for cell in cells]
                texts = [text for text in texts if text]
                if not texts:
                    continue
                if len(texts) == 2:
                    rows.append(f"{texts[0]}: {texts[1]}")
                else:
                    rows.append(" | ".join(texts))
            if rows:
                tables.append(rows)

        return tables

    def extract_sections_by_header(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract paragraphs under specific headers"""
        sections: Dict[str, str] = {}

        headers = soup.find_all(["h2", "h3"])

        for header in headers:
            headline = header.find("span", class_="mw-headline")
            header_text = headline.get_text(" ", strip=True) if headline else header.get_text(" ", strip=True)
            header_lower = str(header_text or "").strip().lower()

            section_type = None
            for s_type, keywords in self.SECTION_KEYWORDS.items():
                keyword_lowers = [str(kw or "").strip().lower() for kw in (keywords or []) if str(kw or "").strip()]
                if any(kw in header_lower for kw in keyword_lowers):
                    section_type = s_type
                    break

            if section_type and section_type not in sections:
                content_parts = []
                for sibling in header.find_next_siblings():
                    if sibling.name in ["h2", "h3"]:
                        break

                    if sibling.name == "p":
                        text = sibling.get_text(" ", strip=True)
                        if len(text) > 10:
                            content_parts.append(text)
                    elif sibling.name in ["ul", "ol"]:
                        items = [li.get_text(" ", strip=True) for li in sibling.find_all("li")]
                        if items:
                            content_parts.append("\n".join(items[:5]))

                    if len(content_parts) >= 5:
                        break

                if content_parts:
                    sections[section_type] = "\n".join(content_parts)

        return sections

    def extract_summary(self, soup: BeautifulSoup) -> str:
        """Extract the first meaningful paragraph as summary"""
        for p in soup.find_all("p"):
            is_clean = True
            for parent in p.parents:
                if parent.name in ["table", "li", "ul", "footer"]:
                    is_clean = False
                    break
                if parent.get("class") and any("navbox" in c for c in parent.get("class")):
                    is_clean = False
                    break

            if is_clean:
                text = p.get_text(" ", strip=True)
                if len(text) > 30:
                    return text

        return ""

    def format_for_llm(self, parsed: Dict, max_chars: int = 50000) -> str:
        """Format parsed data into a compact LLM-ready text."""
        parts: List[str] = []
        title = str(parsed.get("title", "") or "").strip()
        summary = str(parsed.get("summary", "") or "").strip()
        infobox = parsed.get("infobox") or {}
        sections = parsed.get("sections") or {}

        if title:
            parts.append(f"{title}")
        if summary:
            parts.append("Summary:\n" + summary)

        if infobox:
            lines = [f"{k}: {v}" for k, v in infobox.items() if k and v]
            if lines:
                parts.append("Infobox:\n" + "\n".join(lines))

        if sections:
            section_blocks: List[str] = []
            for sec_name, content in sections.items():
                if not content:
                    continue
                name = self._clean_text(sec_name).capitalize() if sec_name else "Section"
                text = self._clean_text(content)
                if not text:
                    continue
                section_blocks.append(f"{name}: {text}")
            if section_blocks:
                parts.append("Key Paragraphs:\n" + "\n\n".join(section_blocks[:5]))

        text = "\n\n".join([p for p in parts if p]).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n\n[Content truncated]"
        return text

    def format_for_preview(self, parsed: Dict, max_chars: int = 1200) -> str:
        """Create short preview text for UI."""
        title = str(parsed.get("title", "") or "").strip()
        summary = str(parsed.get("summary", "") or "").strip()
        sections = parsed.get("sections") or {}

        parts: List[str] = []
        if title:
            parts.append(title)
        if summary:
            parts.append(summary)
        if not summary and sections:
            first_section = next(iter(sections.values()), "")
            if first_section:
                parts.append(first_section)

        text = "\n\n".join([p for p in parts if p]).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        return text


wiki_parser = WikiStructuredParser()
