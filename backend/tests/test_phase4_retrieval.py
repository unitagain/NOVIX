# -*- coding: utf-8 -*-
"""
Phase 4 验收（4a 基建）：嵌入相似度、轻量向量库、关系图查询。
纯本地、无网络、无模型依赖（用手工向量与内存关系）。

Phase 4 验收（4b 集成）：select_engine 混合检索 —— 注入式 FakeEmbedder 验证
语义召回能救回"字面零重叠但语义相关"的事实，且默认（无嵌入后端）行为不变。
"""

import asyncio
import json
from types import SimpleNamespace

from app.context_engine import embeddings as embeddings_module
from app.context_engine.embeddings import cosine_similarity, create_embeddings_backend
from app.context_engine.vector_store import VectorStore
from app.context_engine.relation_graph import Relation, RelationGraph
from app.context_engine.reranker import create_reranker_backend
from app.context_engine.select_engine import ContextSelectEngine
from app.schemas.canon import Fact

# ----------------------------------------------------------- cosine --------


def test_cosine_basics():
    assert cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 1.0
    assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0  # 维度不符


# ------------------------------------------------------- vector store ------


def test_vector_store_search_and_persist(tmp_path):
    vs = VectorStore()
    vs.upsert("a", [1.0, 0.0, 0.0], text="alpha")
    vs.upsert("b", [0.0, 1.0, 0.0], text="beta")
    vs.upsert("c", [0.9, 0.1, 0.0], text="near-alpha")

    res = vs.search([1.0, 0.0, 0.0], top_k=2)
    ids = [r[0] for r in res]
    assert ids[0] == "a" and "c" in ids and "b" not in ids

    p = tmp_path / "vec.jsonl"
    vs.save(p)
    vs2 = VectorStore.load(p)
    assert len(vs2) == 3 and vs2.has("b")
    assert vs2.search([0.0, 1.0, 0.0], top_k=1)[0][0] == "b"


def test_vector_store_empty():
    vs = VectorStore()
    assert vs.search([], top_k=3) == []


def test_vector_store_load_missing(tmp_path):
    vs = VectorStore.load(tmp_path / "nope.jsonl")
    assert len(vs) == 0


# ------------------------------------------------------- relation graph ----


def test_relation_graph_queries():
    rels = [
        Relation("张三", "盟友", "李四", chapter="V1C002"),
        Relation("张三", "敌对", "李四", change="盟友→敌对", chapter="V3C005"),
        Relation("张三", "师承", "王五", chapter="V1C001"),
    ]
    g = RelationGraph(rels)
    assert len(g.neighbors("张三")) == 3
    assert len(g.between("李四", "张三")) == 2  # 无向匹配两条 张三-李四 边
    desc = g.describe("张三", "李四")
    assert "敌对" in desc and "盟友" in desc
    assert "师承" in g.describe("王五")
    assert "未在关系图" in g.describe("不存在的人")


