# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  文件锁管理器 - 异步安全的文件锁，用于并发写入保护
  File Lock Manager - Async-safe file locking for concurrent write protection.

实现方式 / Implementation:
  使用asyncio.Lock实现进程内的文件锁定。适用于单进程应用。
  若需多进程支持，应使用fcntl（Unix）或msvcrt（Windows）。

  Uses asyncio.Lock for in-process file locking suitable for single-process apps.
  For multi-process support, use fcntl (Unix) or msvcrt (Windows).
"""

import asyncio
import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, Dict, Optional
from contextlib import asynccontextmanager
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AsyncFileLock:
    """
    异步文件锁 - 基于asyncio.Lock实现的进程内文件锁定

    Async File Lock - In-process file locking using asyncio.Lock.

    使用per-file的asyncio.Lock确保在多个并发写入操作中保护单个文件。
    每个文件路径都有独立的Lock，不同文件可以并发操作。

    Uses per-file asyncio.Lock to protect against concurrent writes.
    Each file has its own lock, different files can be accessed concurrently.

    Attributes:
        _locks (Dict[str, asyncio.Lock]): 文件路径到锁的映射 / Mapping from file path to lock
        _global_lock (asyncio.Lock): 保护_locks字典本身的全局锁 / Global lock protecting _locks dict
    """

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_lock(self, file_path: str) -> asyncio.Lock:
        """
        获取或创建文件锁

        Get or create lock for file path.

        Args:
            file_path: 文件路径字符串 / File path string

        Returns:
            asyncio.Lock实例 / asyncio.Lock instance
        """
        async with self._global_lock:
            if file_path not in self._locks:
                self._locks[file_path] = asyncio.Lock()
            return self._locks[file_path]

    @asynccontextmanager
    async def lock(self, file_path: Path, timeout: Optional[float] = 30.0):
        """
        获取文件锁（上下文管理器）

        Acquire file lock as context manager.

        Args:
            file_path: 文件路径 / File path
            timeout: 超时时间（秒），None表示无限等待 / Timeout in seconds, None for infinite

        Yields:
            None

        Raises:
            asyncio.TimeoutError: 如果在timeout秒内无法获取锁 / If lock cannot be acquired within timeout

        Example:
            >>> async with file_lock.lock(Path("data.json")):
            ...     # Protected write operation
            ...     await write_file(...)
        """
        path = file_path.resolve()
        path_str = str(path)
        lock = await self._get_lock(path_str)
        acquired = False
        process_handle: Optional[BinaryIO] = None

        try:
            if timeout is not None:
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=timeout)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError(f"file_lock_timeout:{path}") from exc
            else:
                await lock.acquire()
            acquired = True

            process_handle = await asyncio.to_thread(self._acquire_process_lock, path, timeout)

            yield
        finally:
            if process_handle is not None:
                await asyncio.to_thread(self._release_process_lock, process_handle)
            if acquired:
                lock.release()

    @staticmethod
    def _lock_path(target: Path) -> Path:
        digest = hashlib.sha256(str(target).casefold().encode("utf-8")).hexdigest()
        return Path(tempfile.gettempdir()) / "wenshape-file-locks" / f"{digest}.lock"

    @classmethod
    def _acquire_process_lock(cls, target: Path, timeout: Optional[float]) -> BinaryIO:
        """Acquire a cross-process advisory lock on a stable companion file."""

        lock_path = cls._lock_path(target)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except (BlockingIOError, OSError):
                if deadline is not None and time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(f"file_lock_timeout:{target}")
                time.sleep(0.025)

    @staticmethod
    def _release_process_lock(handle: BinaryIO) -> None:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    async def cleanup_unused(self, max_locks: int = 1000) -> int:
        """
        清理未使用的锁

        Clean up unused locks.

        当锁的数量超过max_locks时，删除一半的未锁定锁以防止内存泄漏。
        When lock count exceeds max_locks, remove half of unlocked locks to prevent memory leak.

        Args:
            max_locks: 最大锁数量阈值 / Maximum locks threshold

        Returns:
            清理的锁数量 / Number of locks cleaned
        """
        async with self._global_lock:
            if len(self._locks) <= max_locks:
                return 0

            # 找出未锁定的锁 / Find unlocked locks
            unlocked = [path for path, lock in self._locks.items() if not lock.locked()]

            # 删除一半未锁定的锁 / Remove half of unlocked locks
            to_remove = unlocked[: len(unlocked) // 2]
            for path in to_remove:
                del self._locks[path]

            return len(to_remove)

    def get_stats(self) -> Dict[str, int]:
        """
        获取锁统计信息

        Get lock statistics.

        Returns:
            包含以下统计信息的字典 / Dictionary with statistics:
            - total_locks: 总锁数量 / Total number of locks
            - locked: 已锁定的锁数量 / Number of locked locks
            - unlocked: 未锁定的锁数量 / Number of unlocked locks
        """
        locked_count = sum(1 for lock in self._locks.values() if lock.locked())
        return {
            "total_locks": len(self._locks),
            "locked": locked_count,
            "unlocked": len(self._locks) - locked_count,
        }


# 全局文件锁实例 / Global file lock instance
_file_lock: Optional[AsyncFileLock] = None


def get_file_lock() -> AsyncFileLock:
    """
    获取全局文件锁实例（单例）

    Get global file lock instance (singleton).

    Returns:
        全局AsyncFileLock实例 / Global AsyncFileLock instance
    """
    global _file_lock
    if _file_lock is None:
        _file_lock = AsyncFileLock()
    return _file_lock
