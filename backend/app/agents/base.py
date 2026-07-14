# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  Agent 基类，提供所有智能体的通用功能和 LLM 调用接口。
  Base Agent class providing common functionality and LLM interface for all agents.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from app.llm_gateway import LLMGateway
from app.storage import CardStorage, CanonStorage, DraftStorage
from app.context_engine.trace_collector import trace_collector, TraceEventType
from app.context_engine.token_accounting import count_provider_payload, count_text_tokens
from app.context_engine.token_counter import get_model_context_window
from app.prompts import base_agent_system_prompt, format_context_message
from app.utils.llm_output import parse_json_payload
from app.context_engine.turn_scope import current_turn_scope
from app.utils.json_metrics import record_json_result
from app.utils.logger import get_logger
from app.error_contract import record_degradation

logger = get_logger(__name__)

class BaseAgent(ABC):
    """
    所有智能体的抽象基类 - 提供通用功能

    Abstract base class for all agents. Provides common LLM interface,
    storage access, and tracing capabilities for derived Agent implementations.

    Attributes:
        gateway: LLM gateway instance for model calls.
        card_storage: Storage instance for character/world cards.
        canon_storage: Storage instance for canonical facts.
        draft_storage: Storage instance for draft chapters.
    """

    def __init__(
        self,
        gateway: LLMGateway,
        card_storage: CardStorage,
        canon_storage: CanonStorage,
        draft_storage: DraftStorage,
        language: str = "zh",
    ):
        """
        初始化智能体 - 注入所有依赖

        Initialize agent with storage and LLM gateway dependencies.

        Args:
            gateway: LLM gateway instance for model calls.
            card_storage: Card storage instance for character/world cards.
            canon_storage: Canon storage instance for facts and settings.
            draft_storage: Draft storage instance for chapters and summaries.
            language: Writing language ("zh" for Chinese, "en" for English).
        """
        self.gateway = gateway
        self.card_storage = card_storage
        self.canon_storage = canon_storage
        self.draft_storage = draft_storage
        self.language = language

    @abstractmethod
    async def execute(self, project_id: str, chapter: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行智能体的核心任务 - 由子类实现

        Execute the agent's main task. Must be implemented by subclasses.

        Args:
            project_id: Unique project identifier.
            chapter: Chapter ID for context.
            context: Context dictionary with task-specific parameters.

        Returns:
            Dictionary with execution results (success status, output data, etc).
        """
        pass

    @abstractmethod
    def get_agent_name(self) -> str:
        """
        获取智能体标识名 - 用于配置和日志

        Get the agent's unique name identifier for configuration and logging.

        Returns:
            Agent name string (e.g., "writer", "editor", "archivist").
        """
        pass

    def get_system_prompt(self) -> str:
        """
        获取系统提示词 - 根据智能体类型生成

        Get the system prompt for this agent instance.

        Returns:
            System prompt string customized for the agent type.
        """
        return base_agent_system_prompt(self.get_agent_name(), language=self.language)

    async def call_llm(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        config_agent: Optional[str] = None,
        return_meta: bool = False,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        调用大模型 - 支持智能体特定配置和流量追踪

        Call LLM with agent-specific configuration from gateway settings.
        Automatically records token usage and latency metrics for monitoring.

        Args:
            messages: List of message dicts with "role" and "content" keys.
            temperature: Temperature override for this call (uses agent default if None).
            max_tokens: Maximum output tokens for this call.
            config_agent: Override agent name for configuration lookup.
            return_meta: If True, return full response dict including metadata.

        Returns:
            If return_meta=False: LLM response content string.
            If return_meta=True: Full response dict with content, usage, model info.
        """
        agent_name = config_agent or self.get_agent_name()
        provider = self.gateway.get_provider_for_agent(agent_name)

        if temperature is None:
            temperature = self.gateway.get_temperature_for_agent(agent_name)

        scope = current_turn_scope()
        if scope is not None and scope.source_closure_required:
            scope.register_provider_payload(
                messages,
                source_prefix=f"agent.{agent_name}.final",
                selection_reason="base_agent_final_request",
                artifact_ref=f"{self.__class__.__module__}.{self.__class__.__name__}.call_llm",
            )
        response = await self.gateway.chat(
            messages=messages,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        # ============================================================================
        # 记录 LLM 请求和统计 / Record LLM request and update metrics
        # ============================================================================
        # Track token usage in three categories for context monitor:
        # - guiding: system prompt tokens
        # - informational: context tokens
        # - actionable: prompt instruction + completion tokens
        try:
            usage = response.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            # Heuristic breakdown for Context Monitor
            # Guiding ~ 10% of prompt (System prompt)
            # Informational ~ 80% of prompt (Context)
            # Actionable ~ 10% of prompt + 100% of completion
            guiding = int(prompt_tokens * 0.1)
            informational = int(prompt_tokens * 0.8)
            actionable = prompt_tokens - guiding - informational + completion_tokens

            # 1. Update global stats (for Gauge)
            await trace_collector.update_token_stats(
                total_delta=total_tokens,
                breakdown_delta={"guiding": guiding, "informational": informational, "actionable": actionable},
            )

            # 2. Record detailed event (for Timeline)
            await trace_collector.record(
                TraceEventType.LLM_REQUEST,
                self.get_agent_name(),
                {
                    "model": response.get("model", "unknown"),
                    "provider": response.get("provider", "unknown"),
                    "config_agent": agent_name,
                    "tokens": {"total": total_tokens, "prompt": prompt_tokens, "completion": completion_tokens},
                    "latency_ms": int(response.get("elapsed_time", 0) * 1000),
                },
            )

        except Exception as e:
            logger.warning("TRACE ERROR (LLM): %s", e)

        if return_meta:
            return response
        return response["content"]

    async def call_llm_json(
        self,
        messages: List[Dict[str, str]],
        expected_type: Optional[type] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        config_agent: Optional[str] = None,
    ) -> Any:
        """结构化 JSON 调用（Phase 9）：加 response_format → 解析 → 记录成功率指标 → 返回 (data, err, raw)。

        能力门控：response_format={"type":"json_object"} 对 OpenAI 兼容族强制 JSON mode；
        Anthropic 忽略→靠 prompt 引导。parse_json_payload 始终保底（fallback），故对所有 provider 健壮。

        Returns:
            (data, err, raw)：err 为空串表示成功；raw 为原始文本（供重试/调试）。
        """
        raw = await self.call_llm(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            config_agent=config_agent,
            response_format={"type": "json_object"},
        )
        raw_text = raw if isinstance(raw, str) else str(raw or "")
        data, err = parse_json_payload(raw_text, expected_type=expected_type)
        try:
            record_json_result(config_agent or self.get_agent_name(), success=not err)
        except Exception as exc:
            record_degradation("agent_json_metrics", exc)
        if err:
            logger.warning("JSON 结构化解析失败 [%s]: %s", self.get_agent_name(), err)
        return data, err, raw_text

    async def call_llm_stream(
        self, messages: List[Dict[str, str]], temperature: Optional[float] = None, *, on_thinking=None
    ):
        """
        流式输出大模型响应 - 逐token返回，适合前端实时显示

        Stream LLM response token by token. Yields tokens as they arrive.
        Falls back to non-streaming if upstream chunked read fails.

        Args:
            messages: List of message dicts with "role" and "content" keys.
            temperature: Temperature override (uses agent default if None).
            on_thinking: Optional async callback for reasoning/thinking deltas (default None).

        Yields:
            Token strings as they arrive from LLM.
        """
        agent_name = self.get_agent_name()
        provider = self.gateway.get_provider_for_agent(agent_name)

        if temperature is None:
            temperature = self.gateway.get_temperature_for_agent(agent_name)

        scope = current_turn_scope()
        if scope is not None and scope.source_closure_required:
            scope.register_provider_payload(
                messages,
                source_prefix=f"agent.{agent_name}.stream",
                selection_reason="base_agent_stream_request",
                artifact_ref=f"{self.__class__.__module__}.{self.__class__.__name__}.call_llm_stream",
            )
        has_chunk = False
        try:
            async for chunk in self.gateway.stream_chat(
                messages=messages, provider=provider, temperature=temperature, on_thinking=on_thinking
            ):
                if chunk:
                    has_chunk = True
                yield chunk
        except Exception:
            if has_chunk:
                raise
            # Fallback to non-streaming to avoid hard failures on upstream chunked reads.
            response = await self.gateway.chat(messages=messages, provider=provider, temperature=temperature)
            yield response.get("content", "")

    def build_messages(
        self, system_prompt: str, user_prompt: str, context_items: Optional[List[str]] = None
    ) -> List[Dict[str, str]]:
        """
        构建发送给大模型的消息列表 - 带 token 安全网

        Build message list for LLM API calls with automatic token safety check.
        If the assembled messages exceed the model's input limit, context_items
        are trimmed from the end (lowest priority) until within budget.

        Args:
            system_prompt: System message content (instructions, constraints).
            user_prompt: Main user message content (question, task).
            context_items: Optional list of context items to insert as user message.

        Returns:
            List of message dicts with "role" and "content" keys in order:
            1. System message
            2. Context message (if provided, may be trimmed)
            3. User message
        """
        try:
            profile = dict(self.gateway.get_profile_for_agent(self.get_agent_name()) or {})
        except Exception:
            profile = {}
        provider_name = str(profile.get("provider") or profile.get("id") or "")
        model_name = str(profile.get("model") or "")
        fixed_tokens = count_provider_payload(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            provider=provider_name,
            model=model_name,
        ).upper_bound_tokens

        # 获取模型输入 token 上限
        input_limit = self._get_input_token_limit()

        # 裁剪 context_items 使总量不超限
        trimmed_items = self._trim_context_items(
            context_items or [],
            max_context_tokens=input_limit - fixed_tokens,
            provider=provider_name,
            model=model_name,
        )

        messages = [{"role": "system", "content": system_prompt}]

        if trimmed_items:
            messages.append({"role": "user", "content": format_context_message(trimmed_items, language=self.language)})

        messages.append({"role": "user", "content": user_prompt})

        scope = current_turn_scope()
        if scope is not None and scope.source_closure_required:
            agent_name = self.get_agent_name()
            scope.register_source_content(
                source_id=f"prompt.{agent_name}.system",
                asset_type="prompt",
                content=system_prompt,
                selection_reason="agent_system_prompt",
                artifact_ref=f"{self.__class__.__module__}.{self.__class__.__name__}.build_messages",
            )
            for index, item in enumerate(trimmed_items):
                scope.register_source_content(
                    source_id=f"context.{agent_name}.{index}",
                    asset_type="assembled_context",
                    content=item,
                    selection_reason="token_budget_selected_context",
                    artifact_ref=f"agent_context:{agent_name}",
                )
            scope.register_source_content(
                source_id=f"prompt.{agent_name}.user",
                asset_type="user_message",
                content=user_prompt,
                selection_reason="agent_user_prompt",
                artifact_ref=f"{self.__class__.__module__}.{self.__class__.__name__}.build_messages",
            )
            scope.register_provider_payload(
                messages,
                source_prefix=f"agent.{agent_name}",
                selection_reason="base_agent_message_assembly",
                artifact_ref=f"{self.__class__.__module__}.{self.__class__.__name__}.build_messages",
            )

        return messages

    def _get_input_token_limit(self) -> int:
        """
        获取当前 Agent 使用的模型的输入 token 上限。

        Get the input token limit for this agent's assigned model.
        input_limit = context_window - max_output_tokens

        Returns:
            Maximum input tokens allowed.
        """
        agent_name = self.get_agent_name()
        try:
            profile = self.gateway.get_profile_for_agent(agent_name)
        except Exception:
            profile = None

        if profile:
            # 优先使用用户显式配置的 max_context_tokens
            context_window = profile.get("max_context_tokens") or 0
            if not context_window:
                model_name = profile.get("model") or ""
                context_window = get_model_context_window(model_name)
            max_output = profile.get("max_tokens") or 8000
        else:
            context_window = 32000
            max_output = 8000

        return max(context_window - max_output, 4096)

    @staticmethod
    def _trim_context_items(
        context_items: List[str],
        max_context_tokens: int,
        *,
        provider: str = "",
        model: str = "",
    ) -> List[str]:
        """
        按 token 预算裁剪 context_items，从末尾（低优先级）开始移除。

        Trim context_items to fit within max_context_tokens.
        Items at the end of the list are considered lower priority and removed first.

        Args:
            context_items: Context items ordered by priority (high → low).
            max_context_tokens: Maximum tokens allowed for all context items combined.

        Returns:
            Trimmed list of context items.
        """
        if max_context_tokens <= 0:
            if context_items:
                logger.warning(
                    "Context budget exhausted (%d tokens), dropping all %d context items",
                    max_context_tokens,
                    len(context_items),
                )
            return []

        if not context_items:
            return []

        # 快速路径：计算全量 token，如果不超限直接返回
        item_tokens = [
            count_text_tokens(str(item), provider=provider, model=model).upper_bound_tokens for item in context_items
        ]
        total = sum(item_tokens)
        if total <= max_context_tokens:
            return list(context_items)

        # 超限：从末尾开始逐项移除
        logger.warning(
            "Context tokens (%d) exceed limit (%d), trimming from end",
            total,
            max_context_tokens,
        )

        kept: List[str] = []
        running = 0
        for item, tokens in zip(context_items, item_tokens):
            if running + tokens > max_context_tokens:
                break
            kept.append(item)
            running += tokens

        dropped = len(context_items) - len(kept)
        if dropped > 0:
            logger.warning(
                "Dropped %d/%d context items to fit budget (%d/%d tokens)",
                dropped,
                len(context_items),
                running,
                max_context_tokens,
            )

        return kept
