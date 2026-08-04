# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  SillyTavern 卡片互操作 - Tavern 角色卡 / 世界书与 WenShape 卡片资产的唯一映射 owner
  Tavern Card Interop - the single owner mapping Tavern character cards and
  lorebooks to WenShape card assets, and back.

支持的输入 / Accepted inputs:
  - Character Card V1（扁平 JSON）、V2（``chara_card_v2``）、V3（``chara_card_v3``）
  - 独立世界书 ``{"spec": "lorebook_v3", "data": {...}}``
  - SillyTavern 原生 World Info 导出（``entries`` 为 uid -> entry 映射）
  - 以上 JSON 经 base64 嵌入 PNG ``tEXt`` / ``zTXt`` chunk（``ccv3`` 优先于 ``chara``）

安全边界 / Security boundary（导入内容一律按不可信外部数据处理）：
  - 只映射「设定文本」。一切 prompt 装配指令（``system_prompt`` /
    ``post_history_instructions`` / ``position`` / ``insertion_order`` / ``depth`` /
    ``@@`` decorator）在解析阶段剥离，绝不进入 ContextPlan 与 Provider payload。
  - 纯离线：不解析、不请求任何远程 URI，不落地任何二进制 asset。
  - 文件、PNG chunk 与 zlib 解压输出均设硬上限，防解压炸弹。
  - 所有被丢弃的字段都计入导入报告，不静默截断。

明确非目标 / Explicit non-goals:
  本模块只做「导入 / 导出」两次性映射，不做双向深度同步，不保留 Tavern 侧的
  round-trip 残留字段。ST 的关键词触发状态机（selective / probability /
  inclusion group / timed effects / recursion）不在此实现——WenShape 的召回
  owner 是 ``context_engine`` 的语义+词法融合检索，不建立第二套装配逻辑。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import zlib
from typing import Any, Dict, Iterator, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.schemas.card import CharacterCard, WorldCard
from app.utils.logger import get_logger
from app.utils.path_safety import sanitize_id
from app.utils.trust import detect_prompt_injection

logger = get_logger(__name__)


# --- 硬上限 / Hard limits：解析前后双向设限，任何一项超限即拒绝整个文件 ---
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_PNG_CHUNK_BYTES = 4 * 1024 * 1024
MAX_INFLATED_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 500
MAX_TEXT_CHARS = 20000
MAX_ALIASES = 32
MAX_ALIAS_CHARS = 64

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# ``ccv3`` 优先于 ``chara``：CCv3 规范要求二者并存时以 ccv3 为准。
CARD_CHUNK_KEYWORDS = ("ccv3", "chara")

# 只承载 prompt 装配指令、越狱或应用私有数据的字段：一律剥离。
# Fields that only carry prompt-assembly directives, jailbreaks or private blobs.
STRIPPED_CARD_FIELDS: Dict[str, str] = {
    "system_prompt": "覆盖系统提示词的注入载体 / overrides the system prompt",
    "post_history_instructions": "越狱槽位 / jailbreak slot",
    "extensions": "应用私有数据 / application-private data",
    "assets": "远程或二进制资源，永不解析 / remote or binary assets are never resolved",
    "creator_notes_multilingual": "非设定内容 / not setting content",
}

# 只影响 prompt 位置与预算的 entry 字段：读取但不落地。
STRIPPED_ENTRY_FIELDS: Dict[str, str] = {
    "position": "prompt 插入位置指令 / prompt insertion directive",
    "insertion_order": "prompt 插入顺序指令 / prompt ordering directive",
    "order": "prompt 插入顺序指令 / prompt ordering directive",
    "depth": "prompt 深度指令 / prompt depth directive",
    "role": "消息角色指令 / message role directive",
    "priority": "预算淘汰优先级 / budget eviction priority",
    "probability": "随机触发概率 / stochastic trigger",
    "extensions": "应用私有数据 / application-private data",
}

# decorator 行：``@@name value`` 与其 fallback ``@@@name value``（CCv3）。
_DECORATOR_LINE_RE = re.compile(r"^[ \t]*@@@?[A-Za-z_][A-Za-z0-9_]*[^\n]*\n?", re.MULTILINE)
_WHITESPACE_RUN_RE = re.compile(r"\n{3,}")


class TavernImportError(ValueError):
    """导入解析失败（格式不识别、超限或内容为空）。"""


class DroppedField(BaseModel):
    """一条被丢弃字段的记录（导入报告条目）。"""

    field: str = Field(..., description="Dropped source field name")
    reason: str = Field(..., description="Why the field was dropped")
    count: int = Field(1, ge=1, description="How many times it was dropped")


