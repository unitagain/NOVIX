# -*- coding: utf-8 -*-
"""写前反问健壮化回归测试：_coerce_questions 容错抽取（修复"质量太低回退固定问题"的根因）。
覆盖：裸数组 / 对象包裹数组 / 近义键 / 字符串项 / 缺 type 默认 / 空与垃圾 → []。
"""

from app.agents.writer import WriterAgent


def test_bare_list_of_typed_questions():
    out = WriterAgent._coerce_questions([{"type": "plot", "text": "A?"}, {"type": "detail", "text": "B?"}])
    assert out == [{"type": "plot", "text": "A?"}, {"type": "detail", "text": "B?"}]


def test_object_wrapped_array_is_salvaged():
    # response_format=json_object 强制对象时，模型把数组包进 {"questions":[...]}
    out = WriterAgent._coerce_questions({"questions": [{"type": "p", "text": "Q1"}]})
    assert out == [{"type": "p", "text": "Q1"}]


def test_object_wrapped_under_unknown_key_first_list_value():
    out = WriterAgent._coerce_questions({"result": [{"text": "Q"}]})
    assert out == [{"type": "clarify", "text": "Q"}]


def test_synonym_keys_question_and_category():
    out = WriterAgent._coerce_questions([{"category": "motive", "question": "为何？"}])
    assert out == [{"type": "motive", "text": "为何？"}]  # question→text, category→type


def test_text_synonyms_q_and_content():
    out = WriterAgent._coerce_questions([{"q": "X?"}, {"content": "Y?"}])
    assert [i["text"] for i in out] == ["X?", "Y?"]
    assert all(i["type"] == "clarify" for i in out)  # 缺 type → 默认 clarify


def test_plain_string_items_accepted():
    out = WriterAgent._coerce_questions(["第一问？", "第二问？"])
    assert out == [{"type": "clarify", "text": "第一问？"}, {"type": "clarify", "text": "第二问？"}]


def test_blank_text_items_skipped():
    out = WriterAgent._coerce_questions([{"type": "x", "text": "  "}, {"type": "y", "text": "ok"}])
    assert out == [{"type": "y", "text": "ok"}]


def test_empty_and_garbage_return_empty():
    assert WriterAgent._coerce_questions(None) == []
    assert WriterAgent._coerce_questions({}) == []
    assert WriterAgent._coerce_questions({"meta": "no-list"}) == []
    assert WriterAgent._coerce_questions("just a string") == []
    assert WriterAgent._coerce_questions([1, 2, 3]) == []  # 非 str/dict 项跳过 → []
