"""Regression tests for editor auto-locate helpers."""

from __future__ import annotations

from app.agents.editor import EditorAgent


def _make_editor() -> EditorAgent:
    # These helper methods do not depend on initialized gateway/storage state.
    return EditorAgent.__new__(EditorAgent)


def test_extract_quoted_candidates_handles_symbols() -> None:
    editor = _make_editor()
    feedback = '请把“开头这句？”改成更自然；另外 "第二句!" 也要改。'
    candidates = editor._extract_quoted_candidates(feedback)
    assert "开头这句？" in candidates
    assert "第二句!" in candidates


def test_auto_locate_selection_by_quoted_text() -> None:
    editor = _make_editor()
    text = "第一段文本。\n\n第二段文本。\n\n第三段文本。"
    feedback = "把“第二段文本。”改得更紧凑"
    located = editor._auto_locate_selection(text, feedback)

    assert located is not None
    assert located["selection_text"] == "第二段文本。"


def test_auto_locate_selection_by_paragraph_index() -> None:
    editor = _make_editor()
    text = "A段。\n\nB段。\n\nC段。"
    feedback = "请优化第 2 段的节奏"
    located = editor._auto_locate_selection(text, feedback)

    assert located is not None
    assert located["selection_text"] == "B段。"