class TavernImportPlan(BaseModel):
    """导入预案：dry-run 阶段返回给用户确认，确认后按同一内容落盘。"""

    source_format: str = Field(..., description="Detected source format")
    source_filename: str = Field("", description="Original filename")
    characters: List[CharacterCard] = Field(default_factory=list, description="Mapped character cards")
    world_cards: List[WorldCard] = Field(default_factory=list, description="Mapped world cards")
    dropped: List[DroppedField] = Field(default_factory=list, description="Fields intentionally dropped")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal import warnings")
    injection_detected: bool = Field(False, description="Heuristic prompt-injection hit in imported text")


class _DropLog:
    """聚合丢弃字段记录，保持稳定顺序以便测试与展示。"""

    def __init__(self) -> None:
        self._items: Dict[str, DroppedField] = {}

    def add(self, field_name: str, reason: str, count: int = 1) -> None:
        if count <= 0:
            return
        existing = self._items.get(field_name)
        if existing is None:
            self._items[field_name] = DroppedField(field=field_name, reason=reason, count=count)
        else:
            existing.count += count

    def as_list(self) -> List[DroppedField]:
        return list(self._items.values())


# ---------------------------------------------------------------------------
# 文本与命名工具 / Text and naming helpers
# ---------------------------------------------------------------------------


def _clean_text(value: Any) -> str:
    """归一化为可安全展示的纯文本：去控制字符、裁剪长度。"""

    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        text = "\n".join(_clean_text(item) for item in value)
    elif isinstance(value, (dict, bool, int, float)):
        text = str(value)
    else:
        text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    text = _WHITESPACE_RUN_RE.sub("\n\n", text).strip()
    return text[:MAX_TEXT_CHARS]


def _strip_decorators(content: str) -> Tuple[str, int]:
    """剥离 CCv3 ``@@`` decorator 行，返回 (清理后正文, 剥离条数)。"""

    matches = _DECORATOR_LINE_RE.findall(content)
    if not matches:
        return content, 0
    return _DECORATOR_LINE_RE.sub("", content).strip(), len(matches)


def _split_keys(value: Any) -> List[str]:
    """兼容 ``keys: []`` 与 ST 的逗号分隔字符串两种写法。"""

    raw: List[str] = []
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                raw.append(item)
            elif item is not None:
                raw.append(str(item))
    out: List[str] = []
    for item in raw:
        text = _clean_text(item)[:MAX_ALIAS_CHARS].strip()
        # 正则型 key（``/pattern/flags``）是触发逻辑而非别名，落地无意义。
        if not text or (text.startswith("/") and text.rfind("/") > 0):
            continue
        out.append(text)
    return list(dict.fromkeys(out))[:MAX_ALIASES]


def _safe_card_name(raw: Any, *, fallback: str, used: Dict[str, int]) -> str:
    """产出可安全用作文件名且在本次导入内唯一的卡片名。"""

    candidate = _clean_text(raw).split("\n", 1)[0].strip()
    try:
        name = sanitize_id(candidate) if candidate else ""
    except ValueError:
        name = ""
    if not name:
        name = fallback
    seen = used.get(name, 0)
    used[name] = seen + 1
    if seen:
        name = f"{name}_{seen + 1}"
        used[name] = 1
    return name


def _labeled_sections(pairs: List[Tuple[str, str]]) -> str:
    """把 (标签, 文本) 拼成带标签的多段文本，跳过空段。"""

    return "\n\n".join(f"{label}: {text}" if label else text for label, text in pairs if text)


# ---------------------------------------------------------------------------
# PNG 解析 / PNG parsing
# ---------------------------------------------------------------------------


def _inflate_bounded(blob: bytes) -> bytes:
    """有界解压，超过 :data:`MAX_INFLATED_BYTES` 直接拒绝（防解压炸弹）。"""

    decompressor = zlib.decompressobj()
    try:
        out = decompressor.decompress(blob, MAX_INFLATED_BYTES)
    except zlib.error as exc:
        raise TavernImportError(f"tavern_import_bad_zlib_chunk: {exc}") from exc
    if decompressor.unconsumed_tail:
        raise TavernImportError("tavern_import_decompression_limit_exceeded")
    return out


