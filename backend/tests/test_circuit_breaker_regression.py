"""Regression: circuit breaker counts one failure per logical request, not per retry.

缺陷：`_execute_chat` 每次尝试都调用 `reliability.failure()`，而 `_chat_with_retry`
会重试 max_retries+1 次；于是**单个失败请求**就把熔断计数顶到阈值、误开熔断，导致
后续所有请求（反问生成、跳过后的撰写等）立刻 `provider_circuit_open` 卡死。

修复：重试中间尝试走 `retry_release`（只归还并发额度、不计熔断）；由 `note_failure`
在请求终态时计一次。本测试锁定「一个失败请求最多让熔断计数 +1」。
"""

from __future__ import annotations

import asyncio

from app.llm_gateway.reliability import GatewayReliabilityController


def _simulate_failed_request_with_retries(controller: GatewayReliabilityController, key: str, attempts: int) -> None:
    """模拟一次逻辑请求：attempts 次重试都失败（retry_release），终态计一次 note_failure。"""

    async def _run() -> None:
        for _ in range(attempts):
            lease = await controller.before_request(key)
            controller.retry_release(lease)  # 中间/终态尝试都只归还额度
        controller.note_failure(key)  # 请求终态：计一次熔断失败

    asyncio.run(_run())


def test_single_failed_request_with_retries_does_not_open_circuit():
    controller = GatewayReliabilityController(max_concurrency=4, failure_threshold=5, cooldown_seconds=30.0)
    key = "deepseek:deepseek-chat"

    # 一个失败请求内部重试 6 次（max_retries+1）——修复前会 +6 顶开熔断。
    _simulate_failed_request_with_retries(controller, key, attempts=6)

    snap = controller.snapshot()[key]
    assert snap["failures"] == 1, "一个失败请求只应记一次熔断失败"
    assert snap["open"] is False, "单个失败请求不得开熔断"


def test_circuit_opens_only_after_threshold_distinct_failed_requests():
    controller = GatewayReliabilityController(max_concurrency=4, failure_threshold=5, cooldown_seconds=30.0)
    key = "deepseek:deepseek-chat"

    for _ in range(4):
        _simulate_failed_request_with_retries(controller, key, attempts=6)
    assert controller.snapshot()[key]["open"] is False  # 4 个失败请求仍未开

    _simulate_failed_request_with_retries(controller, key, attempts=6)
    assert controller.snapshot()[key]["open"] is True  # 第 5 个失败请求才开熔断


def test_success_resets_failure_count():
    controller = GatewayReliabilityController(failure_threshold=5)
    key = "p:m"
    _simulate_failed_request_with_retries(controller, key, attempts=3)
    assert controller.snapshot()[key]["failures"] == 1

    async def _succeed() -> None:
        lease = await controller.before_request(key)
        controller.success(lease)

    asyncio.run(_succeed())
    assert controller.snapshot()[key]["failures"] == 0
    assert controller.snapshot()[key]["open"] is False
