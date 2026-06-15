# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  文本分词器 - 轻量级分词器，支持中文和英文的相关性评分
  Text Tokenizer - Lightweight tokenizer with CJK support for relevance scoring.

功能 / Features:
  - 中文分词：使用jieba或简单n-gram / Chinese tokenization: jieba or n-gram
  - 英文分词：基于单词边界 / English tokenization: word boundary based
  - 停用词过滤：内置中英文停用词 / Stopword filtering: built-in CJK/English stopwords
  - 相关性评分：支持Jaccard和BM25算法 / Relevance scoring: Jaccard and BM25 support
"""

import re
import math
from typing import List, Set, Dict, Optional

# 尝试导入 jieba / Try to import jieba
_jieba_available = False
try:
    import jieba

    jieba.setLogLevel(jieba.logging.INFO)  # 减少日志输出 / Reduce logging
    _jieba_available = True
except ImportError:
    pass


# 中文字符范围 / CJK character ranges
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
# 英文单词 / English words
_WORD_PATTERN = re.compile(r"[a-zA-Z]+")
# 数字 / Numbers
_NUMBER_PATTERN = re.compile(r"\d+")


# 常见中文停用词 / Common Chinese stopwords
_CHINESE_STOPWORDS = frozenset(
    [
        "的",
        "了",
        "是",
        "在",
        "我",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "你",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
        "那",
        "他",
        "她",
        "它",
        "们",
        "这个",
        "那个",
        "什么",
        "怎么",
        "为什么",
        "哪",
        "哪里",
        "哪个",
        "谁",
        "多少",
        "几",
        "如何",
        "为",
        "与",
        "及",
        "或",
        "但",
        "而",
        "因为",
        "所以",
        "如果",
        "虽然",
        "但是",
        "然后",
        "之后",
        "之前",
        "可以",
        "能",
        "会",
        "应该",
        "必须",
        "需要",
        "想",
        "要",
        "让",
        "把",
        "被",
        "给",
        "从",
        "向",
        "对",
        "于",
        "以",
        "等",
        "等等",
        "还",
        "又",
        "再",
    ]
)

# 英文停用词 / Common English stopwords
_ENGLISH_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "and",
        "but",
        "if",
        "or",
        "because",
        "until",
        "while",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "myself",
        "we",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "it",
        "its",
        "they",
        "them",
        "their",
        "what",
        "which",
        "who",
        "whom",
    ]
)


def tokenize(text: str, remove_stopwords: bool = True) -> List[str]:
    """
    分词函数，支持中英文混合文本

    Tokenize text with support for mixed Chinese/English.

    分词过程：
    1. 提取并分词中文部分（使用jieba或n-gram）
    2. 提取英文单词
    3. 提取数字
    4. 可选：移除停用词
    5. 可选：移除单字符（除数字）

    Tokenization process:
    1. Extract and tokenize CJK parts (jieba or n-gram)
    2. Extract English words
    3. Extract numbers
    4. Optional: remove stopwords
    5. Optional: remove single chars (except numbers)

    Args:
        text: 输入文本 / Input text
        remove_stopwords: 是否移除停用词 / Whether to remove stopwords

    Returns:
        分词结果列表 / List of tokens

    Example:
        >>> tokenize("我爱读书，尤其是science fiction")
        ['我', '爱', '读', '书', 'science', 'fiction']
    """
    if not text:
        return []

    text_lower = text.lower()
    tokens = []

    # 提取中文部分
    cjk_segments = _CJK_PATTERN.findall(text_lower)
    for segment in cjk_segments:
        if _jieba_available:
            # 使用 jieba 分词
            words = list(jieba.cut(segment))
            tokens.extend(words)
        else:
            # 简单的 n-gram 分词（2-gram 和 3-gram）
            tokens.extend(_simple_cjk_tokenize(segment))

    # 提取英文单词
    english_words = _WORD_PATTERN.findall(text_lower)
    tokens.extend(english_words)

    # 提取数字
    numbers = _NUMBER_PATTERN.findall(text_lower)
    tokens.extend(numbers)

    # 移除停用词
    if remove_stopwords:
        tokens = [t for t in tokens if t not in _CHINESE_STOPWORDS and t not in _ENGLISH_STOPWORDS]

    # 移除单字符（除了数字）
    tokens = [t for t in tokens if len(t) > 1 or t.isdigit()]

    return tokens


def _simple_cjk_tokenize(text: str) -> List[str]:
    """
    简单的中文分词（无jieba时使用）

    Simple Chinese tokenization without jieba.

    使用n-gram方法（2-gram和3-gram）进行分词。
    Uses n-gram approach (2-gram and 3-gram) for tokenization.

    Args:
        text: 中文文本 / Chinese text

    Returns:
        token列表 / List of tokens
    """
    if len(text) <= 1:
        return [text] if text else []

    tokens = []

    # 2-gram
    for i in range(len(text) - 1):
        tokens.append(text[i : i + 2])

    # 3-gram（如果文本足够长）
    if len(text) >= 3:
        for i in range(len(text) - 2):
            tokens.append(text[i : i + 3])

    # 也保留原始文本作为一个 token（如果不太长）
    if len(text) <= 6:
        tokens.append(text)

    return tokens


def get_token_set(text: str, remove_stopwords: bool = True) -> Set[str]:
    """
    获取文本的token集合（去重）

    Get set of tokens from text (deduplicated).

    Args:
        text: 输入文本 / Input text
        remove_stopwords: 是否移除停用词 / Whether to remove stopwords

    Returns:
        token集合 / Set of tokens

    Example:
        >>> get_token_set("hello world hello")
        {'hello', 'world'}
    """
    return set(tokenize(text, remove_stopwords))


def calculate_overlap_score(query: str, content: str) -> float:
    """
    计算查询和内容之间的重叠分数

    Calculate overlap score between query and content.

    使用修改的Jaccard相似度，考虑完全匹配的bonus。
    Uses variant of Jaccard similarity with exact match bonus.

    Args:
        query: 查询文本 / Query text
        content: 内容文本 / Content text

    Returns:
        重叠分数 (0.0 - 1.0) / Overlap score (0.0 to 1.0)

    Example:
        >>> calculate_overlap_score("hello world", "hello there world")
        0.7
    """
    query_tokens = get_token_set(query)
    content_tokens = get_token_set(content)

    if not query_tokens:
        return 0.0

    # 计算 Jaccard 相似度的变体
    # 使用查询 tokens 作为基准
    overlap = len(query_tokens & content_tokens)

    # 基础分数：重叠比例
    base_score = overlap / len(query_tokens)

    # 加权：如果内容包含完整的查询 token，给予额外分数
    exact_match_bonus = 0.0
    for token in query_tokens:
        if token in content.lower():
            exact_match_bonus += 0.1

    return min(1.0, base_score + exact_match_bonus)


def calculate_bm25_score(
    query: str,
    content: str,
    avg_doc_length: float = 500.0,
    k1: float = 1.5,
    b: float = 0.75,
    idf_table: Optional[Dict[str, float]] = None,
) -> float:
    """
    计算 BM25 分数，支持可选的 IDF 加权

    Calculate BM25 score with optional IDF weighting.

    当提供 idf_table 时，使用语料级 IDF 区分稀有词和常用词（标准 BM25）。
    未提供时退化为仅 TF + 长度归一化的简化版本（向后兼容）。

    When idf_table is provided, uses corpus-level IDF to distinguish rare terms
    from common ones (standard BM25). Without it, falls back to simplified
    TF + length normalization (backward compatible).

    Args:
        query: 查询文本 / Query text
        content: 内容文本 / Content text
        avg_doc_length: 平均文档长度（token） / Average document length in tokens
        k1: BM25 参数（通常 1.5） / BM25 parameter (typically 1.5)
        b: BM25 参数（通常 0.75） / BM25 parameter (typically 0.75)
        idf_table: 词 -> IDF 值的映射（由 build_idf_table 生成） /
            Token-to-IDF mapping (built by build_idf_table). Optional.

    Returns:
        BM25 分数 / BM25 score
    """
    query_tokens = tokenize(query)
    content_tokens = tokenize(content)

    if not query_tokens or not content_tokens:
        return 0.0

    doc_length = len(content_tokens)
    content_token_set = set(content_tokens)

    score = 0.0
    for token in query_tokens:
        if token in content_token_set:
            tf = content_tokens.count(token)
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (doc_length / avg_doc_length))
            tf_norm = numerator / denominator
            # 有 IDF 表时使用标准 BM25（IDF × TF_norm），否则仅 TF_norm
            idf = idf_table.get(token, 1.0) if idf_table else 1.0
            score += idf * tf_norm

    return score


def build_idf_table(documents: List[str]) -> Dict[str, float]:
    """
    从文档集合构建 IDF（逆文档频率）表

    Build an IDF (Inverse Document Frequency) table from a collection of documents.

    使用 BM25 标准 IDF 公式：idf = ln((N - df + 0.5) / (df + 0.5) + 1)
    其中 N 为文档总数，df 为包含该词的文档数。

    Uses standard BM25 IDF formula: idf = ln((N - df + 0.5) / (df + 0.5) + 1)
    where N is total document count and df is document frequency.

    在 WenShape 中，每条事实/卡片的文本视为一个"文档"。

    In WenShape, each fact/card text is treated as a "document".

    Args:
        documents: 文档文本列表 / List of document texts

    Returns:
        词 -> IDF 值的字典 / Dict mapping tokens to IDF values
    """
    if not documents:
        return {}

    n = len(documents)
    df: Dict[str, int] = {}

    for doc in documents:
        # 每篇文档的去重 token 集合
        doc_tokens = get_token_set(str(doc or ""))
        for token in doc_tokens:
            df[token] = df.get(token, 0) + 1

    idf_table: Dict[str, float] = {}
    for token, freq in df.items():
        # BM25 标准 IDF，+1 防止负值
        idf_table[token] = math.log((n - freq + 0.5) / (freq + 0.5) + 1)

    return idf_table