def _iter_png_text_chunks(raw: bytes) -> Iterator[Tuple[str, bytes]]:
    """遍历 PNG 的 tEXt / zTXt / iTXt chunk，产出 (keyword, 文本字节)。"""

    offset = len(PNG_SIGNATURE)
    total = len(raw)
    while offset + 8 <= total:
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        chunk_type = raw[offset + 4 : offset + 8]
        if length > MAX_PNG_CHUNK_BYTES:
            raise TavernImportError("tavern_import_png_chunk_too_large")
        data_start = offset + 8
        data_end = data_start + length
        if data_end > total:
            break
        if chunk_type == b"IEND":
            break
        if chunk_type in (b"tEXt", b"zTXt", b"iTXt"):
            payload = raw[data_start:data_end]
            parsed = _parse_text_chunk(chunk_type, payload)
            if parsed is not None:
                yield parsed
        offset = data_end + 4  # 跳过 CRC


def _parse_text_chunk(chunk_type: bytes, payload: bytes) -> Optional[Tuple[str, bytes]]:
    """按 PNG 规范解析单个文本 chunk；解析不了就跳过，不让坏 chunk 毒死整个文件。"""

    keyword, sep, rest = payload.partition(b"\x00")
    if not sep:
        return None
    try:
        name = keyword.decode("latin-1").strip()
    except (UnicodeDecodeError, AttributeError):
        return None
    if chunk_type == b"tEXt":
        return name, rest
    if chunk_type == b"zTXt":
        if not rest:
            return None
        return name, _inflate_bounded(rest[1:])
    # iTXt: compression_flag(1) compression_method(1) language\x00 translated\x00 text
    if len(rest) < 2:
        return None
    compressed = rest[0] == 1
    body = rest[2:]
    _, sep_lang, body = body.partition(b"\x00")
    if not sep_lang:
        return None
    _, sep_tr, body = body.partition(b"\x00")
    if not sep_tr:
        return None
    return (name, _inflate_bounded(body)) if compressed else (name, body)


def _decode_embedded_json(blob: bytes) -> Optional[Any]:
    """卡片 chunk 通常是 base64(utf-8 json)，也兼容直接内嵌的裸 JSON。"""

    text = blob.decode("utf-8", errors="ignore").strip()
    if not text:
        return None
    if not text.startswith("{"):
        try:
            decoded = base64.b64decode(text, validate=False)
        except (binascii.Error, ValueError):
            return None
        if len(decoded) > MAX_INFLATED_BYTES:
            raise TavernImportError("tavern_import_embedded_payload_too_large")
        text = decoded.decode("utf-8", errors="ignore").strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_png_payload(raw: bytes) -> Any:
    """从 PNG 中取出角色卡 JSON，``ccv3`` 优先于 ``chara``。"""

    found: Dict[str, Any] = {}
    for keyword, blob in _iter_png_text_chunks(raw):
        key = keyword.lower()
        if key in CARD_CHUNK_KEYWORDS and key not in found:
            payload = _decode_embedded_json(blob)
            if payload is not None:
                found[key] = payload
    for key in CARD_CHUNK_KEYWORDS:
        if key in found:
            return found[key]
    raise TavernImportError("tavern_import_no_card_chunk_in_png")


# ---------------------------------------------------------------------------
# 结构归一化 / Structural normalization
# ---------------------------------------------------------------------------


