# -*- coding: utf-8 -*-
"""P8 contextual prefix helpers tests."""

from app.context_engine.contextual_prefix import build_contextual_prefix, ensure_contextual_prefix, prefix_coverage


def test_build_contextual_prefix_for_core_item_types():
    fact = build_contextual_prefix("fact", {"source": "V1C004", "scope": "project", "context": "玉佩线"})
    summary = build_contextual_prefix("summary", {"chapter": "V1C004", "title": "章四"})
    draft = build_contextual_prefix("draft", {"chapter": "V1C005"})
    memory = build_contextual_prefix("memory", {"memory_type": "preference", "scope": "project", "source": "author"})
    assert "type:canon_fact" in fact and "玉佩线" in fact
    assert "type:summary" in summary
    assert "type:draft" in draft
    assert "type:memory" in memory and "preference" in memory


def test_ensure_contextual_prefix_does_not_overwrite_existing_prefix():
    item = ensure_contextual_prefix("fact", {"id": "F1", "context_prefix": "已有情境"})
    assert item["context_prefix"] == "已有情境"


def test_prefix_coverage_reports_by_type():
    coverage = prefix_coverage(
        [
            {"type": "fact", "context_prefix": "a"},
            {"type": "fact"},
            {"type": "memory", "context": "b"},
        ]
    )
    assert coverage["total"] == 3
    assert coverage["with_prefix"] == 2
    assert coverage["by_type"]["fact"]["coverage"] == 0.5
    assert coverage["by_type"]["memory"]["coverage"] == 1.0
