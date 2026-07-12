# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  基础存储类 - 提供文件操作的通用工具，支持YAML、JSONL和纯文本文件
  Base Storage Class - Common utilities for file operations (YAML, JSONL, text).

设计特点 / Design Features:
  - 原子化写入：使用临时文件+rename保证数据完整性
  - Atomic writes: Uses temp files + rename for data integrity
  - 并发控制：通过文件锁保护JSONL写入
  - Concurrency: File locks protect JSONL append operations
  - 异步I/O：使用aiofiles避免阻塞事件循环
  - Async I/O: Uses aiofiles to avoid blocking event loop
"""

import json
import asyncio
import os
import yaml
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional
from enum import Enum
import aiofiles
from app.storage.file_lock import get_file_lock
from app.utils.logger import get_logger

logger = get_logger(__name__)

_active_content_project: ContextVar[str] = ContextVar("wenshape_active_content_project", default="")


class _SafeCompatLoader(yaml.SafeLoader):
    """
    安全 YAML Loader（带兼容层）。

    历史原因：我们曾用 `yaml.dump` 写入数据文件，PyYAML 会把 Enum 序列化为
    `!!python/object/apply:...` 标签；而 `yaml.safe_load` 会拒绝该标签，导致 EXE
    读取旧 data 目录时报错。

    这里仅对白名单里的 Enum 标签做“安全降级”（转为普通字符串），不启用任意
    Python 对象构造，以避免 YAML 反序列化带来的 RCE 风险。
    """


_PY_APPLY_TAG_PREFIX = "tag:yaml.org,2002:python/object/apply:"
_ALLOWED_PY_APPLY_SUFFIX_PREFIXES = ("app.schemas.enums.",)


def _construct_python_apply(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Any:
    if not suffix.startswith(_ALLOWED_PY_APPLY_SUFFIX_PREFIXES):
        raise yaml.constructor.ConstructorError(
            None,
            None,
            f"Refusing unsafe YAML tag: {_PY_APPLY_TAG_PREFIX}{suffix}",
            getattr(node, "start_mark", None),
        )

    # Legacy Enum encoding is usually a sequence like: ["zh"] / ["en"] (or a scalar).
    if isinstance(node, yaml.SequenceNode):
        seq = loader.construct_sequence(node)
        val = seq[0] if seq else ""
    elif isinstance(node, yaml.ScalarNode):
        val = loader.construct_scalar(node)
    else:
        val = loader.construct_object(node)

    if isinstance(val, Enum):
        return str(val.value)
    return str(val)


_SafeCompatLoader.add_multi_constructor(_PY_APPLY_TAG_PREFIX, _construct_python_apply)


class _SafeDumper(yaml.SafeDumper):
    """安全 YAML Dumper：把 Enum 序列化为普通字符串值，避免写入 python/object/apply 标签。"""


def _represent_enum(dumper: yaml.SafeDumper, data: Enum) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(getattr(data, "value", str(data))))


_SafeDumper.add_multi_representer(Enum, _represent_enum)


class BaseStorage:
    """
    存储基类 - 提供通用的文件操作接口

    Base Storage Class - Provides common file operation interface.

    所有具体的存储实现（CardStorage, CanonStorage等）都应继承此类。
    提供了对YAML、JSONL和纯文本文件的异步读写操作。

    All concrete storage implementations (CardStorage, CanonStorage, etc.)
    should inherit from this class. Provides async operations for YAML, JSONL,
    and text files with atomic writes and concurrency control.

    Attributes:
        data_dir (Path): 数据根目录路径 / Root data directory path
        encoding (str): 文件编码，默认为utf-8 / File encoding, default utf-8
    """

    def __init__(self, data_dir: Optional[str] = None):
        """
        初始化存储实例

        Initialize storage instance.

        Args:
            data_dir: 数据根目录，如为None则从配置读取 / Root data directory, None reads from config
        """
        from app.config import settings

        raw_data_dir = str(data_dir or settings.data_dir)
        resolved = Path(raw_data_dir)
        if not resolved.is_absolute():
            # Resolve relative paths against the backend root instead of the current working directory
            # so scripts/tests behave consistently regardless of where they are launched from.
            backend_root = Path(__file__).resolve().parents[2]
            resolved = (backend_root / resolved).resolve()
        self.data_dir = resolved
        self.encoding = "utf-8"
        self._generation_store = None

    def _control_store(self):
        if self._generation_store is None:
            from app.control_plane.store import SQLiteControlStore

            self._generation_store = SQLiteControlStore(self.data_dir / "_system" / "control.sqlite3")
        return self._generation_store

    def _project_id_for_path(self, file_path: Path) -> str:
        try:
            relative = Path(file_path).resolve().relative_to(self.data_dir.resolve())
        except ValueError:
            return ""
        if not relative.parts or relative.parts[0].startswith("_"):
            return ""
        return str(relative.parts[0])

    @asynccontextmanager
    async def content_transaction(self, project_id: str) -> AsyncIterator[None]:
        project = str(project_id or "").strip()
        if not project or _active_content_project.get() == project:
            yield
            return
        try:
            await self._control_call(self._control_store().begin_project_write, project)
        except asyncio.CancelledError:
            await self._control_call(self._control_store().end_project_write, project)
            raise
        token = _active_content_project.set(project)
        try:
            yield
        finally:
            _active_content_project.reset(token)
            await self._control_call(self._control_store().end_project_write, project)

    @staticmethod
    async def _control_call(function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def get_project_path(self, project_id: str) -> Path:
        """
        获取项目目录路径

        Get project directory path.

        Args:
            project_id: 项目ID / Project ID

        Returns:
            项目目录路径 / Project directory path
        """
        return self.data_dir / project_id

    def ensure_dir(self, path: Path) -> None:
        """
        确保目录存在，必要时创建

        Ensure directory exists, create if necessary.

        Args:
            path: 目录路径 / Directory path
        """
        path.mkdir(parents=True, exist_ok=True)

    async def _atomic_write(self, file_path: Path, content: str) -> None:
        """
        原子化写入文件

        Write file atomically using temp file + rename strategy.

        确保文件完整性：
        - 写入临时文件
        - 原子性替换（rename）原始文件
        - 处理Windows特殊情况（权限锁定）
        - 清理临时文件

        Ensures data integrity by:
        - Writing to temp file first
        - Atomic rename to target
        - Windows-specific lock handling with retries
        - Cleanup of temp files

        Args:
            file_path: 目标文件路径 / Target file path
            content: 文件内容 / File content

        Raises:
            OSError: 如果写入失败且无法恢复 / If write fails and cannot recover
        """
        project_id = self._project_id_for_path(file_path)
        if project_id and _active_content_project.get() != project_id:
            async with self.content_transaction(project_id):
                await self._atomic_write(file_path, content)
            return

        self.ensure_dir(file_path.parent)
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        tmp_written = False
        try:
            # 写入临时文件 / Write to temp file
            async with aiofiles.open(tmp_path, "w", encoding=self.encoding) as f:
                await f.write(content)
            tmp_written = True
            try:
                # 原子性替换 / Atomic rename
                os.replace(tmp_path, file_path)
                tmp_written = False
            except (PermissionError, OSError) as exc:
                # Windows can deny replace when the destination file is open by another process
                # (e.g., concurrent readers without delete sharing). Retry briefly, then fall back
                # to direct write to avoid breaking core flows like evidence indexing / memory pack building.
                # Windows文件锁定：如果目标文件被其他进程打开，replace会失败
                # 重试几次，然后回退到直接写入
                winerror = getattr(exc, "winerror", None)
                is_windows_lock = isinstance(exc, PermissionError) or winerror in {5, 32}
                if not is_windows_lock:
                    raise

                last_exc: Exception = exc
                for attempt in range(4):
                    await asyncio.sleep(0.05 * (attempt + 1))
                    try:
                        os.replace(tmp_path, file_path)
                        tmp_written = False
                        break
                    except (PermissionError, OSError) as retry_exc:
                        last_exc = retry_exc
                        continue

                if tmp_written:
                    logger.warning(
                        "原子替换失败，回退到直接写入 / Atomic replace failed, falling back to direct write: %s",
                        last_exc,
                    )
                    for attempt in range(3):
                        try:
                            async with aiofiles.open(file_path, "w", encoding=self.encoding) as f:
                                await f.write(content)
                            break
                        except (PermissionError, OSError) as write_exc:
                            last_exc = write_exc
                            await asyncio.sleep(0.05 * (attempt + 1))
                    else:
                        raise last_exc
        finally:
            # 清理临时文件 / Cleanup temp file
            if tmp_path.exists() and tmp_path != file_path:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    # Best-effort cleanup: anti-virus / indexers can transiently lock tmp files.
                    # 尽力清理：杀毒软件/索引器可能会暂时锁定临时文件
                    for attempt in range(3):
                        try:
                            await asyncio.sleep(0.05 * (attempt + 1))
                            tmp_path.unlink(missing_ok=True)
                            break
                        except OSError:
                            continue

    async def read_yaml(self, file_path: Path) -> Dict[str, Any]:
        """
        异步读取YAML文件

        Read YAML file asynchronously.

        Args:
            file_path: YAML文件路径 / Path to YAML file

        Returns:
            解析后的内容（字典） / Parsed YAML content

        Raises:
            FileNotFoundError: 如果文件不存在 / If file not found
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        async with aiofiles.open(file_path, "r", encoding=self.encoding) as f:
            content = await f.read()
            return yaml.load(content, Loader=_SafeCompatLoader)

    async def write_yaml(self, file_path: Path, data: Dict[str, Any]) -> None:
        """
        异步写入YAML文件

        Write YAML file asynchronously.

        Args:
            file_path: YAML文件路径 / Path to YAML file
            data: 要写入的数据 / Data to write
        """
        self.ensure_dir(file_path.parent)

        yaml_content = yaml.dump(data, Dumper=_SafeDumper, allow_unicode=True, sort_keys=False)
        await self._atomic_write(file_path, yaml_content)

    async def read_jsonl(self, file_path: Path) -> list:
        """
        读取JSONL文件（每行一个JSON对象）

        Read JSONL file (one JSON object per line).

        Args:
            file_path: JSONL文件路径 / Path to JSONL file

        Returns:
            解析后的JSON对象列表 / List of parsed JSON objects
        """
        if not file_path.exists():
            return []

        items = []
        bad_lines = 0
        async with aiofiles.open(file_path, "r", encoding=self.encoding) as f:
            async for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except Exception:
                        bad_lines += 1
                        continue
        if bad_lines:
            logger.warning("JSONL解析跳过 %s 行坏行 / JSONL parse skipped %s bad lines: %s", bad_lines, str(file_path))
        return items

    async def append_jsonl(self, file_path: Path, item: Dict[str, Any]) -> None:
        """
        追加条目到JSONL文件（带锁保护）

        Append item to JSONL file with lock protection.

        用文件锁保护并发追加操作，确保数据完整性。
        Protected by file lock to ensure data integrity during concurrent appends.

        Args:
            file_path: JSONL文件路径 / Path to JSONL file
            item: 要追加的条目 / Item to append
        """
        project_id = self._project_id_for_path(file_path)
        if project_id and _active_content_project.get() != project_id:
            async with self.content_transaction(project_id):
                await self.append_jsonl(file_path, item)
            return

        self.ensure_dir(file_path.parent)

        file_lock = get_file_lock()
        async with file_lock.lock(file_path):
            async with aiofiles.open(file_path, "a", encoding=self.encoding) as f:
                await f.write(json.dumps(item, ensure_ascii=False) + "\n")

    async def write_jsonl(self, file_path: Path, items: list) -> None:
        """
        写入JSONL文件（带锁保护）

        Write JSONL file with lock protection.

        用文件锁保护，确保原子化写入。
        Protected by file lock for atomic writes.

        Args:
            file_path: JSONL文件路径 / Path to JSONL file
            items: 要写入的条目列表 / Items to write
        """
        file_lock = get_file_lock()
        async with file_lock.lock(file_path):
            await self._write_jsonl_unlocked(file_path, items)

    async def _write_jsonl_unlocked(self, file_path: Path, items: list) -> None:
        """Write JSONL while the caller owns the transaction lock."""

        lines = [json.dumps(item, ensure_ascii=False) for item in items]
        payload = "\n".join(lines) + ("\n" if lines else "")
        await self._atomic_write(file_path, payload)

    async def _read_jsonl_unlocked(self, file_path: Path) -> list:
        """Read JSONL while the caller owns the transaction lock."""

        return await self.read_jsonl(file_path)

    async def read_text(self, file_path: Path) -> str:
        """
        读取文本文件

        Read text file.

        Args:
            file_path: 文本文件路径 / Path to text file

        Returns:
            文件内容 / File content

        Raises:
            FileNotFoundError: 如果文件不存在 / If file not found
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        async with aiofiles.open(file_path, "r", encoding=self.encoding) as f:
            return await f.read()

    async def write_text(self, file_path: Path, content: str) -> None:
        """
        写入文本文件

        Write text file.

        Args:
            file_path: 文本文件路径 / Path to text file
            content: 要写入的内容 / Content to write
        """
        await self._atomic_write(file_path, content)