def _normalize_card(payload: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """识别 V1 / V2 / V3 角色卡，返回 (卡片数据, 格式标签)。"""

    if not isinstance(payload, dict):
        return None, ""
    spec = str(payload.get("spec") or "").strip().lower()
    data = payload.get("data")
    if spec in {"chara_card_v2", "chara_card_v3"} and isinstance(data, dict):
        return data, spec
    # V1 是扁平结构，没有 spec 包裹。
    if "name" in payload and any(key in payload for key in ("description", "personality", "scenario", "first_mes")):
        return payload, "chara_card_v1"
    return None, ""


def _normalize_lorebook(payload: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """识别独立世界书与 ST 原生 World Info 导出，返回 (世界书数据, 格式标签)。"""

    if not isinstance(payload, dict):
        return None, ""
    spec = str(payload.get("spec") or "").strip().lower()
    data = payload.get("data")
    if spec.startswith("lorebook") and isinstance(data, dict):
        return data, "lorebook_v3"
    if isinstance(payload.get("entries"), (list, dict)):
        return payload, "world_info"
    return None, ""


def _iter_entries(book: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """世界书 entries 在规范里是 list，在 ST 原生导出里是 uid -> entry 映射。"""

    entries = book.get("entries")
    if isinstance(entries, dict):
        source: List[Any] = list(entries.values())
    elif isinstance(entries, list):
        source = list(entries)
    else:
        source = []
    for item in source[:MAX_ENTRIES]:
        if isinstance(item, dict):
            yield item


def _entry_enabled(entry: Dict[str, Any]) -> bool:
    """规范用 ``enabled``，ST 原生导出用反向的 ``disable``。"""

    if "enabled" in entry:
        return bool(entry.get("enabled"))
    if "disable" in entry:
        return not bool(entry.get("disable"))
    return True


# ---------------------------------------------------------------------------
# 映射：Tavern -> WenShape / Mapping inbound
# ---------------------------------------------------------------------------


def _map_character(data: Dict[str, Any], drops: _DropLog, used: Dict[str, int]) -> CharacterCard:
    """角色卡映射：设定文本进 description，对话样本进 voice（口吻项）。"""

    for field_name, reason in STRIPPED_CARD_FIELDS.items():
        if data.get(field_name):
            drops.add(field_name, reason)

    name = _safe_card_name(data.get("name"), fallback="imported_character", used=used)

    description = _labeled_sections(
        [
            ("", _clean_text(data.get("description"))),
            ("性格", _clean_text(data.get("personality"))),
            ("情境", _clean_text(data.get("scenario"))),
        ]
    )

    # 对话类字段不是叙事设定，但精确刻画了角色说话方式：绑定为角色专属口吻样本。
    voice = _labeled_sections(
        [
            ("开场白", _clean_text(data.get("first_mes"))),
            ("备选开场白", _clean_text(data.get("alternate_greetings"))),
            ("群聊开场白", _clean_text(data.get("group_only_greetings"))),
            ("示例对话", _clean_text(data.get("mes_example"))),
        ]
    )

    aliases = _split_keys(data.get("nickname"))
    return CharacterCard(
        name=name,
        aliases=aliases,
        description=description or name,
        voice=voice or None,
        stars=2,
    )


def _map_entry(
    entry: Dict[str, Any],
    *,
    book_name: str,
    drops: _DropLog,
    used: Dict[str, int],
) -> Optional[WorldCard]:
    """世界书 entry -> 世界观卡：keys 降维为 aliases，交给现有融合检索召回。"""

    for field_name, reason in STRIPPED_ENTRY_FIELDS.items():
        if entry.get(field_name) not in (None, "", [], {}):
            drops.add(f"entries[].{field_name}", reason)

    content, decorator_count = _strip_decorators(_clean_text(entry.get("content")))
    if decorator_count:
        drops.add("entries[].@@decorator", "prompt 控制指令 / prompt control directive", decorator_count)
    if not content:
        return None

    keys = _split_keys(entry.get("keys") if "keys" in entry else entry.get("key"))
    secondary = _split_keys(entry.get("secondary_keys") if "secondary_keys" in entry else entry.get("keysecondary"))
    label = entry.get("name") or entry.get("comment") or (keys[0] if keys else "")
    name = _safe_card_name(label, fallback="imported_lore", used=used)

    aliases = [alias for alias in dict.fromkeys(keys + secondary) if alias != name][:MAX_ALIASES]
    return WorldCard(
        name=name,
        description=content,
        aliases=aliases,
        category=book_name or None,
        stars=2,
    )


def _map_lorebook(
    book: Dict[str, Any],
    *,
    drops: _DropLog,
    used: Dict[str, int],
    default_name: str = "",
) -> List[WorldCard]:
    book_name = _clean_text(book.get("name"))[:64] or default_name
    cards: List[WorldCard] = []
    disabled = 0
    for entry in _iter_entries(book):
        if not _entry_enabled(entry):
            disabled += 1
            continue
        card = _map_entry(entry, book_name=book_name, drops=drops, used=used)
        if card:
            cards.append(card)
    if disabled:
        drops.add("entries[].disabled", "源文件中已禁用的条目 / entries disabled in source", disabled)
    return cards


# ---------------------------------------------------------------------------
# 公开 API / Public API
# ---------------------------------------------------------------------------


def parse_tavern_asset(raw: bytes, *, filename: str = "") -> TavernImportPlan:
    """解析 Tavern 角色卡或世界书，返回可供确认的导入预案。

    Args:
        raw: 上传文件的原始字节 / Raw uploaded bytes.
        filename: 原始文件名，仅用于报告 / Original filename, report only.

    Returns:
        导入预案 / A :class:`TavernImportPlan` ready for user confirmation.

    Raises:
        TavernImportError: 超限、格式不识别或没有可导入内容。
    """

    if not raw:
        raise TavernImportError("tavern_import_empty_payload")
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise TavernImportError("tavern_import_payload_too_large")

    if raw.startswith(PNG_SIGNATURE):
        payload = _extract_png_payload(raw)
        container = "png"
    else:
        try:
            payload = json.loads(raw.decode("utf-8", errors="ignore"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise TavernImportError("tavern_import_unrecognized_format") from exc
        container = "json"

    drops = _DropLog()
    warnings: List[str] = []
    character_names: Dict[str, int] = {}
    world_names: Dict[str, int] = {}
    characters: List[CharacterCard] = []
    world_cards: List[WorldCard] = []

    card_data, card_format = _normalize_card(payload)
    book_data, book_format = _normalize_lorebook(payload)

    if card_data is not None:
        characters.append(_map_character(card_data, drops, character_names))
        embedded_book = card_data.get("character_book")
        if isinstance(embedded_book, dict):
            world_cards.extend(
                _map_lorebook(
                    embedded_book,
                    drops=drops,
                    used=world_names,
                    default_name=characters[0].name,
                )
            )
        source_format = card_format
    elif book_data is not None:
        world_cards.extend(_map_lorebook(book_data, drops=drops, used=world_names))
        source_format = book_format
    else:
        raise TavernImportError("tavern_import_unrecognized_format")

    if not characters and not world_cards:
        raise TavernImportError("tavern_import_no_importable_content")

    # 导入内容是不可信外部数据：注入特征只作提示，不阻断，也不据此改写正文。
    probe = "\n".join(
        [card.description for card in characters]
        + [card.voice or "" for card in characters]
        + [card.description for card in world_cards]
    )
    injection = detect_prompt_injection(probe)
    if injection.get("detected"):
        warnings.append("imported_content_matched_prompt_injection_heuristics")

    if container == "png":
        drops.add("png_image", "只提取内嵌卡片数据，图片本身不落地 / image itself is not stored", 1)

    return TavernImportPlan(
        source_format=source_format,
        source_filename=filename,
        characters=characters,
        world_cards=world_cards,
        dropped=drops.as_list(),
        warnings=warnings,
        injection_detected=bool(injection.get("detected")),
    )


def build_lorebook_export(
    cards: List[WorldCard],
    *,
    name: str = "",
    description: str = "",
) -> Dict[str, Any]:
    """把世界观卡导出为独立世界书 ``lorebook_v3``。

    ``aliases`` 回填为 ``keys``（无别名时以卡名兜底，保证条目可被触发）。
    位置与预算类字段不写出：WenShape 侧不存在这些语义，凭空造值是失真。
    """

    entries: List[Dict[str, Any]] = []
    for index, card in enumerate(cards):
        keys = list(dict.fromkeys([alias for alias in (card.aliases or []) if alias] + [card.name]))
        entries.append(
            {
                "keys": keys,
                "content": card.description or "",
                "extensions": {},
                "enabled": True,
                "insertion_order": index,
                "use_regex": False,
                "constant": False,
                "name": card.name,
                "comment": card.name,
            }
        )
    return {
        "spec": "lorebook_v3",
        "data": {
            "name": name or "WenShape Lorebook",
            "description": description,
            "extensions": {},
            "entries": entries,
        },
    }


def build_character_export(
    card: CharacterCard,
    *,
    world_cards: Optional[List[WorldCard]] = None,
) -> Dict[str, Any]:
    """把角色卡导出为 Character Card V3。

    口吻项（``voice``）回写到 ``mes_example``——这是 Tavern 侧语义最接近的槽位。
    ``system_prompt`` / ``post_history_instructions`` 始终导出为空串：WenShape
    不产出这类指令，也不应把它们塞进流通到第三方前端的文件里。
    """

    data: Dict[str, Any] = {
        "name": card.name,
        "description": card.description or "",
        "personality": "",
        "scenario": "",
        "first_mes": "",
        "mes_example": card.voice or "",
        "creator_notes": "Exported from WenShape.",
        "system_prompt": "",
        "post_history_instructions": "",
        "alternate_greetings": [],
        "group_only_greetings": [],
        "tags": [],
        "creator": "WenShape",
        "character_version": "1.0",
        "extensions": {},
    }
    if card.aliases:
        data["nickname"] = card.aliases[0]
    if world_cards:
        data["character_book"] = build_lorebook_export(world_cards, name=f"{card.name} Lorebook")["data"]
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}
