# -*- coding: utf-8 -*-
"""
文本规范化工具。

Text normalization helpers for consistent processing and display.
"""

from __future__ import annotations

import re


def normalize_newlines(text: str | None) -> str:
    """
    规范化换行符（\r\n 和 \r 转换为 \n）。

    Normalize \\r\\n and \\r to \\n.
    """
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def normalize_for_compare(text: str | None) -> str:
    """
    规范化换行并去除尾部空白，便于文本比较。

    Normalize newlines and strip trailing whitespace for comparison.
    """
    return normalize_newlines(text).rstrip()


def normalize_prose_paragraphs(text: str | None, language: str = "zh") -> str:
    """
    规范化小说正文段落，尽量修复“一句一段”或“一行一段”的异常排版。

    Normalize prose paragraph layout and repair overly fragmented output where
    each sentence is emitted as a separate paragraph or line.
    """
    normalized = normalize_newlines(text).strip()
    if not normalized:
        return ""

    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    joiner = " " if str(language or "").lower().startswith("en") else ""
    normalized = re.sub(r"(?<!\n)\n(?!\n)", joiner, normalized)

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
    if len(paragraphs) < 4:
        return "\n\n".join(paragraphs)

    is_english = str(language or "").lower().startswith("en")
    short_threshold = 110 if is_english else 55
    target_threshold = 280 if is_english else 140
    sentence_endings = (
        (".", "!", "?", ".”", "!”", "?”") if is_english else ("。", "！", "？", "”", "。」", "！」", "？」")
    )

    def should_preserve(paragraph: str) -> bool:
        stripped = paragraph.strip()
        if not stripped:
            return True
        if re.match(r"^(#{1,6}\s|[-*•]\s|\d+[.)]\s|>+\s)", stripped):
            return True
        if stripped.startswith(("“", '"', "「", "『", "【", "—", "——")):
            return True
        return False

    short_plain_count = sum(1 for part in paragraphs if len(part) <= short_threshold and not should_preserve(part))
    if short_plain_count / max(len(paragraphs), 1) < 0.6:
        return "\n\n".join(paragraphs)

    merged: list[str] = []
    buffer: list[str] = []
    buffer_length = 0

    def flush_buffer() -> None:
        nonlocal buffer, buffer_length
        if not buffer:
            return
        merged.append(joiner.join(buffer) if is_english else "".join(buffer))
        buffer = []
        buffer_length = 0

    for paragraph in paragraphs:
        if should_preserve(paragraph):
            flush_buffer()
            merged.append(paragraph)
            continue

        buffer.append(paragraph)
        buffer_length += len(paragraph)

        if buffer_length >= target_threshold or (paragraph.endswith(sentence_endings) and len(buffer) >= 3):
            flush_buffer()

    flush_buffer()
    return "\n\n".join(merged)
