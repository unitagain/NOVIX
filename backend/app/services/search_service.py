# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  搜索服务 - 为同人创作导入功能提供萌娘百科搜索，仅支持萌娘百科以确保稳定性和一致性。
  Search service for fanfiction import - Moegirlpedia OpenSearch wrapper providing stable, unified search results for wiki article discovery.
"""

from typing import List, Dict
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SearchService:
    """
    萌娘百科搜索服务包装 - 专用于同人创作导入的搜索。

    Wrapper around Moegirlpedia OpenSearch API.
    Restricts to Moegirlpedia only to ensure consistency and reliability.
    Normalizes URLs and deduplicates results.

    Attributes:
        moegirl_opensearch_api: 萌娘百科搜索 API URL / Moegirlpedia OpenSearch API endpoint
    """

    def __init__(self):
        self.moegirl_opensearch_api = "https://mzh.moegirl.org.cn/api.php"
        self.wikipedia_opensearch_api = "https://en.wikipedia.org/w/api.php"
        self.fandom_search_api = "https://www.fandom.com/api/v1/Search/List"
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

    def _safe_limit(self, max_results: int) -> int:
        return max(1, min(int(max_results or 10), 20))

    def _looks_cjk(self, text: str) -> bool:
        body = str(text or "").strip()
        if not body:
            return False
        return any(0x4E00 <= ord(ch) <= 0x9FFF for ch in body)

    def _merge_results(self, groups: List[List[Dict[str, str]]], limit: int) -> List[Dict[str, str]]:
        merged: List[Dict[str, str]] = []
        seen = set()
        for group in groups or []:
            for item in group or []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                title = str(item.get("title") or "").strip()
                if not url or not title:
                    continue
                key = url.lower()
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                if len(merged) >= limit:
                    return merged
        return merged

    def _normalize_moegirl_url(self, url: str) -> str:
        """
        Normalize Moegirlpedia URLs to a stable `index.php?title=...` form.

        输入可能是：
        - `https://mzh.moegirl.org.cn/index.php?title=词条`
        - `https://zh.moegirl.org.cn/词条`
        - `https://zh.moegirl.org.cn/wiki/词条`

        输出统一为：
        - `https://mzh.moegirl.org.cn/index.php?title=词条`
        """
        raw = str(url or "").strip()
        if not raw:
            return ""

        try:
            parsed = urlparse(raw)
        except Exception:
            return raw

        host = (parsed.netloc or "").lower()
        if "moegirl.org" not in host:
            return raw

        query = parse_qs(parsed.query or "")
        title = query.get("title", [None])[0]
        if title:
            title = unquote(str(title)).strip()
        else:
            path = (parsed.path or "").strip("/")
            if path.startswith("wiki/"):
                path = path[len("wiki/") :]
            if path and path not in {"index.php", "api.php"}:
                title = unquote(path).strip()

        if not title:
            return raw

        safe = quote(str(title).replace(" ", "_"), safe="")
        return f"https://mzh.moegirl.org.cn/index.php?title={safe}"

    def _search_moegirl(self, query: str, limit: int) -> List[Dict[str, str]]:
        """
        Search Moegirlpedia pages.

        Args:
            query: Search query
            limit: Maximum number of results to return

        Returns:
            List of search results with title, url, snippet, and source
        """
        q = str(query or "").strip()
        if not q:
            return []

        try:
            resp = requests.get(
                self.moegirl_opensearch_api,
                params={
                    "action": "opensearch",
                    "search": q,
                    "limit": limit,
                    "format": "json",
                },
                headers=self._headers,
                timeout=(3, 10),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Moegirl API error query=%s err=%s", q, exc)
            return []

        if not isinstance(data, list) or len(data) < 4:
            return []

        titles = data[1] or []
        descriptions = data[2] or []
        urls = data[3] or []

        results: List[Dict[str, str]] = []
        seen_urls = set()
        for i in range(min(len(titles), len(urls))):
            title = str(titles[i] or "").strip()
            url = self._normalize_moegirl_url(urls[i]) or str(urls[i] or "").strip()
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)

            desc = str(descriptions[i] or "").strip() if i < len(descriptions) else ""
            snippet = desc if desc else f"萌娘百科词条：{title}"

            results.append({"title": title, "url": url, "snippet": snippet, "source": "萌娘百科"})
            if len(results) >= limit:
                break

        return results

    def _search_wikipedia(self, query: str, limit: int) -> List[Dict[str, str]]:
        q = str(query or "").strip()
        if not q:
            return []

        try:
            resp = requests.get(
                self.wikipedia_opensearch_api,
                params={
                    "action": "opensearch",
                    "search": q,
                    "limit": limit,
                    "format": "json",
                },
                headers=self._headers,
                timeout=(3, 10),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Wikipedia API error query=%s err=%s", q, exc)
            return []

        if not isinstance(data, list) or len(data) < 4:
            return []

        titles = data[1] or []
        descriptions = data[2] or []
        urls = data[3] or []

        results: List[Dict[str, str]] = []
        seen_urls = set()
        for i in range(min(len(titles), len(urls))):
            title = str(titles[i] or "").strip()
            url = str(urls[i] or "").strip()
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)

            desc = str(descriptions[i] or "").strip() if i < len(descriptions) else ""
            snippet = desc if desc else f"Wikipedia article: {title}"
            results.append({"title": title, "url": url, "snippet": snippet, "source": "Wikipedia"})
            if len(results) >= limit:
                break
        return results

    def _search_fandom(self, query: str, limit: int) -> List[Dict[str, str]]:
        """
        Fandom global search API. Best-effort: if unavailable, return empty list.
        """
        q = str(query or "").strip()
        if not q:
            return []

        try:
            resp = requests.get(
                self.fandom_search_api,
                params={"query": q, "limit": limit, "ns": 0},
                headers=self._headers,
                timeout=(3, 12),
            )
            resp.raise_for_status()
            data = resp.json() or {}
        except Exception as exc:
            logger.error("Fandom API error query=%s err=%s", q, exc)
            return []

        items = data.get("items") or []
        results: List[Dict[str, str]] = []
        seen_urls = set()
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or item.get("articleUrl") or "").strip()
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            snippet = str(item.get("abstract") or item.get("snippet") or "").strip() or f"Fandom article: {title}"
            results.append({"title": title, "url": url, "snippet": snippet, "source": "Fandom"})
            if len(results) >= limit:
                break
        return results

    def search_wiki(self, query: str, max_results: int = 10, engine: str = "moegirl") -> List[Dict[str, str]]:
        """
        Search wiki pages across supported engines: moegirl, wikipedia, fandom.
        """
        limit = self._safe_limit(max_results)
        eng = str(engine or "").strip().lower()
        if eng in {"auto", "smart"}:
            # Heuristic routing:
            # - If query is CJK-heavy, prefer Moegirl.
            # - Otherwise, merge Fandom + Wikipedia for English queries.
            if self._looks_cjk(query):
                return self._search_moegirl(query, limit)
            fandom = self._search_fandom(query, limit)
            wiki = self._search_wikipedia(query, limit)
            return self._merge_results([fandom, wiki], limit)
        if eng in {"wikipedia", "wiki", "enwiki"}:
            return self._search_wikipedia(query, limit)
        if eng in {"fandom"}:
            return self._search_fandom(query, limit)
        return self._search_moegirl(query, limit)


# Singleton instance
search_service = SearchService()
