# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  通用 agentic 工具循环（Phase 3）—— "LLM 在循环里自主调用工具"
  (Anthropic: agents = LLMs autonomously using tools in a loop)。

  注意 / Note:
  消息回放对 OpenAI 兼容 provider 用 `assistant.tool_calls` + `role="tool"`；
  对 Anthropic 用 `tool_use` / `tool_result` 内容块（Phase 5 已适配，按首个响应的 provider 判定）。
  thinking（若 provider 返回）经 on_event 以 {"type":"thinking"} 透传，供透明化展示。
"""

from typing import Any, Awaitable, Callable, Dict, List, Optional
import json

from app.utils.logger import get_logger
from app.error_contract import record_degradation

logger = get_logger(__name__)

OnEvent = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


def _parse_tool_args(arguments: Any) -> Dict[str, Any]:
    """把工具参数（JSON 字符串或 dict）解析为 dict，供 Anthropic tool_use.input 用。"""
    if isinstance(arguments, dict):
        return arguments
    try:
        data = json.loads(arguments or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


async def _emit(on_event: OnEvent, event: Dict[str, Any]) -> None:
    if not on_event:
        return
    try:
        await on_event(event)
    except Exception as exc:
        record_degradation("agentic_event_callback", exc)


# Phase 8 代谢：agentic 多轮循环里工具结果会无限累积膨胀上下文；保留最近的、折叠更早的。
_DEFAULT_TOOL_RESULT_BUDGET = 8000  # 工具结果保留的字符预算（超出的更早结果折叠为占位符）
_FOLD_PLACEHOLDER = "[早前工具结果已折叠（节省上下文）]"


def _fold_old_tool_results(msgs: List[Dict[str, Any]], budget: int = _DEFAULT_TOOL_RESULT_BUDGET) -> int:
    """从后往前累计工具结果字符，超出 budget 的更早结果折叠为占位符（思考.md 的 Context Editing 思想）。

    OpenAI 兼容（role="tool"）与 Anthropic（user 消息内 tool_result 块）两种格式分别处理。
    就地修改 msgs；返回折叠条数（供观测/测试）。budget<=0 则不折叠。
    """
    if not budget or budget <= 0:
        return 0
    remaining = int(budget)
    folded = 0
    for msg in reversed(msgs):
        role = msg.get("role")
        if role == "tool":  # OpenAI 兼容格式
            content = str(msg.get("content") or "")
            if not content or content == _FOLD_PLACEHOLDER:
                continue
            if remaining >= len(content):
                remaining -= len(content)
            else:
                msg["content"] = _FOLD_PLACEHOLDER
                folded += 1
        elif role == "user" and isinstance(msg.get("content"), list):  # Anthropic tool_result 块
            for block in msg["content"]:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                content = str(block.get("content") or "")
                if not content or content == _FOLD_PLACEHOLDER:
                    continue
                if remaining >= len(content):
                    remaining -= len(content)
                else:
                    block["content"] = _FOLD_PLACEHOLDER
                    folded += 1
    return folded


async def run_agentic_chat(
    gateway,
    provider: str,
    messages: List[Dict[str, Any]],
    toolset,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    max_iterations: int = 4,
    thinking: Optional[Any] = None,
    on_event: OnEvent = None,
    tool_result_budget: int = _DEFAULT_TOOL_RESULT_BUDGET,
) -> Dict[str, Any]:
    """运行"LLM ↔ 工具"循环，直到模型不再请求工具或达到上限，返回最终响应 dict。

    Args:
        gateway: LLMGateway（需 Phase 2 的 tools 透传与 tool_calls 返回）。
        provider: profile id。
        messages: 初始消息（system + user）。
        toolset: 提供 .schemas() 与 async .execute(name, arguments) 的工具集。
        max_iterations: 最大工具轮数；达到上限后做一次"不带工具"的强制产出。
        thinking: 可选，透传给 gateway.chat 的 thinking 配置（默认 None=关）。
        on_event: 可选 async 回调，透传 thinking/工具调用过程（供 Phase 5 透明化复用）。
            事件形如 {"type": "thinking"|"tool_call"|"tool_result", ...}。

    Returns:
        最终的 chat 响应 dict（含 content / usage 等）。

    说明：消息回放对 OpenAI 兼容 provider 用 `assistant.tool_calls` + `role:"tool"`；
    对 Anthropic 用 `tool_use` / `tool_result` 内容块（按首个响应的 provider 判定）。
    """
    msgs: List[Dict[str, Any]] = list(messages)
    schemas = toolset.schemas()
    is_anthropic = False

    for _ in range(max(1, max_iterations)):
        resp = await gateway.chat(
            msgs, provider=provider, temperature=temperature, max_tokens=max_tokens, tools=schemas, thinking=thinking
        )
        is_anthropic = str(resp.get("provider") or "").lower() == "anthropic"

        thought = str(resp.get("thinking") or "").strip()
        if thought:
            await _emit(on_event, {"type": "thinking", "content": thought})

        tool_calls = resp.get("tool_calls")
        if not tool_calls:
            return resp

        # 回放 assistant 的工具调用消息（按 provider 选择格式）
        if is_anthropic:
            content_blocks: List[Dict[str, Any]] = []
            if resp.get("content"):
                content_blocks.append({"type": "text", "text": resp.get("content")})
            for tc in tool_calls:
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "input": _parse_tool_args(tc.get("arguments")),
                    }
                )
            msgs.append({"role": "assistant", "content": content_blocks})
        else:
            msgs.append(
                {
                    "role": "assistant",
                    "content": resp.get("content") or "",
                    "tool_calls": [
                        {
                            "id": tc.get("id"),
                            "type": "function",
                            "function": {"name": tc.get("name"), "arguments": tc.get("arguments") or "{}"},
                        }
                        for tc in tool_calls
                    ],
                }
            )

        # 执行工具并回灌结果（Anthropic 用单条 user 消息聚合 tool_result 块）
        anthropic_results: List[Dict[str, Any]] = []
        for tc in tool_calls:
            name = tc.get("name")
            await _emit(on_event, {"type": "tool_call", "name": name, "arguments": tc.get("arguments")})
            result = await toolset.execute(name, tc.get("arguments"))
            if is_anthropic:
                anthropic_results.append({"type": "tool_result", "tool_use_id": tc.get("id"), "content": result})
            else:
                msgs.append({"role": "tool", "tool_call_id": tc.get("id"), "content": result})
            await _emit(on_event, {"type": "tool_result", "name": name, "result": result})

        if is_anthropic and anthropic_results:
            msgs.append({"role": "user", "content": anthropic_results})

        # Phase 8 代谢：折叠早期工具结果，避免多轮循环上下文膨胀（保留最近的，更早的占位符化）。
        _fold_old_tool_results(msgs, tool_result_budget)

    # 达到上限：最后一次不带工具，强制模型产出最终结果
    logger.info("agentic loop hit max_iterations=%d; final call without tools", max_iterations)
    return await gateway.chat(msgs, provider=provider, temperature=temperature, max_tokens=max_tokens)
