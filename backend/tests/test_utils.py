"""Test utilities in app.utils.*"""

import pytest
from bs4 import BeautifulSoup
from app.utils.text import normalize_for_compare, normalize_newlines, normalize_prose_paragraphs
from app.utils.path_safety import sanitize_id, validate_path_within
from app.services.wiki_parser import WikiStructuredParser

# --- stopwords unification (Phase 4 dedup) ---


class TestStopwordsUnified:
    """get_stopwords() 是停用词的唯一来源：含中/英文 + yaml 文件词的并集。"""

    def test_union_includes_former_tokenizer_only_words(self):
        from app.utils.stopwords import get_stopwords

        sw = get_stopwords()
        # 这些词历史上仅存在于 text_tokenizer 的硬编码集合，去重后应进入统一集合
        assert "它" in sw and "怎么" in sw  # CJK pronouns/interrogatives
        assert "with" in sw and "myself" in sw  # English
        # 也应包含 yaml/默认里的词
        assert "的" in sw and "chapter" in sw

    def test_tokenizer_filters_via_shared_source(self):
        from app.context_engine.text_tokenizer import tokenize

        tokens = tokenize("他和她在城里行走", remove_stopwords=True)
        assert "他" not in tokens and "她" not in tokens and "和" not in tokens


# --- normalize_newlines ---


class TestNormalizeNewlines:
    def test_none_returns_empty(self):
        assert normalize_newlines(None) == ""

    def test_empty_string(self):
        assert normalize_newlines("") == ""

    def test_crlf(self):
        assert normalize_newlines("a\r\nb") == "a\nb"

    def test_cr(self):
        assert normalize_newlines("a\rb") == "a\nb"

    def test_mixed(self):
        assert normalize_newlines("a\r\nb\rc\nd") == "a\nb\nc\nd"


# --- normalize_for_compare ---


class TestNormalizeForCompare:
    def test_strips_trailing(self):
        assert normalize_for_compare("hello\r\n  ") == "hello"

    def test_none(self):
        assert normalize_for_compare(None) == ""


class TestNormalizeProseParagraphs:
    def test_collapses_over_fragmented_chinese_paragraphs(self):
        text = (
            "沈鸿站在山脚下，望着那座山。\n\n"
            "七年前，这里还是他的家。\n\n"
            "如今却成了伤心地。\n\n"
            "山门紧闭，铁锁在风中晃荡。\n\n"
            "发出轻响。"
        )
        result = normalize_prose_paragraphs(text, language="zh")
        assert result.count("\n\n") < text.count("\n\n")
        assert "沈鸿站在山脚下，望着那座山。七年前，这里还是他的家。如今却成了伤心地。" in result

    def test_preserves_dialogue_paragraphs(self):
        text = "“你走吧。”\n\n“我不走。”\n\n山风吹过。"
        result = normalize_prose_paragraphs(text, language="zh")
        assert "“你走吧。”" in result
        assert "“我不走。”" in result


# --- sanitize_id ---


class TestSanitizeId:
    def test_simple(self):
        assert sanitize_id("hello") == "hello"

    def test_spaces_to_underscores(self):
        assert sanitize_id("my project") == "my_project"

    def test_traversal_removed(self):
        result = sanitize_id("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            sanitize_id("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            sanitize_id(None)

    def test_dots_only_raises(self):
        with pytest.raises(ValueError):
            sanitize_id("...")

    def test_max_length(self):
        long_id = "a" * 100
        result = sanitize_id(long_id, max_length=10)
        assert len(result) <= 10

    def test_chinese_preserved(self):
        result = sanitize_id("我的项目")
        assert "我的项目" == result


# --- validate_path_within ---


class TestValidatePathWithin:
    def test_valid_child(self, tmp_path):
        child = tmp_path / "sub" / "file.txt"
        child.parent.mkdir(parents=True, exist_ok=True)
        child.touch()
        result = validate_path_within(child, tmp_path)
        assert result == child.resolve()

    def test_traversal_rejected(self, tmp_path):
        evil = tmp_path / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="escapes"):
            validate_path_within(evil, tmp_path)


class TestWikiParser:
    def test_extract_tables_handles_pages_without_tables(self):
        parser = WikiStructuredParser()
        soup = BeautifulSoup("<html><body><p>Only prose here.</p></body></html>", "html.parser")
        assert parser.extract_tables(soup) == []
