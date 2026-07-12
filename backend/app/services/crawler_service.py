# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  爬虫服务 - 支持多个 Wiki 平台（萌娘百科、Fandom、Wikipedia）的页面内容爬取和解析。
  Crawler service - Scrapes Wiki pages from multiple platforms using MediaWiki APIs and HTML fallbacks with intelligent link extraction.
"""

import asyncio
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse, urldefrag, parse_qs, quote, unquote

import aiohttp
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.utils.logger import get_logger
from app.error_contract import safe_error_code
from .crawler_moegirl_helpers import (
    build_moegirl_page_url,
    build_moegirl_raw_url,
    extract_moegirl_links_from_raw,
    extract_moegirl_list_links_from_html,
    extract_moegirl_outgoing_links_from_html,
    is_mediawiki_namespace_title,
    is_moegirl_domain,
    merge_link_lists,
    moegirl_article_title_from_url,
    normalize_raw_wikitext,
    normalize_url,
)
from .wiki_parser import wiki_parser

logger = get_logger(__name__)


class CrawlerService:
    """
    Wiki 页面爬虫服务。

    Service for scraping Wiki pages with support for multiple platforms (Moegirlpedia, Fandom, Wikipedia).
    Uses MediaWiki API for reliable extraction when available, with HTML scraping fallbacks.
    Implements intelligent link extraction to avoid noise and over-inclusion of navigation links.

    Attributes:
        MAX_PREVIEW_CHARS: 预览文本最大字符数 (Max characters for preview)
        MAX_LLM_CHARS: LLM 处理的最大字符数 (Max characters for LLM input)
        MAX_LINKS: 通用最大链接数 (Max links for generic sites)
        MOEGIRL_MAX_LINKS: 萌娘百科特定最大链接数 (Moegirl-specific max links)
    """

    MAX_PREVIEW_CHARS = 1200
    MAX_LLM_CHARS = 50000
    MAX_LINKS = 400
    MOEGIRL_MAX_LINKS = 900
    MOEGIRL_API_PRIMARY = "https://mzh.moegirl.org.cn/api.php"
    MOEGIRL_API_FALLBACK = "https://zh.moegirl.org.cn/api.php"
    MOEGIRL_WEB_PRIMARY = "https://mzh.moegirl.org.cn"
    MOEGIRL_WEB_FALLBACK = "https://zh.moegirl.org.cn"

    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        retry = Retry(
            total=4,
            backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def scrape_page(self, url: str) -> Dict[str, Any]:
        """
        Scrape a Wiki page and extract main content.
        Uses API when available, falls back to HTML scraping.
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            if "fandom.com" in domain:
                result = self._scrape_mediawiki_parse(url, parsed)
            elif "moegirl.org" in domain:
                result = self._scrape_moegirl(url, parsed)
            elif "wikipedia.org" in domain:
                result = self._scrape_wikipedia(url, parsed)
            elif "huijiwiki.com" in domain or "wiki" in domain:
                result = self._scrape_mediawiki_parse(url, parsed)
            else:
                result = self._scrape_html(url)

            if result.get("success", False):
                if not result.get("content") and not result.get("links"):
                    result["content"] = (
                        f"Page Title: {result.get('title', 'Unknown')}\n\n"
                        "This page has no extractable text. Please open it in a browser."
                    )
                    result["links"] = []

            return result

        except Exception as exc:
            return {
                "success": False,
                "error": safe_error_code(exc),
                "url": url,
                "content": "",
                "links": [],
                "llm_content": "",
            }

    def _normalize_url(self, url: str) -> str:
        return normalize_url(url)

    def _is_moegirl_domain(self, netloc: str) -> bool:
        return is_moegirl_domain(netloc)

    def _moegirl_article_title_from_url(self, parsed) -> Optional[str]:
        return moegirl_article_title_from_url(parsed)

    def _build_moegirl_page_url(self, title: str) -> str:
        return build_moegirl_page_url(title)

    def _build_moegirl_raw_url(self, title: str, base: str) -> str:
        return build_moegirl_raw_url(title, base)

    def _is_mediawiki_namespace_title(self, title: str) -> bool:
        return is_mediawiki_namespace_title(title)

    def _extract_moegirl_list_links_from_html(
        self,
        content_root: BeautifulSoup,
        base_url: str,
    ) -> List[Dict[str, str]]:
        return extract_moegirl_list_links_from_html(
            content_root=content_root,
            base_url=base_url,
            max_links=self.MOEGIRL_MAX_LINKS,
        )

    def _extract_moegirl_outgoing_links_from_html(
        self,
        content_root: BeautifulSoup,
        base_url: str,
        cap: int,
    ) -> List[Dict[str, str]]:
        return extract_moegirl_outgoing_links_from_html(
            content_root=content_root,
            base_url=base_url,
            cap=cap,
        )

    def _extract_moegirl_links_from_raw(self, raw_text: str, cap: int) -> List[Dict[str, str]]:
        return extract_moegirl_links_from_raw(raw_text, cap)

    def _normalize_raw_wikitext(self, text: str) -> str:
        return normalize_raw_wikitext(text)

    def _scrape_moegirl_raw(self, title: str) -> Optional[Dict[str, Any]]:
        for base in [self.MOEGIRL_WEB_PRIMARY, self.MOEGIRL_WEB_FALLBACK]:
            raw_url = self._build_moegirl_raw_url(title, base)
            try:
                response = self.session.get(raw_url, timeout=(6, 20))
                response.raise_for_status()
                response.encoding = "utf-8"
                raw_text = str(response.text or "").strip()
                if not raw_text:
                    continue
                # Some anti-bot pages return HTML instead of raw wikitext.
                if raw_text.lstrip().startswith("<!DOCTYPE") or "<html" in raw_text[:300].lower():
                    continue
                llm_content = self._normalize_raw_wikitext(raw_text)
                if not llm_content:
                    continue
                links = self._extract_moegirl_links_from_raw(raw_text, cap=self.MOEGIRL_MAX_LINKS)
                return {
                    "success": True,
                    "title": title,
                    "content": self._clean_content(llm_content[: self.MAX_PREVIEW_CHARS]),
                    "llm_content": self._clean_content(llm_content[: self.MAX_LLM_CHARS]),
                    "links": links,
                    "is_list_page": len(links) > 10,
                    "url": self._build_moegirl_page_url(title),
                }
            except Exception as exc:
                logger.info("Moegirl raw fetch failed url=%s err=%s", raw_url, exc)
                continue
        return None

    def _fetch_moegirl_html_outgoing_links(self, page_url: str, cap: int) -> List[Dict[str, str]]:
        """
        Fetch the actual page HTML and extract outgoing links.

        目的：当 `action=parse` 的 HTML/links 结果异常偏少时，用“浏览器实际页面”兜底一次，
        避免出现“百科页面明明很多链接，但系统只识别到很少”的体验。
        """
        url = self._normalize_url(page_url)
        if not url:
            return []
        try:
            response = self.session.get(url, timeout=(6, 20))
            response.raise_for_status()
            response.encoding = "utf-8"
            html = response.text or ""
        except Exception as exc:
            logger.warning("Moegirl HTML fallback fetch failed url=%s err=%s", url, exc)
            return []

        soup = BeautifulSoup(html, "lxml")
        content_root = soup.find("div", class_="mw-parser-output")
        if not content_root:
            # 避免退回到 body 把整站 header/footer/sidebar 的链接一并抓进来，导致“无关词条”。
            # 找不到正文容器时直接放弃兜底链接，宁可少也不要脏。
            return []
        return self._extract_moegirl_outgoing_links_from_html(content_root, url, cap=cap)

    def _is_probably_moegirl_list_page(
        self,
        title: str,
        content_root: BeautifulSoup,
        list_links: List[Dict[str, str]],
    ) -> bool:
        """Heuristic list-page detector for Moegirlpedia."""
        t = str(title or "").strip()
        if any(k in t for k in ["列表", "目录", "条目", "登场人物", "角色列表", "人物列表", "角色表"]):
            return True

        if not content_root:
            return False

        li_count = len(content_root.find_all("li"))
        paragraph_count = 0
        for p in content_root.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text) >= 60:
                paragraph_count += 1
            if paragraph_count >= 8:
                break

        # List pages: many list items, few paragraphs, many list-links
        if len(list_links) >= 30 and paragraph_count <= 6:
            return True
        if li_count >= 50 and paragraph_count <= 4 and len(list_links) >= 12:
            return True
        return False

    def _merge_link_lists(
        self,
        primary: List[Dict[str, str]],
        secondary: List[Dict[str, str]],
        cap: int,
    ) -> List[Dict[str, str]]:
        return merge_link_lists(primary, secondary, cap)

    def _scrape_moegirl_raw(self, title: str) -> Optional[Dict[str, Any]]:
        for base in [self.MOEGIRL_WEB_PRIMARY, self.MOEGIRL_WEB_FALLBACK]:
            raw_url = self._build_moegirl_raw_url(title, base)
            try:
                response = self.session.get(raw_url, timeout=(6, 20))
                response.raise_for_status()
                response.encoding = "utf-8"
                raw_text = str(response.text or "").strip()
                if not raw_text:
                    continue
                # Some anti-bot pages return HTML instead of raw wikitext.
                if raw_text.lstrip().startswith("<!DOCTYPE") or "<html" in raw_text[:300].lower():
                    continue
                llm_content = self._normalize_raw_wikitext(raw_text)
                if not llm_content:
                    continue
                links = self._extract_moegirl_links_from_raw(raw_text, cap=self.MOEGIRL_MAX_LINKS)
                return {
                    "success": True,
                    "title": title,
                    "content": self._clean_content(llm_content[: self.MAX_PREVIEW_CHARS]),
                    "llm_content": self._clean_content(llm_content[: self.MAX_LLM_CHARS]),
                    "links": links,
                    "is_list_page": len(links) > 10,
                    "url": self._build_moegirl_page_url(title),
                }
            except Exception as exc:
                logger.info("Moegirl raw fetch failed url=%s err=%s", raw_url, exc)
                continue
        return None

    def _scrape_moegirl(self, url: str, parsed) -> Dict[str, Any]:
        """
        Moegirlpedia specialized scraper.

        目标：稳定（尽量避免 HTML 直抓失败），且把高价值信息置顶，提升建卡质量。
        """
        normalized_url = self._normalize_url(url)
        parsed = urlparse(normalized_url) if normalized_url else parsed
        title = self._moegirl_article_title_from_url(parsed)
        if not title:
            return self._scrape_html(normalized_url or url)

        # Preferred path for Moegirl now: raw wikitext (more stable than parse API).
        raw_result = self._scrape_moegirl_raw(title)
        if raw_result and raw_result.get("success"):
            logger.info("Moegirl raw extraction succeeded title=%s links=%s", title, len(raw_result.get("links") or []))
            return raw_result

        # HTML fallback path (multi-domain + printable mode) before parse API.
        html_candidates: List[str] = []
        safe_title = quote(str(title).replace(" ", "_"), safe="")
        html_candidates.append(self._normalize_url(normalized_url or url))
        html_candidates.append(f"{self.MOEGIRL_WEB_PRIMARY}/index.php?title={safe_title}")
        html_candidates.append(f"{self.MOEGIRL_WEB_FALLBACK}/index.php?title={safe_title}")
        html_candidates.append(f"{self.MOEGIRL_WEB_PRIMARY}/index.php?title={safe_title}&printable=yes")
        html_candidates.append(f"{self.MOEGIRL_WEB_FALLBACK}/index.php?title={safe_title}&printable=yes")

        seen = set()
        for candidate in html_candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            html_result = self._scrape_html(candidate)
            if html_result.get("success"):
                content = str(html_result.get("content") or "").strip()
                llm_content = str(html_result.get("llm_content") or "").strip()
                links = html_result.get("links") or []
                if content or llm_content or links:
                    logger.info("Moegirl HTML extraction succeeded url=%s links=%s", candidate, len(links))
                    return html_result

        # Last resort: parse API (some mirrors may still allow it).
        for api_url in [self.MOEGIRL_API_PRIMARY, self.MOEGIRL_API_FALLBACK]:
            try:
                params = {
                    "action": "parse",
                    "page": title,
                    "prop": "text|links|categories|sections",
                    "format": "json",
                    "formatversion": "2",
                    "redirects": "1",
                    "disablelimitreport": "1",
                }
                response = self.session.get(api_url, params=params, timeout=(6, 20))
                response.raise_for_status()
                response.encoding = "utf-8"
                data = response.json()
                if "error" in data:
                    raise ValueError(str(data.get("error")))

                parse_data = data.get("parse") or {}
                display_title = str(parse_data.get("title") or title).strip() or title
                html_content = str(parse_data.get("text") or "")
                if not html_content.strip():
                    raise ValueError("empty_parse_text")

                # 重要：链接抽取使用“原始 parse HTML”（不做清理），否则会误删导航模板/目录等导致子词条显著变少。
                soup_raw = BeautifulSoup(html_content, "lxml")
                content_root_raw = soup_raw.find("div", class_="mw-parser-output") or soup_raw

                # 内容/预览用于建卡：仍做清理，减少噪声并把高价值信息置顶。
                soup = BeautifulSoup(html_content, "lxml")
                content_root = soup.find("div", class_="mw-parser-output") or soup
                for unwanted in content_root.find_all(["nav", "aside", "footer", "script", "style", "noscript"]):
                    unwanted.decompose()
                for cls in ["navbox", "toc", "mw-editsection", "reference", "reflist", "catlinks"]:
                    for elem in content_root.find_all(class_=re.compile(cls, re.I)):
                        elem.decompose()

                parsed_data = wiki_parser.parse_page(str(content_root), title=display_title)
                llm_content = self._format_moegirl_llm_content(parsed_data, content_root, parse_data)
                preview_content = wiki_parser.format_for_preview(parsed_data, max_chars=self.MAX_PREVIEW_CHARS)
                if not preview_content:
                    preview_content = llm_content[: self.MAX_PREVIEW_CHARS]

                base_page_url = self._build_moegirl_page_url(display_title)
                html_links = self._extract_moegirl_outgoing_links_from_html(
                    content_root_raw,
                    base_page_url,
                    cap=self.MOEGIRL_MAX_LINKS,
                )
                is_list_page = self._is_probably_moegirl_list_page(display_title, content_root_raw, html_links)

                links = (html_links or [])[: self.MOEGIRL_MAX_LINKS]

                # 若依旧异常偏少（与用户预期不符），用“页面 HTML”兜底抽一次。
                html_fallback_links: List[Dict[str, str]] = []
                if len(links) < 80:
                    html_fallback_links = self._fetch_moegirl_html_outgoing_links(
                        base_page_url,
                        cap=self.MOEGIRL_MAX_LINKS,
                    )
                    if html_fallback_links:
                        links = self._merge_link_lists(links, html_fallback_links, cap=self.MOEGIRL_MAX_LINKS)
                        links = (links or [])[: self.MOEGIRL_MAX_LINKS]

                logger.info(
                    "Moegirl parsed title=%s llm_chars=%s links=%s (html=%s html_fallback=%s) is_list_page=%s",
                    display_title,
                    len(llm_content or ""),
                    len(links or []),
                    len(html_links or []),
                    len(html_fallback_links or []),
                    is_list_page,
                )

                return {
                    "success": True,
                    "title": display_title,
                    "content": self._clean_content(preview_content),
                    "llm_content": self._clean_content(llm_content),
                    "links": links[: self.MOEGIRL_MAX_LINKS],
                    "is_list_page": is_list_page,
                    "url": base_page_url,
                }
            except Exception as exc:
                logger.info("Moegirl API parse failed api=%s url=%s err=%s", api_url, url, exc)
                continue

        return {
            "success": False,
            "error": "Moegirl extraction failed: parse API is blocked and raw/HTML fallback returned empty content.",
            "url": normalized_url or url,
            "content": "",
            "links": [],
            "llm_content": "",
        }

    def _extract_moegirl_links(self, parse_data: Dict[str, Any]) -> List[Dict[str, str]]:
        raw_links = parse_data.get("links") or []
        # 兼容少数站点/版本把 links 包成对象（或根本不是 list）
        if isinstance(raw_links, dict):
            raw_links = raw_links.get("*") or raw_links.get("links") or []
        links: List[Dict[str, str]] = []
        seen = set()
        for item in raw_links[: self.MOEGIRL_MAX_LINKS * 3]:
            name = ""
            ns = None
            if isinstance(item, dict):
                ns = item.get("ns")
                # MediaWiki `action=parse&prop=links` 的字段在不同站点/formatversion 下可能不同：
                # - {"*": "标题"} (常见)
                # - {"title": "标题"} (部分站点/版本)
                # - {"link": "标题"} (兼容)
                name = str(item.get("link") or item.get("*") or item.get("title") or item.get("name") or "").strip()
            elif isinstance(item, str):
                name = item.strip()
            else:
                continue
            if not name or len(name) < 2:
                continue
            # 尽量聚焦“词条页”（主命名空间）。若 ns 缺失，则不拦截。
            if ns is not None and ns not in (0, "0"):
                continue
            if self._is_mediawiki_namespace_title(name):
                continue
            lowered = name.lower()
            if any(
                lowered.startswith(prefix)
                for prefix in ["special:", "file:", "talk:", "template:", "user:", "help:", "category:"]
            ):
                continue
            url = self._build_moegirl_page_url(name)
            if url in seen:
                continue
            seen.add(url)
            links.append({"title": name, "url": url})
            if len(links) >= self.MOEGIRL_MAX_LINKS:
                break
        return links

    def _format_moegirl_llm_content(
        self,
        parsed_data: Dict[str, Any],
        content_root: BeautifulSoup,
        parse_data: Dict[str, Any],
    ) -> str:
        """
        Moegirl LLM content formatting: put high-value evidence first.
        用途：仅用于“建卡”，因此优先：简介 + 基础资料/信息框 + 关键段落。
        """
        title = str(parsed_data.get("title") or "").strip()
        summary = str(parsed_data.get("summary") or "").strip()
        infobox = parsed_data.get("infobox") or {}
        sections = parsed_data.get("sections") or {}
        categories = []
        for item in (parse_data.get("categories") or [])[:30]:
            if isinstance(item, dict):
                name = str(item.get("category") or item.get("*") or "").strip()
                if name:
                    categories.append(name)
            elif isinstance(item, str) and item.strip():
                categories.append(item.strip())

        parts: List[str] = []
        if title:
            parts.append(title)
        if summary:
            parts.append("【简介】\n" + summary)

        if infobox:
            lines = []
            for k, v in list(infobox.items())[:28]:
                key = str(k or "").strip()
                val = str(v or "").strip()
                if key and val:
                    lines.append(f"{key}: {val}")
            if lines:
                parts.append("【基础资料】\n" + "\n".join(lines))

        if sections:
            # Keep only the most relevant sections for building a card
            ordered = []
            priority = {"appearance": 1, "personality": 2, "background": 3, "abilities": 4, "relationships": 5}
            for k, v in sections.items():
                ordered.append((priority.get(k, 99), str(v or "").strip()))
            ordered.sort(key=lambda x: x[0])
            kept = 0
            for _, text in ordered:
                if not text:
                    continue
                parts.append("【关键段落】\n" + text[:1800].strip())
                kept += 1
                if kept >= 3:
                    break

        if categories:
            parts.append("【分类】\n" + "、".join(categories[:20]))

        # Append a compact excerpt from the page to avoid missing important cues (but keep it late).
        extra = []
        for p in content_root.find_all("p")[:25]:
            text = p.get_text(" ", strip=True)
            if len(text) >= 25:
                extra.append(text)
            if len(extra) >= 10:
                break
        if extra:
            parts.append("【正文摘录】\n" + "\n".join(extra))

        text = "\n\n".join([p for p in parts if p]).strip()
        if len(text) > self.MAX_LLM_CHARS:
            text = text[: self.MAX_LLM_CHARS].rstrip() + "\n\n[Content truncated]"
        return text

    def _format_generic_llm_content(self, parsed_data: Dict[str, Any], soup: BeautifulSoup) -> str:
        """
        Generic LLM content formatting for non-Moegirl sites (Fandom/Wikipedia/etc.).
        Keep the input compact and structured to reduce noise.
        """
        title = str(parsed_data.get("title") or "").strip()
        summary = str(parsed_data.get("summary") or "").strip()
        infobox = parsed_data.get("infobox") or {}
        sections = parsed_data.get("sections") or {}
        tables = parsed_data.get("tables") or []

        parts: List[str] = []
        if title:
            parts.append(title)
        if summary:
            parts.append("Summary:\n" + summary[:1600].strip())

        if infobox:

            def infobox_rank(key: str) -> int:
                k = str(key or "").strip().lower()
                if not k:
                    return 99
                # Prefer stable identity signals; avoid credits/meta.
                if any(
                    x in k
                    for x in [
                        "created by",
                        "designed by",
                        "illustrated by",
                        "voiced by",
                        "voice actor",
                        "portrayed by",
                        "played by",
                    ]
                ):
                    return 80
                if any(x in k for x in ["first appearance", "first game", "publisher", "developer", "composer"]):
                    return 70
                if any(
                    x in k
                    for x in ["name", "identity", "real name", "full name", "alias", "aliases", "nickname", "codename"]
                ):
                    return 1
                if any(x in k for x in ["occupation", "role", "title", "profession"]):
                    return 2
                if any(
                    x in k
                    for x in [
                        "affiliation",
                        "organization",
                        "faction",
                        "team",
                        "species",
                        "race",
                        "gender",
                        "age",
                        "height",
                        "weight",
                    ]
                ):
                    return 3
                if any(x in k for x in ["status", "alignment", "origin", "nationality"]):
                    return 4
                return 20

            lines = []
            ordered_items = sorted(list(infobox.items()), key=lambda kv: (infobox_rank(kv[0]), str(kv[0] or "")))
            for k, v in ordered_items[:36]:
                key = str(k or "").strip()
                val = str(v or "").strip()
                if key and val:
                    lines.append(f"{key}: {val}")
            if lines:
                parts.append("Infobox:\n" + "\n".join(lines))

        if sections:
            ordered = []
            priority = {"appearance": 1, "personality": 2, "background": 3, "abilities": 4, "relationships": 5}
            section_to_label = {
                "appearance": "Appearance",
                "personality": "Personality",
                "background": "Identity",
                "abilities": "Ability",
                "relationships": "Relations",
            }
            for k, v in sections.items():
                ordered.append((priority.get(k, 99), str(k or "").strip(), str(v or "").strip()))
            ordered.sort(key=lambda x: x[0])
            blocks = []
            for _, name, text in ordered:
                text = str(text or "").strip()
                if not text:
                    continue
                label = section_to_label.get(name, (name or "Section").strip().capitalize())
                blocks.append(f"{label}: {text[:1600].strip()}")
                if len(blocks) >= 5:
                    break
            if blocks:
                parts.append("Key Paragraphs:\n" + "\n\n".join(blocks))

        if tables:
            table_blocks: List[str] = []
            for idx, rows in enumerate(tables[:2], start=1):
                if not rows:
                    continue
                compact = []
                for row in rows[:10]:
                    row_text = str(row or "").strip()
                    if row_text:
                        compact.append(row_text)
                if compact:
                    table_blocks.append(f"Table {idx}:\n" + "\n".join(compact))
            if table_blocks:
                parts.append("Tables:\n" + "\n\n".join(table_blocks))

        excerpt = []
        for p in soup.find_all("p")[:60]:
            text = p.get_text(" ", strip=True)
            if len(text) >= 40:
                excerpt.append(text)
            if len(excerpt) >= 12:
                break
        if excerpt:
            parts.append("Excerpt:\n" + "\n".join(excerpt))

        raw_text = self._extract_text_from_soup(soup)
        if raw_text:
            # Avoid Chinese intro marker in generic sources; keep it neutral for English prompts.
            raw_text = re.sub(r"(?m)^##\s*简介\s*\n", "## Intro\n", raw_text)
            parts.append("RawText:\n" + raw_text[:12000].strip())

        text = "\n\n".join([p for p in parts if p]).strip()
        if len(text) > self.MAX_LLM_CHARS:
            text = text[: self.MAX_LLM_CHARS].rstrip() + "\n\n[Content truncated]"
        return text

    def _extract_mediawiki_title(self, parsed) -> Optional[str]:
        if "index.php" in parsed.path:
            query_params = parse_qs(parsed.query)
            title = query_params.get("title", [None])[0]
            return unquote(title) if title else None

        path_parts = parsed.path.strip("/").split("/")
        if not path_parts:
            return None
        if path_parts[0] == "wiki" and len(path_parts) > 1:
            return unquote("/".join(path_parts[1:]))
        if path_parts[0] != "index.php":
            return unquote(parsed.path.strip("/"))
        return None

    def _get_mediawiki_api_url(self, parsed) -> str:
        path_parts = parsed.path.strip("/").split("/")
        lang_prefix = ""
        if len(path_parts) > 0 and len(path_parts[0]) == 2 and path_parts[0] != "wiki":
            lang_prefix = f"/{path_parts[0]}"
        return f"{parsed.scheme}://{parsed.netloc}{lang_prefix}/api.php"

    def _scrape_mediawiki_parse(self, url: str, parsed) -> Dict[str, Any]:
        """Generic MediaWiki parse-action scraper (Fandom/Moegirl/Huiji/etc.)"""
        article_name = self._extract_mediawiki_title(parsed)
        if not article_name:
            return self._scrape_html(url)

        api_url = self._get_mediawiki_api_url(parsed)
        params = {
            "action": "parse",
            "page": article_name,
            "prop": "text|categories",
            "format": "json",
            "redirects": "1",
        }

        try:
            response = self.session.get(api_url, params=params, timeout=15)
            response.raise_for_status()
            response.encoding = "utf-8"
            data = response.json()

            if "error" in data:
                return self._scrape_html(url)

            parse_data = data.get("parse", {})
            title = parse_data.get("title", "Untitled")
            html_content = parse_data.get("text", {}).get("*", "")

            if not html_content:
                return self._scrape_html(url)

            return self._build_from_html(html_content, title, url)

        except Exception as exc:
            logger.error("MediaWiki parse error: %s", exc)
            return self._scrape_html(url)

    def _scrape_wikipedia(self, url: str, parsed) -> Dict[str, Any]:
        """Scrape Wikipedia using REST API and mobile HTML."""
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) < 2 or path_parts[0] != "wiki":
            return self._scrape_html(url)

        article_name = "/".join(path_parts[1:])

        try:
            content_url = f"{parsed.scheme}://{parsed.netloc}/api/rest_v1/page/mobile-html/{quote(article_name)}"
            content_resp = self.session.get(content_url, timeout=15)
            content_resp.raise_for_status()
            html = content_resp.text

            title = ""
            try:
                soup_preview = BeautifulSoup(html[:5000], "lxml")
                title_tag = soup_preview.find("title")
                if title_tag:
                    title = title_tag.get_text(strip=True).split(" - ")[0]
            except Exception as exc:
                logger.warning("Failed to extract title: %s", exc)

            return self._build_from_html(html, title or article_name, url)
        except Exception:
            return self._scrape_html(url)

    def _scrape_html(self, url: str) -> Dict[str, Any]:
        """Fallback HTML scraping method"""
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()

            if response.encoding and response.encoding.lower() in ["iso-8859-1", "ascii"]:
                detected = response.apparent_encoding
                response.encoding = detected if detected else "utf-8"
            elif not response.encoding:
                response.encoding = "utf-8"

            parsed_check = urlparse(url)
            if any(x in parsed_check.netloc for x in ["moegirl", "baike", "hudong", "zh."]):
                response.encoding = "utf-8"

            html = response.text
            title = ""
            try:
                soup_preview = BeautifulSoup(html[:5000], "lxml")
                title_tag = soup_preview.find("title")
                if title_tag:
                    title = title_tag.get_text(strip=True).split(" - ")[0]
            except Exception as exc:
                logger.warning("Failed to extract title: %s", exc)

            return self._build_from_html(html, title, url)

        except Exception as exc:
            return {
                "success": False,
                "error": f"page_load_failed:{safe_error_code(exc)}",
                "url": url,
                "content": "",
                "links": [],
                "llm_content": "",
            }

    def _build_from_html(self, html: str, title: str, url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "lxml")
        parsed_data = wiki_parser.parse_page(html, title=title or "Untitled")

        has_structure = any(
            [
                parsed_data.get("summary"),
                parsed_data.get("sections"),
                parsed_data.get("infobox"),
                parsed_data.get("tables"),
            ]
        )

        if not has_structure:
            fallback_text = self._extract_text_from_soup(soup)
            parsed_data = {
                "title": title or "Untitled",
                "summary": fallback_text[:800],
                "sections": {"content": fallback_text},
                "infobox": {},
                "tables": [],
            }

        lowered_url = str(url or "").lower()
        if any(x in lowered_url for x in ["fandom.com", "wikipedia.org"]):
            llm_content = self._format_generic_llm_content(parsed_data, soup)
        else:
            llm_content = wiki_parser.format_for_llm(parsed_data, max_chars=self.MAX_LLM_CHARS)
            # 追加更多正文段落，尽可能还原页面信息
            extra_paragraphs = []
            for p in soup.find_all("p")[:80]:
                text = p.get_text(" ", strip=True)
                if len(text) >= 20:
                    extra_paragraphs.append(text)
            if extra_paragraphs:
                llm_content = f"{llm_content}\n\n" + "\n".join(extra_paragraphs)

            # 再追加纯文本兜底，确保长内容传递给 LLM
            fallback_text = self._extract_text_from_soup(soup)
            if fallback_text:
                llm_content = f"{llm_content}\n\n{fallback_text[:20000]}"
        links: List[Dict[str, str]]
        parsed_check = urlparse(str(url or ""))
        if self._is_moegirl_domain(parsed_check.netloc):
            content_root = soup.find("div", class_="mw-parser-output") or soup.find("body")
            links = (
                self._extract_moegirl_outgoing_links_from_html(content_root, url, cap=self.MOEGIRL_MAX_LINKS)
                if content_root
                else []
            )
        else:
            links = self._extract_links(soup, url)

        preview_content = wiki_parser.format_for_preview(parsed_data, max_chars=self.MAX_PREVIEW_CHARS)
        if not preview_content:
            preview_content = llm_content[: self.MAX_PREVIEW_CHARS]

        # For JS-heavy pages (e.g., Moegirl), text may be empty while anchors are still available.
        if (not preview_content or len(preview_content.strip()) < 20) and links:
            link_lines = "\n".join([f"- {item.get('title')}" for item in links[:80] if item.get("title")])
            fallback_title = parsed_data.get("title") or title or "Untitled"
            preview_content = f"{fallback_title}\n\n页面正文提取受限，以下为页面可识别词条：\n{link_lines}".strip()
            llm_content = preview_content if not llm_content or len(llm_content.strip()) < 20 else llm_content

        is_list_page = len(links) > 10

        return {
            "success": True,
            "title": parsed_data.get("title") or title or "Untitled",
            "content": self._clean_content(preview_content),
            "llm_content": self._clean_content(llm_content),
            "links": links,
            "is_list_page": is_list_page,
            "url": url,
        }

    def _extract_text_from_soup(self, soup: BeautifulSoup) -> str:
        """Extract main text content from soup with smart list/table handling"""
        content_selectors = [
            ("div", {"class": "mw-parser-output"}),
            ("div", {"id": "mw-content-text"}),
            ("div", {"class": "page-content"}),
            ("article", {}),
            ("main", {}),
        ]

        content = None
        for tag, attrs in content_selectors:
            content = soup.find(tag, attrs) if attrs else soup.find(tag)
            if content:
                break

        if not content:
            content = soup.find("body")

        if not content:
            return ""

        tables = content.find_all("table")
        lists = content.find_all(["ul", "ol"])
        is_list_heavy = len(tables) > 2 or len(lists) > 3

        for unwanted in content.find_all(["nav", "aside", "footer", "script", "style", "noscript"]):
            unwanted.decompose()

        if not is_list_heavy:
            for cls in ["navbox", "toc", "sidebar", "infobox", "navigation", "mw-editsection", "reference"]:
                for elem in content.find_all(class_=re.compile(cls, re.I)):
                    elem.decompose()

        text_parts = []
        first_p = content.find("p")
        if first_p:
            intro = first_p.get_text(" ", strip=True)
            if intro and len(intro) > 20:
                text_parts.append(f"## 简介\n{intro}\n")

        for elem in content.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
            text = elem.get_text(" ", strip=True)
            if not text or len(text) < 3:
                continue

            if elem.name in ["h1", "h2"]:
                text_parts.append(f"\n## {text}\n")
            elif elem.name in ["h3", "h4"]:
                text_parts.append(f"\n### {text}\n")
            else:
                text_parts.append(text)

        if len(text_parts) < 5 and tables:
            for table in tables[:3]:
                rows = table.find_all("tr")
                for row in rows[:10]:
                    cells = row.find_all(["td", "th"])
                    row_text = " | ".join([c.get_text(" ", strip=True) for c in cells if c.get_text(" ", strip=True)])
                    if row_text:
                        text_parts.append(row_text)

        result = "\n\n".join(text_parts)
        if not result or len(result) < 50:
            result = content.get_text(separator="\n", strip=True)[:1000]

        return result

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
        """Extract internal links with enhanced table/category support"""
        base_domain = urlparse(base_url).netloc
        links: List[Dict[str, str]] = []
        seen_urls = set()

        content = soup.find("div", class_="mw-parser-output") or soup.find("body")
        if not content:
            return []

        def add_link(href: str, link_text: str, link_title: str = "") -> None:
            full_url = urljoin(base_url, href)
            full_url, _ = urldefrag(full_url)
            full_domain = urlparse(full_url).netloc
            if full_domain != base_domain:
                # 萌娘百科在移动/桌面/分站之间可能混用域名：mzh / zh / www
                if "moegirl.org" in (base_domain or "") and "moegirl.org" in (full_domain or ""):
                    pass
                else:
                    return
            if any(x in href.lower() for x in ["special:", "file:", "talk:", "template:", "user:"]):
                return
            text = str(link_text or "").strip()
            title_attr = str(link_title or "").strip()
            if (not text or len(text) < 2) and title_attr:
                text = title_attr
            if (not text or len(text) < 2) and "moegirl.org" in (full_domain or ""):
                parsed = urlparse(full_url)
                guessed = self._moegirl_article_title_from_url(parsed)
                if guessed:
                    text = guessed
            if not text or len(text) < 2:
                return
            if full_url not in seen_urls:
                seen_urls.add(full_url)
                links.append({"title": text, "url": full_url})

        tables = content.find_all("table")
        for table in tables[:20]:
            for a_tag in table.find_all("a", href=True):
                add_link(a_tag["href"], a_tag.get_text(strip=True), a_tag.get("title"))
                if len(links) >= self.MAX_LINKS:
                    break
            if len(links) >= self.MAX_LINKS:
                break

        if len(links) < self.MAX_LINKS:
            lists = content.find_all(["ul", "ol"])
            for lst in lists[:20]:
                for a_tag in lst.find_all("a", href=True):
                    add_link(a_tag["href"], a_tag.get_text(strip=True), a_tag.get("title"))
                    if len(links) >= self.MAX_LINKS:
                        break
                if len(links) >= self.MAX_LINKS:
                    break

        if len(links) < self.MAX_LINKS:
            for a_tag in content.find_all("a", href=True):
                add_link(a_tag["href"], a_tag.get_text(strip=True), a_tag.get("title"))
                if len(links) >= self.MAX_LINKS:
                    break

        return links

    def _clean_content(self, content: str) -> str:
        """Clean and normalize content"""
        if not content:
            return ""

        content = re.sub(r"\n{3,}", "\n\n", content)
        content = re.sub(r" {2,}", " ", content)
        return content.strip()

    async def scrape_pages_concurrent(self, urls: List[str], concurrency: int = 6) -> List[Dict[str, Any]]:
        """
        Scrape multiple pages concurrently using thread pool executor.
        Returns a list of structured data compatible with extraction.
        """
        import concurrent.futures

        loop = asyncio.get_event_loop()

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [loop.run_in_executor(executor, self._scrape_for_batch, url) for url in urls]
            results = await asyncio.gather(*futures)

        return list(results)

    def _scrape_for_batch(self, url: str) -> Dict[str, Any]:
        """Wrapper around scrape_page that formats result for batch extraction"""
        try:
            result = self.scrape_page(url)
            return {
                "success": result.get("success", False),
                "url": url,
                "title": result.get("title", ""),
                "content": result.get("content", ""),
                "llm_content": result.get("llm_content", ""),
                "error": result.get("error"),
            }
        except Exception as exc:
            return {"success": False, "url": url, "error": safe_error_code(exc)}

    async def _scrape_single_async(
        self, session: aiohttp.ClientSession, url: str, semaphore: asyncio.Semaphore
    ) -> Dict[str, Any]:
        """Async scrape a single page and use WikiStructuredParser"""
        async with semaphore:
            try:
                headers = {"User-Agent": self.headers["User-Agent"]}
                parsed = urlparse(url)
                force_utf8 = any(x in parsed.netloc for x in ["moegirl", "baike", "zh."])

                timeout = aiohttp.ClientTimeout(total=30)
                async with session.get(url, headers=headers, timeout=timeout) as response:
                    if response.status != 200:
                        logger.warning("HTTP %s for %s", response.status, url)
                        return {"success": False, "url": url, "error": f"Status {response.status}"}

                    content_bytes = await response.read()

                    if force_utf8:
                        html = content_bytes.decode("utf-8", errors="replace")
                    else:
                        encoding = response.get_encoding() or "utf-8"
                        try:
                            html = content_bytes.decode(encoding, errors="replace")
                        except (UnicodeDecodeError, LookupError) as exc:
                            logger.warning("Failed to decode with %s: %s", encoding, exc)
                            html = content_bytes.decode("utf-8", errors="replace")

                    title = ""
                    try:
                        soup_preview = BeautifulSoup(html[:5000], "lxml")
                        title_tag = soup_preview.find("title")
                        if title_tag:
                            title = title_tag.get_text(strip=True).split(" - ")[0]
                    except Exception as exc:
                        logger.warning("Failed to extract title: %s", exc)

                    parsed_data = wiki_parser.parse_page(html, title=title or url.split("/")[-1])
                    parsed_data["url"] = url
                    parsed_data["success"] = True

                    if not any(
                        [
                            parsed_data.get("infobox"),
                            parsed_data.get("sections"),
                            parsed_data.get("summary"),
                            parsed_data.get("tables"),
                        ]
                    ):
                        soup = BeautifulSoup(html, "lxml")
                        content_div = soup.find("div", class_="mw-parser-output") or soup.find("body")
                        if content_div:
                            raw_paragraphs = []
                            for p in content_div.find_all("p")[:5]:
                                text = p.get_text(" ", strip=True)
                                if len(text) > 20:
                                    raw_paragraphs.append(text)

                            if raw_paragraphs:
                                parsed_data["summary"] = "\n".join(raw_paragraphs[:2])
                                parsed_data["sections"] = {"background": "\n".join(raw_paragraphs)}

                    parsed_data["llm_content"] = wiki_parser.format_for_llm(parsed_data, max_chars=self.MAX_LLM_CHARS)
                    return parsed_data

            except Exception as exc:
                logger.error("Concurrent scraper failed %s: %s", url, exc)
                return {"success": False, "url": url, "error": safe_error_code(exc)}


crawler_service = CrawlerService()
