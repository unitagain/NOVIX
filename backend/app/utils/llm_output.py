# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  LLM输出解析工具 - 从可能包含噪声的LLM响应中弹性提取JSON
  LLM Output Parsing Helpers - Provide resilient JSON extraction for LLM responses.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Tuple


def parse_json_payload(
    text: str,
    expected_type: Optional[type] = None,
) -> Tuple[Optional[Any], str]:
    """
    从可能包含噪声的LLM响应中解析JSON

    Parse JSON payload from a possibly noisy LLM response.

    Attempts multiple strategies to extract valid JSON including:
    1. Direct parsing of the full text
    2. Extraction from markdown code blocks
    3. Extraction of JSON objects/arrays from within text
    4. Aggressive extraction for common LLM response patterns (新增)

    Args:
        text: LLM响应文本 / LLM response text
        expected_type: 期望的JSON类型（如dict、list） / Expected type (e.g., dict, list)

    Returns:
        元组 (数据, 错误消息) / Tuple of (data, error_message)
        - data: 解析后的JSON对象或None / Parsed data or None
        - error_message: 空字符串表示成功，否则为错误代码 / Empty string on success, error code otherwise

    Example:
        >>> parse_json_payload('{"key": "value"}')
        ({'key': 'value'}, '')
        >>> parse_json_payload('```json\\n{"key": "value"}\\n```')
        ({'key': 'value'}, '')
        >>> parse_json_payload('invalid')
        (None, 'json_parse_failed')
    """
    if not text or not str(text).strip():
        return None, "empty_response"

    candidates = _build_candidates(str(text))
    for candidate in candidates:
        data = _try_parse_json(candidate, expected_type)
        if data is not None:
            return data, ""

        for segment in _extract_json_segments(candidate):
            data = _try_parse_json(segment, expected_type)
            if data is not None:
                return data, ""

    # 新增：激进模式 - 处理 LLM 在 JSON 前后添加文字的情况
    # 例如："我生成的 JSON 如下：\n{...}\n这是最终答案"
    for segment in _extract_json_segments(str(text)):
        data = _try_parse_json(segment, expected_type)
        if data is not None:
            return data, ""

    return None, "json_parse_failed"


def _try_parse_json(text: str, expected_type: Optional[type]) -> Optional[Any]:
    """
    尝试解析JSON字符串

    Attempt to parse JSON with optional type validation.

    Args:
        text: JSON文本 / JSON text
        expected_type: 期望的类型 / Expected type

    Returns:
        解析的对象或None / Parsed object or None if invalid
    """
    try:
        data = json.loads(text)
    except Exception:
        return None
    if expected_type is not None and not isinstance(data, expected_type):
        return None
    return data


def _build_candidates(text: str) -> Iterable[str]:
    """
    生成JSON解析候选字符串

    Build candidate strings for JSON parsing.

    Yields candidates in priority order:
    1. Full text as-is
    2. Text extracted from markdown code blocks

    Args:
        text: 输入文本 / Input text

    Yields:
        候选解析字符串 / Candidate strings to parse
    """
    cleaned = text.strip()
    yield cleaned

    if "```" not in cleaned:
        return

    parts = cleaned.split("```")
    for i in range(1, len(parts), 2):
        segment = parts[i]
        if not segment:
            continue
        segment = segment.strip()
        lines = segment.splitlines()
        if lines and lines[0].strip().lower() in {"json", "jsonc", "yaml", "yml"}:
            segment = "\n".join(lines[1:]).strip()
        if segment:
            yield segment


def _extract_json_segments(text: str) -> Iterable[str]:
    """
    从文本中提取JSON对象/数组片段

    Extract JSON object/array segments from text.

    Uses bracket matching to identify complete JSON structures.
    Handles nested structures and quoted strings correctly.

    Args:
        text: 输入文本 / Input text

    Yields:
        有效的JSON片段 / Valid JSON segments
    """
    if not text:
        return []

    starts = [i for i, ch in enumerate(text) if ch in "{["]
    for start in starts:
        stack = []
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch in "{[":
                stack.append(ch)
                continue
            if ch in "}]":
                if not stack:
                    break
                open_ch = stack.pop()
                if (open_ch == "{" and ch != "}") or (open_ch == "[" and ch != "]"):
                    break
                if not stack:
                    yield text[start : idx + 1]
                    break
