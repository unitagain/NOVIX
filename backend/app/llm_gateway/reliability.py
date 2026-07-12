"""Provider reliability controls shared by chat and streaming requests."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from pathlib import Path

from app.config import settings
from app.control_plane.runtime import get_control_store
from app.control_plane.store import SQLiteControlStore


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: float = 0.0
    half_open_inflight: bool = False


@dataclass
class ProviderControl:
    semaphore: asyncio.Semaphore
    circuit: CircuitState = field(default_factory=CircuitState)
    last_request_at: float = 0.0
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    rate_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class ProviderLease:
    control: ProviderControl
    half_open: bool = False
    released: bool = False


@dataclass(frozen=True)
class RequestDeadline:
    total_seconds: float
    started_at: float = field(default_factory=time.monotonic)

    @property
    def expires_at(self) -> float:
        return self.started_at + max(0.001, float(self.total_seconds))

    def remaining(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("request_deadline_exceeded")
        return remaining

    async def wait_for(self, awaitable, *, limit: Optional[float] = None):
        try:
            timeout = self.remaining()
        except Exception:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise
        if limit is not None:
            timeout = min(timeout, max(0.001, float(limit)))
        return await asyncio.wait_for(awaitable, timeout=timeout)

    async def sleep(self, delay: float) -> None:
        await self.wait_for(asyncio.sleep(max(0.0, float(delay))))


class GatewayReliabilityController:
    def __init__(
        self,
        *,
        max_concurrency: int = 4,
        min_interval_ms: int = 0,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
    ):
        self.max_concurrency = max(1, int(max_concurrency))
        self.min_interval = max(0.0, float(min_interval_ms) / 1000.0)
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._controls: Dict[str, ProviderControl] = {}
        self._responses: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def control(self, provider_key: str) -> ProviderControl:
        return self._controls.setdefault(provider_key, ProviderControl(asyncio.Semaphore(self.max_concurrency)))

    async def before_request(
        self,
        provider_key: str,
        *,
        deadline: Optional[RequestDeadline] = None,
    ) -> ProviderLease:
        control = self.control(provider_key)
        half_open = False
        acquired = False
        try:
            async with control.state_lock:
                circuit = control.circuit
                now = time.monotonic()
                if circuit.opened_at:
                    if now - circuit.opened_at < self.cooldown_seconds:
                        raise RuntimeError("provider_circuit_open")
                    if circuit.half_open_inflight:
                        raise RuntimeError("provider_circuit_half_open_busy")
                    circuit.half_open_inflight = True
                    half_open = True

            if deadline is None:
                await control.semaphore.acquire()
            else:
                await deadline.wait_for(control.semaphore.acquire())
            acquired = True

            async with control.rate_lock:
                wait = self.min_interval - (time.monotonic() - control.last_request_at)
                if wait > 0:
                    if deadline is None:
                        await asyncio.sleep(wait)
                    else:
                        await deadline.sleep(wait)
                control.last_request_at = time.monotonic()
            return ProviderLease(control=control, half_open=half_open)
        except BaseException:
            if half_open:
                control.circuit.half_open_inflight = False
            if acquired:
                control.semaphore.release()
            raise

    def success(self, lease: ProviderLease | ProviderControl) -> None:
        resolved = self._lease(lease)
        if resolved.released:
            return
        resolved.control.circuit = CircuitState()
        self._release(resolved)

    def failure(self, lease: ProviderLease | ProviderControl) -> None:
        resolved = self._lease(lease)
        if resolved.released:
            return
        state = resolved.control.circuit
        state.failures += 1
        state.half_open_inflight = False
        if state.failures >= self.failure_threshold:
            state.opened_at = time.monotonic()
        self._release(resolved)

    def cancelled(self, lease: ProviderLease | ProviderControl) -> None:
        resolved = self._lease(lease)
        if resolved.released:
            return
        if resolved.half_open:
            resolved.control.circuit.half_open_inflight = False
        self._release(resolved)

    @staticmethod
    def _lease(value: ProviderLease | ProviderControl) -> ProviderLease:
        return value if isinstance(value, ProviderLease) else ProviderLease(control=value)

    @staticmethod
    def _release(lease: ProviderLease) -> None:
        lease.released = True
        lease.control.semaphore.release()

    async def cached_response(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        async with self._lock:
            value = self._responses.get(idempotency_key)
            return dict(value) if value else None

    async def cache_response(self, idempotency_key: Optional[str], response: Dict[str, Any]) -> None:
        if not idempotency_key:
            return
        async with self._lock:
            self._responses[idempotency_key] = dict(response)
            if len(self._responses) > 1000:
                for key in list(self._responses)[:500]:
                    self._responses.pop(key, None)

    def snapshot(self) -> Dict[str, Any]:
        return {
            key: {
                "failures": value.circuit.failures,
                "open": bool(value.circuit.opened_at),
                "half_open_inflight": value.circuit.half_open_inflight,
            }
            for key, value in self._controls.items()
        }


class PersistentIdempotencyRegistry:
    """Persist request state without storing provider response content."""

    def __init__(self, root: Optional[Path] = None, *, store: Optional[SQLiteControlStore] = None):
        use_shared_store = root is None
        if root is None:
            data = Path(settings.data_dir)
            if not data.is_absolute():
                data = (Path(__file__).resolve().parents[2] / data).resolve()
            root = data / "_system" / "gateway_idempotency"
        self.root = Path(root)
        self.store = store or (get_control_store() if use_shared_store else SQLiteControlStore(self.root / "control.sqlite3"))
        self.store.import_legacy_idempotency(self.root)

    async def begin(self, key: str, request_fingerprint: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self.store.begin_idempotency, key, request_fingerprint)

    async def complete(self, key: str, request_fingerprint: str, response: Dict[str, Any]) -> None:
        await asyncio.to_thread(
            self.store.complete_idempotency,
            key,
            request_fingerprint,
            response,
        )

    async def fail(self, key: str, request_fingerprint: str, reason: str) -> None:
        await asyncio.to_thread(self.store.fail_idempotency, key, request_fingerprint, reason)
