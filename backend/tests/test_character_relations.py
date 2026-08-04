"""U4 角色关系图谱回归：卡片层设定边的读写/校验/级联，以及 Agent 侧合并消费。

资产边界（plan.md §2.5/§8.2）：设定边落 cards/relations.yaml，与角色卡同层；
Canon 的 relations.jsonl 仍只由档案员从正文抽取，两者互不写入对方。
全部用真实存储 + tmp_path + asyncio.run，无网络、无 API key。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.agents.tools import WriterToolset
from app.context_engine.relation_graph import Relation, RelationGraph
from app.storage.cards import CardStorage
from app.storage.character_relations import MAX_EDGES, MAX_LABEL_CHARS, CharacterRelationStorage
from app.schemas.card import CharacterCard


def _storage(tmp_path: Path) -> CharacterRelationStorage:
    return CharacterRelationStorage(str(tmp_path))


def _edge(source: str, target: str, relation: str, appellation: str = "", edge_id: str = "", reverse: str = "") -> dict:
    edge = {"from": source, "to": target, "relation": relation}
    if appellation:
        edge["appellation"] = appellation
    if reverse:
        edge["reverse_appellation"] = reverse
    if edge_id:
        edge["id"] = edge_id
    return edge


def _seed_characters(tmp_path: Path, *names: str) -> CardStorage:
    cards = CardStorage(str(tmp_path))
    for name in names:
        asyncio.run(cards.save_character_card("p", CharacterCard(name=name, description=f"{name} 的设定")))
    return cards


# --------------------------------------------------------------- 文档读写 --


def test_load_document_returns_empty_when_missing(tmp_path: Path):
    document = asyncio.run(_storage(tmp_path).load_document("p"))
    assert document == {"edges": [], "layout": {}}


def test_save_and_load_roundtrip(tmp_path: Path):
    storage = _storage(tmp_path)
    payload = {
        "edges": [_edge("林清越", "林清河", "姐姐", "阿姐", edge_id="a1b2c3d4", reverse="小河")],
        "layout": {"林清越": {"x": 120, "y": 80}, "林清河": {"x": 340, "y": 80}},
    }
    saved = asyncio.run(storage.save_document("p", payload, existing_characters=["林清越", "林清河"]))

    assert saved["edges"][0] == {
        "id": "a1b2c3d4",
        "from": "林清越",
        "to": "林清河",
        "relation": "姐姐",
        "appellation": "阿姐",
        "reverse_appellation": "小河",
    }
    assert saved["layout"]["林清越"] == {"x": 120.0, "y": 80.0}
    # 落盘位置在卡片层，不在 canon 层
    assert (tmp_path / "p" / "cards" / "relations.yaml").is_file()
    assert not (tmp_path / "p" / "canon").exists()

    reloaded = asyncio.run(storage.load_document("p"))
    assert reloaded == saved


def test_missing_edge_id_is_generated_and_unique(tmp_path: Path):
    storage = _storage(tmp_path)
    saved = asyncio.run(
        storage.save_document(
            "p",
            {"edges": [_edge("A", "B", "师父"), _edge("A", "B", "同门", edge_id="dup"), _edge("B", "A", "徒弟", edge_id="dup")]},
            existing_characters=["A", "B"],
        )
    )
    ids = [edge["id"] for edge in saved["edges"]]
    assert all(ids) and len(set(ids)) == 3  # 缺省补齐 + 冲突重生成，画布 id 唯一


def test_layout_prunes_unknown_characters(tmp_path: Path):
    storage = _storage(tmp_path)
    saved = asyncio.run(
        storage.save_document(
            "p",
            {"edges": [], "layout": {"林清越": {"x": 1, "y": 2}, "已删除的人": {"x": 3, "y": 4}}},
            existing_characters=["林清越"],
        )
    )
    assert list(saved["layout"]) == ["林清越"]  # 悬空坐标静默剪除，不报错


def test_corrupted_file_degrades_to_empty_document(tmp_path: Path):
    path = tmp_path / "p" / "cards" / "relations.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("edges: [this is: not: valid yaml", encoding="utf-8")

    assert asyncio.run(_storage(tmp_path).load_document("p")) == {"edges": [], "layout": {}}


# ----------------------------------------------------------------- 校验 --


def test_validate_rejects_self_loop(tmp_path: Path):
    errors = _storage(tmp_path).validate_document({"edges": [_edge("林清越", "林清越", "自己")]}, ["林清越"])
    assert any(error.startswith("relation_edge_self_loop") for error in errors)


def test_validate_rejects_duplicate_triple_but_allows_second_relation(tmp_path: Path):
    storage = _storage(tmp_path)
    duplicate = storage.validate_document(
        {"edges": [_edge("A", "B", "姐姐"), _edge("A", "B", "姐姐")]}, ["A", "B"]
    )
    assert any(error.startswith("relation_edge_duplicate") for error in duplicate)

    # 同一对角色的不同关系类型是合法的（姐妹 + 同门）
    assert storage.validate_document({"edges": [_edge("A", "B", "姐姐"), _edge("A", "B", "同门")]}, ["A", "B"]) == []


def test_validate_rejects_dangling_endpoint(tmp_path: Path):
    errors = _storage(tmp_path).validate_document({"edges": [_edge("A", "查无此人", "师父")]}, ["A"])
    assert "relation_edge_unknown_character:查无此人" in errors


def test_validate_rejects_oversized_labels_and_missing_relation(tmp_path: Path):
    storage = _storage(tmp_path)
    long_label = "长" * (MAX_LABEL_CHARS + 1)
    errors = storage.validate_document(
        {
            "edges": [
                _edge("A", "B", long_label),
                _edge("A", "B", "姐姐", appellation=long_label),
                _edge("A", "B", "同门", reverse=long_label),
                _edge("B", "A", ""),
            ]
        },
        ["A", "B"],
    )
    assert any(error.startswith("relation_label_too_long") for error in errors)
    assert any(error.startswith("relation_appellation_too_long") for error in errors)
    assert any(error.startswith("relation_reverse_appellation_too_long") for error in errors)
    assert any(error.startswith("relation_edge_missing_relation") for error in errors)


def test_validate_rejects_too_many_edges(tmp_path: Path):
    edges = [_edge("A", "B", f"关系{index}") for index in range(MAX_EDGES + 1)]
    errors = _storage(tmp_path).validate_document({"edges": edges}, ["A", "B"])
    assert any(error.startswith("relation_edges_limit_exceeded") for error in errors)


def test_validate_accepts_empty_document(tmp_path: Path):
    assert _storage(tmp_path).validate_document({"edges": [], "layout": {}}, []) == []


# ------------------------------------------------------------- 删除级联 --


def test_delete_character_card_purges_its_edges_only(tmp_path: Path):
    cards = _seed_characters(tmp_path, "林清越", "林清河", "谢无咎")
    relations = _storage(tmp_path)
    asyncio.run(
        relations.save_document(
            "p",
            {
                "edges": [
                    _edge("林清越", "林清河", "姐姐", "阿姐"),
                    _edge("谢无咎", "林清河", "师父"),
                ],
                "layout": {"林清越": {"x": 1, "y": 1}, "谢无咎": {"x": 2, "y": 2}},
            },
            existing_characters=["林清越", "林清河", "谢无咎"],
        )
    )

    assert asyncio.run(cards.delete_character_card("p", "林清越")) is True

    remaining = asyncio.run(relations.load_document("p"))
    assert [edge["from"] for edge in remaining["edges"]] == ["谢无咎"]  # 无关边保留
    assert "林清越" not in remaining["layout"]


def test_purge_character_is_noop_without_relations(tmp_path: Path):
    _seed_characters(tmp_path, "独行者")
    assert asyncio.run(_storage(tmp_path).purge_character("p", "独行者")) is False
    assert not (tmp_path / "p" / "cards" / "relations.yaml").exists()  # 不因删除而创建文件


# ------------------------------------------------------- Agent 侧合并消费 --


class _FakeSelect:
    async def retrieval_select(self, **_kwargs):
        return []


class _Adapter:
    """最小适配器：canon 关系文件 + 卡片层设定边，模拟 UnifiedStorageAdapter 契约。"""

    def __init__(self, relations_path: Path, storage: CharacterRelationStorage):
        self._relations_path = relations_path
        self._storage = storage

    def get_relations_path(self, _project_id):
        return self._relations_path

    async def get_card_relation_edges(self, project_id):
        document = await self._storage.load_document(project_id)
        return list(document.get("edges") or [])


def _write_canon_relations(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_query_relations_merges_card_edges_with_canon(tmp_path: Path):
    storage = _storage(tmp_path)
    asyncio.run(
        storage.save_document(
            "p",
            {"edges": [_edge("林清越", "林清河", "姐姐", "阿姐", reverse="小河")]},
            existing_characters=["林清越", "林清河"],
        )
    )
    canon_path = tmp_path / "p" / "canon" / "relations.jsonl"
    _write_canon_relations(canon_path, [{"subject": "林清越", "relation": "并肩作战", "object": "林清河", "chapter": "V1C003"}])

    toolset = WriterToolset("p", _Adapter(canon_path, storage), _FakeSelect(), current_chapter="V1C010")
    output = asyncio.run(toolset.execute("query_relations", {"entity": "林清越"}))

    # 设定关系（含双向称呼）与 Canon 抽取关系（带出处）同列展示，模型自行区分
    assert "姐姐" in output and "林清河称林清越「阿姐」" in output and "林清越称林清河「小河」" in output
    assert "并肩作战" in output and "@V1C003" in output


def test_query_relations_card_edges_ignore_chapter_window(tmp_path: Path):
    """设定边没有章节出处，不该被『未来章节』过滤掉。"""
    storage = _storage(tmp_path)
    asyncio.run(storage.save_document("p", {"edges": [_edge("A", "B", "宿敌")]}, existing_characters=["A", "B"]))
    canon_path = tmp_path / "p" / "canon" / "relations.jsonl"
    _write_canon_relations(canon_path, [{"subject": "A", "relation": "结盟", "object": "B", "chapter": "V1C099"}])

    toolset = WriterToolset("p", _Adapter(canon_path, storage), _FakeSelect(), current_chapter="V1C001")
    output = asyncio.run(toolset.execute("query_relations", {"entity": "A", "other": "B"}))

    assert "宿敌" in output  # 设定边保留
    assert "结盟" not in output  # 未来章节的 Canon 关系仍被挡住


def test_query_relations_without_card_edges_matches_previous_behavior(tmp_path: Path):
    canon_path = tmp_path / "p" / "canon" / "relations.jsonl"
    _write_canon_relations(canon_path, [{"subject": "张三", "relation": "敌对", "object": "李四", "chapter": "V1C002"}])

    class _CanonOnlyAdapter:
        def get_relations_path(self, _project_id):
            return canon_path

    output = asyncio.run(
        WriterToolset("p", _CanonOnlyAdapter(), _FakeSelect()).execute("query_relations", {"entity": "张三"})
    )
    assert "敌对" in output and "称" not in output


def test_card_edges_never_touch_canon_inconsistency_report(tmp_path: Path):
    """一致性护栏只看 Canon 层：作者设定与正文抽取表述不一致不应触发报警。"""
    canon_path = tmp_path / "p" / "canon" / "relations.jsonl"
    _write_canon_relations(canon_path, [{"subject": "A", "relation": "朋友", "object": "B", "chapter": "V1C002"}])

    canon_graph = RelationGraph.load(canon_path)
    assert canon_graph.inconsistencies() == []

    # 设定边（姐妹）与 Canon（朋友）合并后仅用于工具展示，不写回 canon 文件
    merged = RelationGraph(canon_graph.relations + [Relation.from_card_edge(_edge("A", "B", "姐妹", "阿姐"))])
    assert len(merged.relations) == 2
    assert canon_path.read_text(encoding="utf-8").count("\n") == 1


# -------------------------------------------- 供给主路径：默认注入稳定前缀 --


class _EdgeAdapter:
    """只提供设定边的最小适配器（关系推送不读 canon、不做检索）。"""

    def __init__(self, edges: list):
        self._edges = edges

    async def get_card_relation_edges(self, _project_id):
        return list(self._edges)


def _writing_service(adapter):
    from app.orchestrator.context_assembly_service import ContextAssemblyService
    from app.orchestrator.writing_service import WritingService

    return WritingService(
        gateway=None,
        writer=None,
        draft_storage=None,
        storage_adapter=adapter,
        select_engine=None,
        context_assembly=ContextAssemblyService(),
    )


def test_relations_push_renders_edges_with_appellations():
    service = _writing_service(_EdgeAdapter([_edge("林清越", "林清河", "姐姐", "阿姐", reverse="小河")]))
    push = asyncio.run(service._resolve_relations_push("p"))
    assert push == "- 林清越 —[姐姐]→ 林清河（林清河称林清越「阿姐」；林清越称林清河「小河」）"


def test_relations_push_is_empty_without_edges_or_storage():
    assert asyncio.run(_writing_service(_EdgeAdapter([]))._resolve_relations_push("p")) == ""

    class _NoRelations:
        pass

    assert asyncio.run(_writing_service(_NoRelations())._resolve_relations_push("p")) == ""


def test_relations_push_marks_overflow_instead_of_silent_truncation():
    from app.config import config

    limit = int((config.get("retrieval", {}).get("relations", {}) or {}).get("max_push_edges") or 80)
    edges = [_edge(f"A{index}", f"B{index}", "同门") for index in range(limit + 5)]
    push = asyncio.run(_writing_service(_EdgeAdapter(edges))._resolve_relations_push("p"))

    lines = push.splitlines()
    assert len(lines) == limit + 1  # limit 条关系 + 1 行溢出说明
    assert "另有 5 条设定关系未在此列出" in lines[-1] and "query_relations" in lines[-1]


def test_writer_system_prompt_carries_relations_block_only_when_present():
    from app.orchestrator.context_assembly_service import ContextAssemblyService

    assembly = ContextAssemblyService()
    push = "- 林清越 —[姐姐]→ 林清河（林清河称林清越「阿姐」）"
    request = assembly.assemble_writer_request(
        message="写第三章",
        chapter="V1C003",
        current_text="",
        has_selection=False,
        target_word_count=1000,
        relations_push=push,
    )
    system = request.messages[0]["content"]
    assert "人物关系与称呼（作者设定" in system and push in system
    assert "card" in request.supply_report.pushed  # 供给可观测：卡片层设定已推送

    without = assembly.assemble_writer_request(
        message="写第三章",
        chapter="V1C003",
        current_text="",
        has_selection=False,
        target_word_count=1000,
    )
    assert "人物关系与称呼" not in without.messages[0]["content"]
    assert "card" not in without.supply_report.pushed


def test_relation_from_card_edge_direction_semantics():
    relation = Relation.from_card_edge(_edge("林清越", "林清河", "姐姐", "阿姐", reverse="小河"))
    # from 是 to 的 relation；appellation 是 to 对 from 的称呼，reverse 是 from 对 to 的称呼
    assert relation.subject == "林清越" and relation.object == "林清河"
    assert relation.chapter == "" and relation.change == ""
    assert relation.text() == "林清越 —[姐姐]→ 林清河（林清河称林清越「阿姐」；林清越称林清河「小河」）"


def test_relation_text_renders_each_appellation_independently():
    """两个称呼各自独立：只填一侧时不臆造另一侧。"""
    only_forward = Relation.from_card_edge(_edge("A", "B", "师父", "师父大人"))
    assert only_forward.text() == "A —[师父]→ B（B称A「师父大人」）"

    only_reverse = Relation.from_card_edge(_edge("A", "B", "师父", reverse="徒儿"))
    assert only_reverse.text() == "A —[师父]→ B（A称B「徒儿」）"

    neither = Relation.from_card_edge(_edge("A", "B", "同门"))
    assert neither.text() == "A —[同门]→ B"


# ------------------------------------------------------------------ 端点 --


@pytest.fixture
def relations_client(tmp_path: Path, monkeypatch):
    """把卡片路由的存储单例指到临时目录，避免测试写入真实 data 目录。"""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.routers import cards as cards_router

    _seed_characters(tmp_path, "林清越", "林清河")
    monkeypatch.setattr(cards_router, "card_storage", CardStorage(str(tmp_path)))
    monkeypatch.setattr(cards_router, "relation_storage", _storage(tmp_path))

    async def _client():
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _client


async def test_relations_endpoint_roundtrip(relations_client):
    async with await relations_client() as client:
        empty = await client.get("/projects/p/cards/relations")
        assert empty.status_code == 200
        assert empty.json() == {"edges": [], "layout": {}}

        saved = await client.put(
            "/projects/p/cards/relations",
            json={
                "edges": [
                    {
                        "from": "林清越",
                        "to": "林清河",
                        "relation": "姐姐",
                        "appellation": "阿姐",
                        "reverse_appellation": "小河",
                    }
                ],
                "layout": {"林清越": {"x": 120, "y": 80}},
            },
        )
        assert saved.status_code == 200
        edge = saved.json()["edges"][0]
        assert edge["from"] == "林清越" and edge["to"] == "林清河" and edge["id"]  # 服务端补齐 id
        assert edge["appellation"] == "阿姐" and edge["reverse_appellation"] == "小河"

        reloaded = await client.get("/projects/p/cards/relations")
        assert reloaded.json() == saved.json()


async def test_relations_endpoint_rejects_invalid_document(relations_client):
    async with await relations_client() as client:
        response = await client.put(
            "/projects/p/cards/relations",
            json={"edges": [{"from": "林清越", "to": "查无此人", "relation": "师父"}]},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "relation_document_invalid"
        assert "relation_edge_unknown_character:查无此人" in detail["errors"]

        # 整体拒绝：不落盘、不半写
        assert (await client.get("/projects/p/cards/relations")).json()["edges"] == []
