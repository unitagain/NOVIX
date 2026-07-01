"""Regression tests for modular Archivist prompt template exports."""

from __future__ import annotations

from app.prompt_templates.archivist import (
    archivist_canon_updates_prompt,
    archivist_chapter_summary_prompt,
    archivist_fanfiction_card_prompt,
    archivist_fanfiction_card_repair_prompt,
    archivist_focus_characters_binding_prompt,
    archivist_style_profile_prompt,
    archivist_volume_summary_prompt,
    get_archivist_system_prompt,
)


def test_archivist_facade_exports_system_prompt() -> None:
    prompt = get_archivist_system_prompt("zh")
    assert "Archivist" in prompt
    assert "结构化" in prompt


def test_archivist_facade_exports_style_prompt() -> None:
    prompt = archivist_style_profile_prompt("示例文本", language="zh")
    assert "A." in prompt.user
    assert "写作教练" in prompt.system


def test_archivist_facade_exports_fanfiction_prompts() -> None:
    extract_prompt = archivist_fanfiction_card_prompt("标题", "内容", language="zh")
    repair_prompt = archivist_fanfiction_card_repair_prompt("标题", "内容", hint="补全能力", language="zh")
    assert '"type": "Character|World"' in extract_prompt.user
    assert "补全能力" in repair_prompt.user


def test_archivist_facade_exports_summary_prompts() -> None:
    canon_prompt = archivist_canon_updates_prompt("1", "正文", language="zh")
    chapter_prompt = archivist_chapter_summary_prompt("1", "标题", "正文", language="zh")
    focus_prompt = archivist_focus_characters_binding_prompt(
        chapter="1",
        candidates=[{"name": "阿青", "stars": 3, "aliases": ["小青"]}],
        final_draft="阿青出场。",
        language="zh",
    )
    volume_prompt = archivist_volume_summary_prompt(
        volume_id="卷一",
        chapter_items=[{"chapter": "1", "brief_summary": "起始事件"}],
        language="zh",
    )

    assert "facts:" in canon_prompt.user
    assert "relations:" in canon_prompt.user  # Phase 4: 关系三元组抽取
    assert "context:" in canon_prompt.user  # Phase 4: 情境前缀
    assert "brief_summary:" in chapter_prompt.user
    assert "focus_characters:" in focus_prompt.user
    assert "volume_id: 卷一" in volume_prompt.user
