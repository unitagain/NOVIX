"""Character relation storage —— 角色关系图谱（U4，作者设定层）。

设计要点（plan.md §8.2）：
- 落 ``cards/relations.yaml``，与角色卡同层，**不进** ``canon/relations.jsonl``：
  Canon relations 是档案员从正文抽取的故事事实（带章节出处、走审核状态机），
  本资产是作者手绘的创作设定。两个写入者共用一个 store 会互相覆盖。
- 单文件两个区段：``edges``（Agent 消费的设定真相）+ ``layout``（画布坐标，纯视图状态）。
  整文档原子读写——画布天然持有全图状态，增量协议无收益（KISS）；layout 丢失无害。
- 方向语义定死：``edge(from=A, to=B, relation=R)`` 读作「**A 是 B 的 R**」；
  ``appellation`` 是「**B 对 A 的称呼**」，``reverse_appellation`` 是「**A 对 B 的称呼**」。
  两个称呼各自独立填写，不做任何自动互推（中文称谓有性别/长幼歧义）。
  前端文案与本模块校验必须表达同一方向。
- 本模块是 ``cards/relations.yaml`` 的唯一 owner，也是关系边全部业务规则的唯一 owner：
  路由只负责结构解析与错误映射，不复制校验规则。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

import yaml

from app.storage.base import BaseStorage
from app.utils.logger import get_logger

logger = get_logger(__name__)

__all__ = ["CharacterRelationStorage", "MAX_EDGES", "MAX_LABEL_CHARS"]

# 单部小说的设定关系规模上限；超出属于百科式协作，不在本产品定位内。
MAX_EDGES = 500
MAX_LABEL_CHARS = 20

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean_text(value: Any) -> str:
    """归一化用户输入：去控制字符 + 去首尾空白。"""
    return _CONTROL_CHARS.sub("", str(value or "")).strip()


def _coord(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    # 画布坐标只是视图状态；非有限值按原点处理，避免把 NaN 写进 YAML。
    return number if number == number and abs(number) != float("inf") else 0.0


class CharacterRelationStorage(BaseStorage):
    """``cards/relations.yaml`` 唯一读写 owner（角色关系设定边 + 画布布局）。"""

    def _relations_path(self, project_id: str) -> Path:
        return self.get_project_path(project_id) / "cards" / "relations.yaml"

    # ------------------------------------------------------------------ 读 --

    async def load_document(self, project_id: str) -> Dict[str, Any]:
        """读取关系文档；文件缺失或损坏时返回空文档。

        关系图是作者可重建的设定视图，读失败不应阻断写作主链路（Agent 侧退化为
        「没有设定关系」，Canon 抽取关系不受影响）。
        """
        path = self._relations_path(project_id)
        if not path.is_file():
            return {"edges": [], "layout": {}}
        try:
            data = await self.read_yaml(path)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            logger.warning("关系文档读取失败 / failed to read relations (%s): %s", path, exc)
            return {"edges": [], "layout": {}}
        return self._coerce_document(data)

    # ------------------------------------------------------------------ 写 --

    async def save_document(
        self,
        project_id: str,
        document: Any,
        *,
        existing_characters: Iterable[str] = (),
    ) -> Dict[str, Any]:
        """整文档覆盖写（唯一写入路径），返回实际落盘文档。

        写入前统一归一化：补齐缺省 id、裁剪标签、剪除已不存在角色的布局条目。
        调用方应先跑 :meth:`validate_document` 以获得可回传的错误列表。
        """
        normalized = self._normalize_document(document, existing_characters)
        await self.write_yaml(self._relations_path(project_id), normalized)
        return normalized

    async def purge_character(self, project_id: str, character_name: str) -> bool:
        """删除某角色的全部关系边与布局条目；返回是否发生变更。

        与角色卡删除级联调用：已删除的实体不得继续出现在画布或 Agent 查询里。
        """
        target = _clean_text(character_name)
        if not target:
            return False
        document = await self.load_document(project_id)
        edges = [
            edge for edge in document["edges"] if edge.get("from") != target and edge.get("to") != target
        ]
        layout = {name: pos for name, pos in document["layout"].items() if name != target}
        if len(edges) == len(document["edges"]) and len(layout) == len(document["layout"]):
            return False
        await self.write_yaml(self._relations_path(project_id), {"edges": edges, "layout": layout})
        return True

    # ------------------------------------------------------------------ 校验 --

    def validate_document(self, document: Any, existing_characters: Iterable[str]) -> List[str]:
        """校验整份文档，返回稳定错误码列表（空列表表示通过）。

        整体校验、整体拒绝：任一条边不合法则整份文档不落盘，避免半写状态。
        错误码只含结构化定位信息，不回传原始异常文本。
        """
        known = {_clean_text(name) for name in existing_characters or ()}
        errors: List[str] = []

        edges = document.get("edges") if isinstance(document, dict) else None
        if edges is None:
            edges = []
        if not isinstance(edges, list):
            return ["relation_edges_invalid_type"]
        if len(edges) > MAX_EDGES:
            errors.append(f"relation_edges_limit_exceeded:{len(edges)}>{MAX_EDGES}")

        seen: Set[Tuple[str, str, str]] = set()
        for index, raw in enumerate(edges):
            if not isinstance(raw, dict):
                errors.append(f"relation_edge_invalid_type:{index}")
                continue
            source, target, relation, appellation, reverse = self._edge_fields(raw)
            if not source or not target:
                errors.append(f"relation_edge_missing_endpoint:{index}")
                continue
            if source == target:
                # 自环：对齐 plan.md §4「关系自环必须为 0」。
                errors.append(f"relation_edge_self_loop:{source}")
                continue
            for endpoint in (source, target):
                if endpoint not in known:
                    errors.append(f"relation_edge_unknown_character:{endpoint}")
            if not relation:
                errors.append(f"relation_edge_missing_relation:{source}->{target}")
            elif len(relation) > MAX_LABEL_CHARS:
                errors.append(f"relation_label_too_long:{source}->{target}")
            if len(appellation) > MAX_LABEL_CHARS:
                errors.append(f"relation_appellation_too_long:{source}->{target}")
            if len(reverse) > MAX_LABEL_CHARS:
                errors.append(f"relation_reverse_appellation_too_long:{source}->{target}")
            key = (source, target, relation)
            if key in seen:
                # 同一对角色允许多种关系类型（姐妹 + 同门），但同一三元组不重复。
                errors.append(f"relation_edge_duplicate:{source}->{target}:{relation}")
            seen.add(key)

        layout = document.get("layout") if isinstance(document, dict) else None
        if layout is not None and not isinstance(layout, dict):
            errors.append("relation_layout_invalid_type")
        return errors

    # -------------------------------------------------------------- 内部实现 --

    @staticmethod
    def _edge_fields(raw: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
        """返回 (from, to, relation, appellation, reverse_appellation)。

        ``appellation`` 是 to 对 from 的称呼，``reverse_appellation`` 是 from 对 to 的称呼。
        """
        return (
            _clean_text(raw.get("from")),
            _clean_text(raw.get("to")),
            _clean_text(raw.get("relation")),
            _clean_text(raw.get("appellation")),
            _clean_text(raw.get("reverse_appellation")),
        )

    def _coerce_document(self, data: Any) -> Dict[str, Any]:
        """把磁盘上的任意 YAML 内容收敛为 ``{edges, layout}`` 结构。"""
        if not isinstance(data, dict):
            return {"edges": [], "layout": {}}
        edges: List[Dict[str, Any]] = []
        for raw in data.get("edges") or []:
            if not isinstance(raw, dict):
                continue
            source, target, relation, appellation, reverse = self._edge_fields(raw)
            if not source or not target or not relation:
                continue
            edge: Dict[str, Any] = {
                "id": _clean_text(raw.get("id")),
                "from": source,
                "to": target,
                "relation": relation,
            }
            if appellation:
                edge["appellation"] = appellation
            if reverse:
                edge["reverse_appellation"] = reverse
            edges.append(edge)
        layout: Dict[str, Dict[str, float]] = {}
        raw_layout = data.get("layout")
        if isinstance(raw_layout, dict):
            for name, position in raw_layout.items():
                cleaned = _clean_text(name)
                if cleaned and isinstance(position, dict):
                    layout[cleaned] = {"x": _coord(position.get("x")), "y": _coord(position.get("y"))}
        return {"edges": edges, "layout": layout}

    def _normalize_document(self, document: Any, existing_characters: Iterable[str]) -> Dict[str, Any]:
        """落盘前的确定性归一化：补 id、裁剪标签、剪除悬空布局条目。"""
        known = {_clean_text(name) for name in existing_characters or ()}
        source_doc = self._coerce_document(document if isinstance(document, dict) else {})

        used_ids: Set[str] = set()
        edges: List[Dict[str, Any]] = []
        for raw in source_doc["edges"]:
            source, target, relation, appellation, reverse = self._edge_fields(raw)
            if source == target:
                continue  # 已由 validate_document 拦截；此处防御性丢弃，绝不落盘自环
            edge_id = self._unique_id(_clean_text(raw.get("id")), used_ids)
            edge: Dict[str, Any] = {
                "id": edge_id,
                "from": source,
                "to": target,
                "relation": relation[:MAX_LABEL_CHARS],
            }
            if appellation:
                edge["appellation"] = appellation[:MAX_LABEL_CHARS]
            if reverse:
                edge["reverse_appellation"] = reverse[:MAX_LABEL_CHARS]
            edges.append(edge)

        layout = {
            name: position
            for name, position in source_doc["layout"].items()
            # 布局是纯视图状态：角色已不存在时静默剪除，不作为错误上报；
            # 未提供角色名单（例如内部维护调用）时不剪除，避免误删有效坐标。
            if not known or name in known
        }
        return {"edges": edges, "layout": layout}

    @staticmethod
    def _unique_id(candidate: str, used: Set[str]) -> str:
        edge_id = candidate
        while not edge_id or edge_id in used:
            edge_id = uuid.uuid4().hex[:8]
        used.add(edge_id)
        return edge_id
