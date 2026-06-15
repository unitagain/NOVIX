# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  智能压缩器 - 智能内容压缩模块，保留关键信息同时削减冗余内容。
  Smart compressor - Intelligent content compression that preserves key information while removing redundancy.
"""

import re
from typing import List, Tuple, Optional
from app.utils.logger import get_logger

logger = get_logger(__name__)


# 句子分隔符
_SENTENCE_PATTERN = re.compile(r'([。！？.!?；;]+["\'"」』）)]*' r"|(?:——|……)" r"|[\n]{2,})")

# 关键词模式（用于识别重要句子）
_KEY_PATTERNS = [
    # 角色相关
    re.compile(r"(性格|外貌|特征|能力|技能|身份|职业|关系)", re.IGNORECASE),
    # 情节相关
    re.compile(r"(转折|关键|重要|核心|秘密|真相|原因|目的)", re.IGNORECASE),
    # 世界观相关
    re.compile(r"(规则|法则|禁忌|限制|条件|前提)", re.IGNORECASE),
    # 时间相关
    re.compile(r"(之前|之后|同时|期间|最终|开始|结束)", re.IGNORECASE),
    # 因果相关
    re.compile(r"(因为|所以|导致|造成|引发|结果)", re.IGNORECASE),
    # 英文关键词
    re.compile(r"(important|key|critical|secret|truth|reason|purpose)", re.IGNORECASE),
]

# 段落标记
_PARAGRAPH_MARKERS = re.compile(r"^(#{1,3}\s|[-*]\s|\d+\.\s|【|「|『)")


def split_sentences(text: str) -> List[str]:
    """
    将文本分割成句子

    Args:
        text: 输入文本

    Returns:
        句子列表
    """
    if not text:
        return []

    # 使用正则分割
    parts = _SENTENCE_PATTERN.split(text)

    sentences = []
    current = ""

    for part in parts:
        if not part:
            continue
        if _SENTENCE_PATTERN.match(part):
            # 这是分隔符，附加到当前句子
            current += part
            if current.strip():
                sentences.append(current.strip())
            current = ""
        else:
            current += part

    # 处理最后一个句子
    if current.strip():
        sentences.append(current.strip())

    return sentences


def score_sentence(sentence: str, query: Optional[str] = None) -> float:
    """
    为句子打分，评估其重要性

    Args:
        sentence: 句子
        query: 可选的查询（用于相关性评分）

    Returns:
        重要性分数 (0.0 - 1.0)
    """
    if not sentence:
        return 0.0

    score = 0.0

    # 1. 关键词匹配
    for pattern in _KEY_PATTERNS:
        if pattern.search(sentence):
            score += 0.2

    # 2. 段落标记（通常是标题或列表项）
    if _PARAGRAPH_MARKERS.match(sentence):
        score += 0.15

    # 3. 长度适中的句子通常更有信息量
    length = len(sentence)
    if 20 <= length <= 100:
        score += 0.1
    elif 100 < length <= 200:
        score += 0.05

    # 4. 包含数字（可能是重要数据）
    if re.search(r"\d+", sentence):
        score += 0.05

    # 5. 包含引号（可能是对话或引用）
    if re.search(r'[""\'\'「」『』]', sentence):
        score += 0.05

    # 6. 查询相关性
    if query:
        query_lower = query.lower()
        sentence_lower = sentence.lower()
        # 简单的词重叠
        query_words = set(query_lower.split())
        overlap = sum(1 for w in query_words if w in sentence_lower)
        if overlap > 0:
            score += min(0.3, overlap * 0.1)

    return min(1.0, score)


def smart_compress(
    content: str,
    target_ratio: float = 0.5,
    query: Optional[str] = None,
    preserve_structure: bool = True,
) -> Tuple[str, dict]:
    """
    智能压缩内容，保留关键信息

    策略：
    1. 保留开头（通常包含主题/背景）
    2. 保留结尾（通常包含结论/结果）
    3. 从中间选择最重要的句子

    Args:
        content: 原始内容
        target_ratio: 目标压缩比例 (0.0 - 1.0)
        query: 可选的查询（用于相关性评分）
        preserve_structure: 是否保留段落结构

    Returns:
        (压缩后的内容, 压缩统计信息)
    """
    if not content or target_ratio >= 1.0:
        return content, {"compressed": False}

    original_length = len(content)
    target_length = int(original_length * target_ratio)

    if original_length <= target_length:
        return content, {"compressed": False}

    # 分割成句子
    sentences = split_sentences(content)

    if len(sentences) <= 3:
        # 句子太少，使用简单的头尾截取
        return _simple_compress(content, target_length)

    # 为每个句子打分
    scored_sentences = [(i, sentence, score_sentence(sentence, query)) for i, sentence in enumerate(sentences)]

    # 计算预算分配
    head_budget = int(target_length * 0.30)  # 30% 给开头
    tail_budget = int(target_length * 0.30)  # 30% 给结尾
    middle_budget = target_length - head_budget - tail_budget  # 40% 给中间重要句子

    # 选择开头句子
    head_sentences = []
    head_length = 0
    for i, sentence, _ in scored_sentences:
        if head_length + len(sentence) <= head_budget:
            head_sentences.append((i, sentence))
            head_length += len(sentence)
        else:
            break

    # 选择结尾句子
    tail_sentences = []
    tail_length = 0
    for i, sentence, _ in reversed(scored_sentences):
        if i <= (head_sentences[-1][0] if head_sentences else -1):
            break
        if tail_length + len(sentence) <= tail_budget:
            tail_sentences.insert(0, (i, sentence))
            tail_length += len(sentence)
        else:
            break

    # 选择中间重要句子
    head_end = head_sentences[-1][0] if head_sentences else -1
    tail_start = tail_sentences[0][0] if tail_sentences else len(sentences)

    middle_candidates = [(i, sentence, score) for i, sentence, score in scored_sentences if head_end < i < tail_start]

    # 按分数排序
    middle_candidates.sort(key=lambda x: x[2], reverse=True)

    middle_sentences = []
    middle_length = 0
    for i, sentence, score in middle_candidates:
        if middle_length + len(sentence) <= middle_budget:
            middle_sentences.append((i, sentence))
            middle_length += len(sentence)

    # 按原始顺序排序中间句子
    middle_sentences.sort(key=lambda x: x[0])

    # 组装结果
    result_parts = []

    # 添加开头
    if head_sentences:
        result_parts.append("".join(s for _, s in head_sentences))

    # 添加省略标记和中间句子
    if middle_sentences:
        if head_sentences:
            result_parts.append("\n[...]\n")
        result_parts.append("".join(s for _, s in middle_sentences))

    # 添加省略标记和结尾
    if tail_sentences:
        if middle_sentences or head_sentences:
            result_parts.append("\n[...]\n")
        result_parts.append("".join(s for _, s in tail_sentences))

    compressed = "".join(result_parts)

    stats = {
        "compressed": True,
        "original_length": original_length,
        "compressed_length": len(compressed),
        "ratio": len(compressed) / original_length,
        "sentences_original": len(sentences),
        "sentences_kept": len(head_sentences) + len(middle_sentences) + len(tail_sentences),
        "method": "smart_compress",
    }

    return compressed, stats


def _simple_compress(content: str, target_length: int) -> Tuple[str, dict]:
    """
    简单压缩（用于短文本）
    """
    head_len = int(target_length * 0.6)
    tail_len = target_length - head_len - 10  # 预留省略标记空间

    head = content[:head_len].rstrip()
    tail = content[-tail_len:].lstrip() if tail_len > 0 else ""

    if tail:
        compressed = f"{head}\n[...]\n{tail}"
    else:
        compressed = head

    return compressed, {
        "compressed": True,
        "original_length": len(content),
        "compressed_length": len(compressed),
        "ratio": len(compressed) / len(content),
        "method": "simple_head_tail",
    }


def compress_for_context(
    content: str,
    max_tokens: int,
    query: Optional[str] = None,
) -> Tuple[str, dict]:
    """
    为上下文压缩内容到指定 token 数

    Args:
        content: 原始内容
        max_tokens: 最大 token 数
        query: 可选的查询

    Returns:
        (压缩后的内容, 压缩统计信息)
    """
    from app.context_engine.token_counter import count_tokens

    current_tokens = count_tokens(content)

    if current_tokens <= max_tokens:
        return content, {"compressed": False, "tokens": current_tokens}

    # 计算目标比例
    target_ratio = max_tokens / current_tokens * 0.95  # 留 5% 余量

    compressed, stats = smart_compress(content, target_ratio, query)

    # 验证压缩结果
    final_tokens = count_tokens(compressed)
    stats["tokens"] = final_tokens

    # 如果还是超出，进行二次压缩
    if final_tokens > max_tokens:
        second_ratio = max_tokens / final_tokens * 0.9
        compressed, second_stats = smart_compress(compressed, second_ratio, query)
        stats["second_pass"] = True
        stats["tokens"] = count_tokens(compressed)

    return compressed, stats