def test_relation_graph_load_and_skip_invalid(tmp_path):
    p = tmp_path / "relations.jsonl"
    p.write_text(
        json.dumps({"subject": "甲", "relation": "背叛", "object": "乙", "chapter": "V2C010"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"subject": "", "relation": "x", "object": "y"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    g = RelationGraph.load(p)
    assert len(g.relations) == 1  # 空 subject 的无效行被跳过
    assert "背叛" in g.describe("甲", "乙")


def test_relation_graph_load_missing(tmp_path):
    g = RelationGraph.load(tmp_path / "nope.jsonl")
    assert g.relations == []


# ----------------------------------- relation consistency guardrail (P6) ---


def test_relation_inconsistencies_flags_silent_change():
    """同一对实体出现两种关系类型且无 change 标注 → 报警。"""
    g = RelationGraph(
        [
            Relation("张三", "盟友", "李四", chapter="V1C002"),
            Relation("张三", "敌对", "李四", chapter="V3C005"),  # 无 change → 悄悄改写
        ]
    )
    issues = g.inconsistencies()
    assert len(issues) == 1
    assert sorted(issues[0]["entities"]) == ["张三", "李四"]


def test_relation_inconsistencies_ignores_marked_evolution():
    """带 change 演变标注（盟友→敌对）视为有意为之，不报警。"""
    g = RelationGraph(
        [
            Relation("张三", "盟友", "李四", chapter="V1C002"),
            Relation("张三", "敌对", "李四", change="盟友→敌对", chapter="V3C005"),
        ]
    )
    assert g.inconsistencies() == []


def test_relation_inconsistencies_consistent_pair():
    """同一关系类型重复出现 → 一致，不报警。"""
    g = RelationGraph(
        [
            Relation("甲", "师承", "乙", chapter="V1C001"),
            Relation("甲", "师承", "乙", chapter="V2C003"),
        ]
    )
    assert g.inconsistencies() == []


def test_canon_detect_relation_inconsistencies_roundtrip(tmp_path):
    """落库 relations.jsonl → CanonStorage 检测出可读报警字符串。"""
    from app.storage.canon import CanonStorage

    storage = CanonStorage(data_dir=str(tmp_path))
    pid = "p"
    asyncio.run(
        storage.add_relations(
            pid,
            [
                {"subject": "张三", "relation": "盟友", "object": "李四", "chapter": "V1C002"},
                {"subject": "张三", "relation": "敌对", "object": "李四", "chapter": "V3C005"},
            ],
        )
    )
    msgs = asyncio.run(storage.detect_relation_inconsistencies(pid))
    assert len(msgs) == 1 and "Relation Conflict" in msgs[0]
    assert "张三" in msgs[0] and "李四" in msgs[0]


# ----------------------------------------------- hybrid retrieval (4b) -----


class _FakeFactStorage:
    """只实现 retrieval_select(item_types=['fact']) 所需的 get_all_facts。"""

    def __init__(self, facts):
        self._facts = facts

    async def get_all_facts(self, project_id):
        return list(self._facts)


class _MixedStorage(_FakeFactStorage):
    def __init__(self, facts=None, characters=None, worlds=None, chunks=None):
        super().__init__(facts or [])
        self._characters = characters or {}
        self._worlds = worlds or {}
        self._chunks = chunks or []

    async def list_character_cards(self, project_id):
        return list(self._characters)

    async def get_character_card(self, project_id, name):
        return self._characters.get(name)

    async def list_world_cards(self, project_id):
        return list(self._worlds)

    async def get_world_card(self, project_id, name):
        return self._worlds.get(name)

    async def search_text_chunks(self, project_id, query, limit=50):
        return list(self._chunks)[:limit]


class _KeywordEmbedder:
    """确定性嵌入：按关键词把文本投到 3 维语义空间（恐惧/中性/天气）；无网络、无模型。"""

    @staticmethod
    def _vec(text):
        t = str(text)
        if any(k in t for k in ("恐惧", "拳头", "不敢", "害怕")):
            return [1.0, 0.0, 0.0]
        if any(k in t for k in ("天气", "阳光", "晴")):
            return [0.0, 0.0, 1.0]
        return [0.0, 1.0, 0.0]

    async def embed(self, texts):
        return [self._vec(t) for t in texts]


def _fear_facts():
    return [
        Fact(id="F1", statement="他攥紧拳头不敢回头", source="V1C001", introduced_in="V1C001"),
        Fact(id="F2", statement="今天天气很好阳光明媚", source="V1C001", introduced_in="V1C001"),
        Fact(id="F3", statement="主角的恐惧来源于童年", source="V1C001", introduced_in="V1C001"),
    ]


def test_lexical_only_drops_zero_overlap_fact():
    """纯词法（无嵌入后端，默认）：与『恐惧』字面零重叠的 F1 被丢弃 —— 行为与历史一致。"""
    engine = ContextSelectEngine()  # embeddings_service=None
    storage = _FakeFactStorage(_fear_facts())
    results = asyncio.run(
        engine.retrieval_select(project_id="p", query="恐惧", item_types=["fact"], storage=storage, top_k=5)
    )
    ids = [r.id for r in results]
    assert "F3" in ids  # 含『恐惧』，词法命中
    assert "F1" not in ids  # 字面零重叠，词法模式丢弃
    assert "F2" not in ids


def test_retrieval_excludes_future_canon_when_current_chapter_is_known():
    facts = [
        Fact(id="past", statement="铜钥匙此前一直由管家保管", source="V1C002", introduced_in="V1C002"),
        Fact(id="current", statement="铜钥匙当前留在仓库门边", source="V1C003", introduced_in="V1C003"),
        Fact(id="future", statement="铜钥匙后来被访客带走", source="V1C004", introduced_in="V1C004"),
    ]
    engine = ContextSelectEngine()

    results = asyncio.run(
        engine.retrieval_select(
            project_id="p",
            query="铜钥匙",
            item_types=["fact"],
            storage=_FakeFactStorage(facts),
            top_k=5,
            current_chapter="V1C003",
        )
    )
    trace = engine.get_last_ranking_trace()

    assert {row.id for row in results} == {"past", "current"}
    assert trace["signals"]["temporal_scope"] is True
    assert trace["filters"]["future_facts_excluded"] == 1


def test_retrieval_excludes_future_prose_chunks_when_current_chapter_is_known():
    storage = _MixedStorage(
        chunks=[
            {"chapter": "V1C002", "text": "林舟在旧仓库发现一条钥匙线索。"},
            {"chapter": "V1C004", "text": "林舟后来确认钥匙已经被人带走。"},
        ]
    )
    engine = ContextSelectEngine()

    results = asyncio.run(
        engine.retrieval_select(
            project_id="p",
            query="钥匙线索",
            item_types=["text_chunk"],
            storage=storage,
            top_k=5,
            current_chapter="V1C003",
        )
    )
    trace = engine.get_last_ranking_trace()

    assert [row.metadata["chapter"] for row in results] == ["V1C002"]
    assert trace["filters"]["future_text_chunks_excluded"] == 1


def test_semantic_rescues_zero_overlap_fact():
    """语义模式：注入嵌入后端后，F1（语义近『恐惧』但字面零重叠）被召回且靠前。"""
    engine = ContextSelectEngine(embeddings_service=_KeywordEmbedder())
    storage = _FakeFactStorage(_fear_facts())
    results = asyncio.run(
        engine.retrieval_select(project_id="p", query="恐惧", item_types=["fact"], storage=storage, top_k=5)
    )
    ids = [r.id for r in results]
    assert "F1" in ids  # 语义召回救回字面零重叠条目（embeddings 不再是死参数）
    assert ids[0] in ("F1", "F3")  # 语义相关的排在最前
    assert ids[-1] == "F2"  # 天气类（语义最远）垫底


def test_semantic_failure_falls_back_to_lexical():
    """嵌入后端抛错时，检索自动降级为纯词法，不中断、不抛出。"""

    class _BrokenEmbedder:
        async def embed(self, texts):
            raise RuntimeError("boom")

    engine = ContextSelectEngine(embeddings_service=_BrokenEmbedder())
    storage = _FakeFactStorage(_fear_facts())
    results = asyncio.run(
        engine.retrieval_select(project_id="p", query="恐惧", item_types=["fact"], storage=storage, top_k=5)
    )
    ids = [r.id for r in results]
    assert ids == ["F3"]  # 降级为词法：仅字面命中的 F3


# ----------------------------------------- embed-once vector cache (4b) ----


class _CountingEmbedder:
    """记录每次 embed() 的批大小，用于验证 embed-once 缓存。"""

    def __init__(self):
        self.batches = []

    @staticmethod
    def _vec(text):
        t = str(text)
        if any(k in t for k in ("恐惧", "拳头", "不敢")):
            return [1.0, 0.0, 0.0]
        if any(k in t for k in ("天气", "阳光")):
            return [0.0, 0.0, 1.0]
        return [0.0, 1.0, 0.0]

    async def embed(self, texts):
        texts = list(texts)
        self.batches.append(len(texts))
        return [self._vec(t) for t in texts]


class _FactStorageWithCache(_FakeFactStorage):
    def __init__(self, facts, cache_path):
        super().__init__(facts)
        self._cache_path = cache_path

    def get_embeddings_cache_path(self, project_id):
        return self._cache_path


def test_embed_once_cache_reuses_vectors(tmp_path):
    """候选向量只嵌入一次并落盘：二次查询只嵌入 query；新实例从磁盘复用。"""
    cache = tmp_path / "embeddings_cache.jsonl"
    emb = _CountingEmbedder()
    engine = ContextSelectEngine(embeddings_service=emb)
    storage = _FactStorageWithCache(_fear_facts(), cache)

    def _run(e):
        return asyncio.run(
            e.retrieval_select(project_id="p", query="恐惧", item_types=["fact"], storage=storage, top_k=5)
        )

    _run(engine)
    assert emb.batches[-1] == 4  # query + 3 个未缓存事实
    _run(engine)
    assert emb.batches[-1] == 1  # 候选命中缓存，仅嵌入 query
    assert cache.exists()  # 向量已落盘

    # 跨实例：新引擎从磁盘加载缓存，同样只嵌入 query
    emb2 = _CountingEmbedder()
    engine2 = ContextSelectEngine(embeddings_service=emb2)
    _run(engine2)
    assert emb2.batches[-1] == 1


# -------------------------------------------- relations production (4b) ----


def test_relations_production_roundtrip(tmp_path):
    """生产侧：从角色状态派生关系边 → 落 relations.jsonl → 经关系图读回 → 覆盖删除。"""
    from app.storage.canon import CanonStorage
    from app.schemas.canon import CharacterState

    storage = CanonStorage(data_dir=str(tmp_path))
    pid = "proj1"
    states = [
        CharacterState(character="张三", relationships={"李四": "敌对", "王五": "师承"}, last_seen="V3C005"),
        CharacterState(character="张三", relationships={"张三": "自指应被跳过"}, last_seen="V3C005"),
    ]
    derived = storage.derive_relations_from_states(states, "V3C005")
    assert len(derived) == 2  # 自指关系被跳过

    written = asyncio.run(storage.add_relations(pid, derived))
    assert written == 2

    path = storage.get_project_path(pid) / "canon" / "relations.jsonl"
    graph = RelationGraph.load(path)
    assert "敌对" in graph.describe("张三", "李四")  # 连点成线 + 出处
    assert "师承" in graph.describe("张三", "王五")
    assert "V3C005" in graph.describe("张三", "李四")

    deleted = asyncio.run(storage.delete_relations_by_chapter(pid, "V3C005"))
    assert deleted == 2
    assert asyncio.run(storage.get_all_relations(pid)) == []


def test_add_relations_skips_malformed():
    """缺主体/客体/关系的三元组被跳过，不写入。"""
    from app.storage.canon import CanonStorage
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        storage = CanonStorage(data_dir=d)
        n = asyncio.run(
            storage.add_relations(
                "p",
                [
                    {"subject": "甲", "relation": "", "object": "乙"},  # 缺关系
                    {"subject": "", "relation": "x", "object": "乙"},  # 缺主体
                    {"subject": "甲", "relation": "盟友", "object": "乙", "chapter": "V1C001"},  # 有效
                ],
            )
        )
        assert n == 1


# ----------------------------------------- contextual prefix (4b) ----------


def test_context_prefix_used_for_lexical_index():
    """情境前缀参与检索索引：字面零重叠但前缀命中的事实被召回；展示仍为原始 statement。"""
    facts = [
        Fact(
            id="P1",
            statement="他攥紧拳头不敢回头",
            source="V1C001",
            introduced_in="V1C001",
            context_prefix="迷雾森林 张三对峙",
        ),
        Fact(id="P2", statement="今天天气晴朗无云", source="V1C001", introduced_in="V1C001"),
    ]
    engine = ContextSelectEngine()  # 纯词法
    storage = _FakeFactStorage(facts)
    results = asyncio.run(
        engine.retrieval_select(project_id="p", query="迷雾森林", item_types=["fact"], storage=storage, top_k=5)
    )
    ids = [r.id for r in results]
    assert ids == ["P1"]  # 前缀含『迷雾森林』→ 词法命中；无前缀的 P2 被丢弃
    assert results[0].content == "他攥紧拳头不敢回头"  # 展示仍是原始 statement（不含前缀）
    assert "_index_text" not in results[0].metadata  # 内部索引文本不外泄


def test_card_name_used_as_contextual_index_prefix():
    """角色卡名称进入索引前缀：正文无查询词时仍可按角色名召回。"""
    storage = _MixedStorage(
        characters={
            "林舟": {"appearance": "总穿灰色长衣", "personality": "沉默克制"},
            "沈桥": {"appearance": "红衣", "personality": "张扬"},
        }
    )
    engine = ContextSelectEngine()
    results = asyncio.run(
        engine.retrieval_select(project_id="p", query="林舟", item_types=["character"], storage=storage, top_k=5)
    )
    assert [r.id for r in results] == ["char_林舟"]
    assert "_index_text" not in results[0].metadata


def test_text_chunk_chapter_used_as_contextual_index_prefix():
    """正文片段章节进入索引前缀：可按章节号召回具体片段。"""
    storage = _MixedStorage(
        chunks=[
            {"chapter": "V1C009", "text": "他在雨夜推开旧宅木门。"},
            {"chapter": "V1C002", "text": "清晨的集市人声鼎沸。"},
        ]
    )
    engine = ContextSelectEngine()
    results = asyncio.run(
        engine.retrieval_select(project_id="p", query="V1C009", item_types=["text_chunk"], storage=storage, top_k=5)
    )
    assert results[0].metadata.get("chapter") == "V1C009"
    assert results[0].relevance_score > results[-1].relevance_score


def test_ranking_trace_records_signals_and_top_results():
    facts = [
        Fact(id="R1", statement="张三是李四的师父", source="V1C001", introduced_in="V1C001"),
        Fact(id="R2", statement="迷雾森林夜晚会迷路", source="V1C002", introduced_in="V1C002"),
    ]
    engine = ContextSelectEngine()
    storage = _FakeFactStorage(facts)
    results = asyncio.run(
        engine.retrieval_select(
            project_id="p",
            query="张三 李四",
            item_types=["fact"],
            storage=storage,
            top_k=2,
            current_chapter="V1C003",
        )
    )
    assert results[0].id == "R1"
    trace = engine.get_last_ranking_trace()
    assert trace["fusion"] == "lexical"
    assert trace["signals"]["bm25"] is True
    assert trace["signals"]["chapter_distance"] is True
    assert trace["top_results"][0]["id"] == "R1"


def test_retrieval_policy_overrides_are_explicit_and_validated():
    engine = ContextSelectEngine(
        embeddings_service=_KeywordEmbedder(),
        fusion="weighted",
        semantic_rerank=False,
        rerank_top_k=7,
    )

    assert engine.get_retrieval_policy() == {
        "semantic_enabled": True,
        "fusion": "weighted",
        "semantic_rerank": False,
        "reranker_available": False,
        "reranker_backend": None,
        "rerank_top_k": 7,
    }

    try:
        ContextSelectEngine(fusion="not-a-fusion")
    except ValueError as exc:
        assert "unsupported retrieval fusion" in str(exc)
    else:
        raise AssertionError("invalid fusion must be rejected")


def test_embedding_cache_dir_resolves_against_writable_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings_module, "get_settings", lambda: SimpleNamespace(data_dir=str(tmp_path)))
    backend = create_embeddings_backend(
        {
            "retrieval": {
                "embeddings": {
                    "enabled": True,
                    "backend": "onnx",
                    "model": "demo-model",
                    "cache_dir": "models",
                }
            }
        }
    )

    assert backend is not None
    assert backend.cache_dir == str((tmp_path / "models").resolve())


def test_cross_encoder_reranker_is_explicit_and_uses_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings_module, "get_settings", lambda: SimpleNamespace(data_dir=str(tmp_path)))
    assert create_reranker_backend({"retrieval": {"reranker": {"enabled": False}}}) is None

    backend = create_reranker_backend(
        {
            "retrieval": {
                "reranker": {
                    "enabled": True,
                    "backend": "onnx_cross_encoder",
                    "model": "demo-reranker",
                    "cache_dir": "models/reranker",
                }
            }
        }
    )

    assert backend is not None
    assert backend.model_name == "demo-reranker"
    assert backend.cache_dir == str((tmp_path / "models" / "reranker").resolve())


def test_canon_parser_extracts_relations_and_context(tmp_path):
    """档案员解析：facts 带回 context_prefix；relations 三元组被规范化、空主体被跳过。"""
    from app.agents.archivist import ArchivistAgent
    from app.storage.canon import CanonStorage

    canon = CanonStorage(data_dir=str(tmp_path))
    agent = ArchivistAgent(gateway=None, card_storage=None, canon_storage=canon, draft_storage=None, language="zh")
    yaml_content = "\n".join(
        [
            "facts:",
            "  - statement: 张三在迷雾森林揭穿了李四的真实身份",
            "    confidence: 0.9",
            "    context: 迷雾森林·对峙",
            "timeline_events: []",
            "character_states: []",
            "relations:",
            "  - subject: 张三",
            "    relation: 敌对",
            "    object: 李四",
            "    change: 盟友→敌对",
            "  - subject: ''",
            "    relation: x",
            "    object: y",
        ]
    )
    result = asyncio.run(agent._parse_canon_updates_yaml(project_id="p", chapter="V3C005", yaml_content=yaml_content))
    facts = result["facts"]
    assert len(facts) == 1 and facts[0].context_prefix == "迷雾森林·对峙"
    rels = result["relations"]
    assert len(rels) == 1  # 空 subject 的三元组被跳过
    assert rels[0]["subject"] == "张三" and rels[0]["relation"] == "敌对"
    assert rels[0]["object"] == "李四" and rels[0]["chapter"] == "V3C005"
