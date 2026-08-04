"""
中文说明：卡片存储读写服务。

Card storage.
"""

from typing import List, Optional, Dict, Any
import re

from app.storage.base import BaseStorage
from app.schemas.card import CharacterCard, WorldCard, StyleCard


class CardStorage(BaseStorage):
    """Storage operations for cards."""

    async def get_character_card(
        self,
        project_id: str,
        character_name: str,
    ) -> Optional[CharacterCard]:
        file_path = self.get_project_path(project_id) / "cards" / "characters" / f"{character_name}.yaml"

        if not file_path.exists():
            return None

        data = await self.read_yaml(file_path)
        coerced = self._coerce_character_data(data)
        return CharacterCard(**coerced)

    async def save_character_card(self, project_id: str, card: CharacterCard) -> None:
        file_path = self.get_project_path(project_id) / "cards" / "characters" / f"{card.name}.yaml"

        payload = card.model_dump(exclude_none=True)
        if file_path.exists():
            try:
                existing = await self.read_yaml(file_path)
                if isinstance(existing, dict):
                    existing.update(payload)
                    if "stars" not in existing:
                        existing["stars"] = self._normalize_stars(None)
                    payload = existing
            except (KeyError, TypeError, ValueError):
                pass
        if "stars" not in payload:
            payload["stars"] = self._normalize_stars(None)

        await self.write_yaml(file_path, payload)

    async def list_character_cards(self, project_id: str) -> List[str]:
        cards_dir = self.get_project_path(project_id) / "cards" / "characters"
        if not cards_dir.exists():
            return []

        return [f.stem for f in cards_dir.glob("*.yaml")]

    async def delete_character_card(self, project_id: str, character_name: str) -> bool:
        file_path = self.get_project_path(project_id) / "cards" / "characters" / f"{character_name}.yaml"

        if file_path.exists():
            file_path.unlink()
            await self._purge_card_index(project_id, character_name)
            await self._relation_storage().purge_character(project_id, character_name)
            return True
        return False

    def _relation_storage(self):
        """按需构造角色关系存储（U4 设定层关系边）。

        函数级导入 + 就地构造：避免 storage.cards → dependencies 的循环导入，
        同时继承本实例的 data_dir（测试与多数据目录场景下不会写错位置）。
        """
        from app.storage.character_relations import CharacterRelationStorage

        return CharacterRelationStorage(str(self.data_dir))

    async def get_world_card(self, project_id: str, card_name: str) -> Optional[WorldCard]:
        file_path = self.get_project_path(project_id) / "cards" / "world" / f"{card_name}.yaml"
        if not file_path.exists():
            return None

        data = await self.read_yaml(file_path)
        coerced = self._coerce_world_data(data)
        return WorldCard(**coerced)

    async def save_world_card(self, project_id: str, card: WorldCard) -> None:
        file_path = self.get_project_path(project_id) / "cards" / "world" / f"{card.name}.yaml"
        payload = card.model_dump(exclude_none=True)
        # World cards are now description-first; stop writing deprecated fields.
        payload.pop("rules", None)
        payload.pop("immutable", None)
        if file_path.exists():
            try:
                existing = await self.read_yaml(file_path)
                if isinstance(existing, dict):
                    existing.update(payload)
                    if "stars" not in existing:
                        existing["stars"] = self._normalize_stars(None)
                    payload = existing
            except (KeyError, TypeError, ValueError):
                pass
        if "stars" not in payload:
            payload["stars"] = self._normalize_stars(None)
        await self.write_yaml(file_path, payload)

    async def list_world_cards(self, project_id: str) -> List[str]:
        cards_dir = self.get_project_path(project_id) / "cards" / "world"
        if not cards_dir.exists():
            return []
        return [f.stem for f in cards_dir.glob("*.yaml")]

    async def delete_world_card(self, project_id: str, card_name: str) -> bool:
        file_path = self.get_project_path(project_id) / "cards" / "world" / f"{card_name}.yaml"
        if file_path.exists():
            file_path.unlink()
            await self._purge_card_index(project_id, card_name)
            return True
        return False

    async def _purge_card_index(self, project_id: str, card_name: str) -> None:
        """删除卡片后立即使派生检索索引与关系快照失效，避免已删实体继续进入 Agent 上下文。"""
        path = self.get_project_path(project_id) / "index" / "cards.jsonl"
        rows = await self.read_jsonl(path)
        if rows:
            kept = []
            for row in rows:
                source = row.get("source") if isinstance(row, dict) else None
                indexed_card = source.get("card") if isinstance(source, dict) else ""
                if str(indexed_card or "") != str(card_name):
                    kept.append(row)
            if len(kept) != len(rows):
                await self.write_jsonl(path, kept)
        # 关系三元组是以实体为主语/宾语的派生快照：按 subject/object 精确匹配
        # 失效，避免 query_relations 把已删除实体重新注入上下文。只做精确匹配——
        # 旧实现用整行 JSON 子串匹配，卡名越短命中面越大（"王" 会命中 "国王"），
        # 批量导入的世界书条目会把它放大成数据删除事故。本段不依赖卡片索引存在。
        # canon/facts.jsonl 不在此清理：事实是从正文抽取的故事真相源，
        # 其生命周期归章节级联管理，不因设定卡被删而销毁。
        relations_path = self.get_project_path(project_id) / "canon" / "relations.jsonl"
        relations = await self.read_jsonl(relations_path)
        if not relations:
            return
        target = str(card_name).strip()
        remaining = [
            entry
            for entry in relations
            if not (
                isinstance(entry, dict)
                and target
                and (str(entry.get("subject") or "").strip() == target or str(entry.get("object") or "").strip() == target)
            )
        ]
        if len(remaining) != len(relations):
            await self.write_jsonl(relations_path, remaining)

    async def get_style_card(self, project_id: str) -> Optional[StyleCard]:
        file_path = self.get_project_path(project_id) / "cards" / "style.yaml"
        if not file_path.exists():
            return None

        data = await self.read_yaml(file_path)
        coerced = self._coerce_style_data(data)
        return StyleCard(**coerced)

    async def save_style_card(self, project_id: str, card: StyleCard) -> None:
        file_path = self.get_project_path(project_id) / "cards" / "style.yaml"
        await self.write_yaml(file_path, card.model_dump())

    def _coerce_character_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        name = str(data.get("name", "")).strip()
        aliases = self._normalize_aliases(data.get("aliases"))
        stars = self._normalize_stars(data.get("stars"))
        voice = str(data.get("voice") or "").strip() or None
        description = str(data.get("description", "")).strip()
        if description:
            return {
                "name": name,
                "aliases": aliases,
                "description": description,
                "voice": voice,
                "stars": stars,
            }

        parts = []
        identity = str(data.get("identity", "")).strip()
        if identity:
            parts.append(f"身份: {identity}")
        appearance = str(data.get("appearance", "")).strip()
        if appearance:
            parts.append(f"外貌: {appearance}")
        motivation = str(data.get("motivation", "")).strip()
        if motivation:
            parts.append(f"动机: {motivation}")
        personality = data.get("personality") or []
        if isinstance(personality, list) and personality:
            parts.append(f"性格: {', '.join([str(item) for item in personality if item])}")
        speech_pattern = str(data.get("speech_pattern", "")).strip()
        if speech_pattern:
            parts.append(f"口吻: {speech_pattern}")
        relationships = data.get("relationships") or []
        if isinstance(relationships, list) and relationships:
            rel_parts = []
            for item in relationships:
                target = str(item.get("target", "")).strip() if isinstance(item, dict) else ""
                relation = str(item.get("relation", "")).strip() if isinstance(item, dict) else ""
                if target or relation:
                    rel_parts.append(f"{target}({relation})".strip())
            if rel_parts:
                parts.append(f"关系: {', '.join(rel_parts)}")
        boundaries = data.get("boundaries") or []
        if isinstance(boundaries, list) and boundaries:
            parts.append(f"边界: {', '.join([str(item) for item in boundaries if item])}")
        arc = str(data.get("arc", "")).strip()
        if arc:
            parts.append(f"角色弧线: {arc}")

        return {
            "name": name,
            "aliases": aliases,
            "description": "\n".join(parts).strip(),
            "voice": voice,
            "stars": stars,
        }

    def _coerce_world_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        aliases = self._normalize_aliases(data.get("aliases"))
        stars = self._normalize_stars(data.get("stars"))
        category = str(data.get("category", "")).strip()
        category = category if category else None
        legacy_rules = data.get("rules") or []
        if isinstance(legacy_rules, str):
            legacy_rules = [item.strip() for item in re.split(r"[,\n，;；]+", legacy_rules) if item.strip()]
        if not isinstance(legacy_rules, list):
            legacy_rules = []
        legacy_rules = [str(item).strip() for item in legacy_rules if str(item).strip()]

        if legacy_rules:
            rules_text = "；".join(legacy_rules)
            if description:
                if rules_text not in description:
                    description = f"{description}\n规则补充: {rules_text}".strip()
            else:
                description = f"规则补充: {rules_text}"

        if not description:
            parts = []
            if category:
                parts.append(f"类型: {category}")
            description = "\n".join([item for item in parts if item]).strip()

        return {
            "name": name,
            "description": description,
            "aliases": aliases,
            "category": category,
            "stars": stars,
        }

    def _normalize_aliases(self, value: Any) -> List[str]:
        if isinstance(value, str):
            parts = re.split(r"[,，;；\n]+", value)
        elif isinstance(value, list):
            parts = value
        else:
            parts = []
        aliases = [str(item).strip() for item in parts if str(item).strip()]
        return list(dict.fromkeys(aliases))

    def _normalize_stars(self, value: Any) -> int:
        try:
            stars = int(value)
        except (TypeError, ValueError):
            return 1
        return max(1, min(stars, 3))

    def _coerce_style_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        style = str(data.get("style", "")).strip()
        if style:
            return {"style": style}

        content = str(data.get("content", "")).strip()
        if content:
            return {"style": content}

        parts = []
        narrative_distance = str(data.get("narrative_distance", "")).strip()
        if narrative_distance:
            parts.append(f"叙事距离: {narrative_distance}")
        pacing = str(data.get("pacing", "")).strip()
        if pacing:
            parts.append(f"节奏: {pacing}")
        sentence_structure = str(data.get("sentence_structure", "")).strip()
        if sentence_structure:
            parts.append(f"句式: {sentence_structure}")
        vocab = data.get("vocabulary_constraints") or []
        if isinstance(vocab, list) and vocab:
            parts.append(f"用词: {', '.join([str(item) for item in vocab if item])}")
        examples = data.get("example_passages") or []
        if isinstance(examples, list) and examples:
            parts.append("参考片段:\n" + "\n---\n".join([str(item) for item in examples if item]))

        return {"style": "\n".join([item for item in parts if item]).strip()}
