# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  上下文预算管理器 - 动态token预算分配
  Context Budget Manager - Dynamic context budget allocation based on model capabilities
  Manages token allocation across different content categories (cards, canon, summaries, output).
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional
from app.config import config
from app.context_engine.token_accounting import count_text_tokens, token_estimator_calibration
from app.context_engine.token_counter import get_model_context_window
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BudgetAllocation:
    """
    预算分配结果 / Budget allocation result

    Represents how tokens are allocated across different content categories
    for a single LLM call.

    Attributes:
        total_available (int): 总可用token数 / Total available tokens.
        system_rules (int): 系统规则预算 / Budget for system prompts and rules.
        cards (int): 卡片预算 / Budget for character/world cards.
        canon (int): 事实表预算 / Budget for canonical facts.
        summaries (int): 摘要预算 / Budget for chapter summaries.
        current_draft (int): 当前草稿预算 / Budget for draft content.
        output_reserve (int): 输出预留 / Reserved tokens for model output.
        remaining (int): 剩余可用token / Remaining available tokens (default 0).
    """

    total_available: int  # 总可用 tokens
    system_rules: int  # 系统规则预算
    cards: int  # 卡片预算
    canon: int  # 事实表预算
    summaries: int  # 摘要预算
    current_draft: int  # 当前草稿预算
    output_reserve: int  # 输出预留
    remaining: int = 0  # 剩余可用

    def to_dict(self) -> Dict[str, int]:
        """转换为字典 / Convert to dictionary format."""
        return {
            "total_available": self.total_available,
            "system_rules": self.system_rules,
            "cards": self.cards,
            "canon": self.canon,
            "summaries": self.summaries,
            "current_draft": self.current_draft,
            "output_reserve": self.output_reserve,
            "remaining": self.remaining,
        }


@dataclass
class BudgetUsage:
    """
    预算使用情况 / Budget usage tracking

    Tracks how much of an allocated budget has been used for a specific category.

    Attributes:
        category (str): 类别名称 / Category name.
        allocated (int): 分配的token数 / Allocated tokens for this category.
        used (int): 已使用的token数 / Tokens used so far.
        items_count (int): 项目数量 / Number of items in this category (default 0).
    """

    category: str
    allocated: int
    used: int
    items_count: int = 0

    @property
    def remaining(self) -> int:
        """剩余可用token / Remaining tokens in this category."""
        return max(0, self.allocated - self.used)

    @property
    def usage_ratio(self) -> float:
        """使用比率 / Usage ratio (0.0 - 1.0)."""
        if self.allocated == 0:
            return 0.0
        return self.used / self.allocated


class ContextBudgetManager:
    """
    上下文预算管理器 / Context Budget Manager

    根据用户配置的模型动态计算和分配上下文预算
    Dynamically calculates and allocates context budgets based on model capabilities
    and configured allocation ratios. Tracks usage and provides allocation info.

    Attributes:
        model_name (Optional[str]): 模型名称，用于确定上下文窗口大小 / Model name.
        max_output_tokens (int): 最大输出token数 / Max tokens to generate.
        ratios (Dict[str, float]): 各类别的预算比例 / Budget ratios for each category.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        max_output_tokens: int = 8000,
        max_context_tokens: int = 0,
        provider: Optional[str] = None,
    ):
        """
        初始化预算管理器 / Initialize the budget manager.

        Args:
            model_name: 模型名称，用于确定上下文窗口大小 / Model name (e.g., 'gpt-4o').
            max_output_tokens: 最大输出token数 / Maximum tokens the model will generate.
            max_context_tokens: 用户显式配置的上下文窗口大小，0 表示自动推断 /
                User-configured context window override. 0 means auto-detect from model name.
            provider: 提供商类型，用于 token 校准 / Provider type for token calibration.
        """
        self.model_name = model_name
        self.max_output_tokens = max_output_tokens
        self.provider = str(provider or "").lower()

        # 从 config 加载预算比例
        # Load budget ratios from config.yaml
        budget_config = config.get("context_budget", {})
        self.ratios = {
            "system_rules": budget_config.get("system_rules", 0.05),
            "cards": budget_config.get("cards", 0.15),
            "canon": budget_config.get("canon", 0.10),
            "summaries": budget_config.get("summaries", 0.20),
            "current_draft": budget_config.get("current_draft", 0.30),
            "output_reserve": budget_config.get("output_reserve", 0.20),
        }

        # 上下文窗口大小：优先使用显式配置，否则从模型名推断
        # Context window: prefer explicit config, fallback to model name inference
        if max_context_tokens and max_context_tokens > 0:
            self._context_window = max_context_tokens
        elif model_name:
            self._context_window = get_model_context_window(model_name)
        else:
            self._context_window = 128000

        self._total_budget = self._calculate_total_budget()

        # 使用追踪
        # Track usage by category
        self._usage: Dict[str, BudgetUsage] = {}

    def _calculate_total_budget(self) -> int:
        """计算总可用预算（扣除输出预留，并按提供商校准系数缩减） / Calculate total available budget."""
        output_reserve = int(self._context_window * self.ratios["output_reserve"])
        # 确保输出预留至少能容纳 max_output_tokens
        output_reserve = max(output_reserve, self.max_output_tokens)
        raw_budget = self._context_window - output_reserve
        return int(raw_budget)

    def get_allocation(self) -> BudgetAllocation:
        """获取预算分配"""
        total = self._total_budget

        # 按比例分配（不含 output_reserve，因为已经扣除）
        input_ratios = {k: v for k, v in self.ratios.items() if k != "output_reserve"}
        ratio_sum = sum(input_ratios.values())

        # 归一化比例
        normalized = {k: v / ratio_sum for k, v in input_ratios.items()}

        allocation = BudgetAllocation(
            total_available=total,
            system_rules=int(total * normalized.get("system_rules", 0.0625)),
            cards=int(total * normalized.get("cards", 0.1875)),
            canon=int(total * normalized.get("canon", 0.125)),
            summaries=int(total * normalized.get("summaries", 0.25)),
            current_draft=int(total * normalized.get("current_draft", 0.375)),
            output_reserve=self._context_window - total,
        )

        # 计算剩余
        used = (
            allocation.system_rules
            + allocation.cards
            + allocation.canon
            + allocation.summaries
            + allocation.current_draft
        )
        allocation.remaining = total - used

        return allocation

    def allocate_for_agent(self, agent_name: str) -> Dict[str, int]:
        """
        为特定 Agent 分配预算

        不同 Agent 有不同的预算需求：
        - archivist: 需要更多 cards 和 canon
        - writer: 需要更多 current_draft 和 summaries
        - editor: 需要更多 current_draft
        """
        base = self.get_allocation()

        # Agent 特定调整
        adjustments = {
            "archivist": {
                "cards": 1.2,
                "canon": 1.3,
                "summaries": 0.8,
                "current_draft": 0.7,
            },
            "writer": {
                "cards": 1.0,
                "canon": 1.0,
                "summaries": 1.2,
                "current_draft": 1.1,
            },
            "editor": {
                "cards": 0.8,
                "canon": 0.8,
                "summaries": 0.9,
                "current_draft": 1.3,
            },
        }

        adj = adjustments.get(agent_name, {})

        return {
            "system_rules": base.system_rules,
            "cards": int(base.cards * adj.get("cards", 1.0)),
            "canon": int(base.canon * adj.get("canon", 1.0)),
            "summaries": int(base.summaries * adj.get("summaries", 1.0)),
            "current_draft": int(base.current_draft * adj.get("current_draft", 1.0)),
            "total_available": base.total_available,
            "output_reserve": base.output_reserve,
        }

    def track_usage(self, category: str, content: str, items_count: int = 1) -> BudgetUsage:
        """
        追踪预算使用

        Args:
            category: 预算类别
            content: 内容
            items_count: 项目数量

        Returns:
            使用情况
        """
        tokens = count_text_tokens(content, provider=self.provider, model=str(self.model_name or "")).upper_bound_tokens
        allocation = self.get_allocation()

        allocated = getattr(allocation, category, 0)

        if category not in self._usage:
            self._usage[category] = BudgetUsage(
                category=category,
                allocated=allocated,
                used=0,
                items_count=0,
            )

        self._usage[category].used += tokens
        self._usage[category].items_count += items_count

        return self._usage[category]

    def get_usage_summary(self) -> Dict[str, Any]:
        """获取使用情况摘要"""
        total_used = sum(u.used for u in self._usage.values())

        return {
            "model": self.model_name,
            "provider": self.provider,
            "context_window": self._context_window,
            "total_budget": self._total_budget,
            "total_used": total_used,
            "usage_ratio": total_used / self._total_budget if self._total_budget > 0 else 0,
            "categories": {
                cat: {
                    "allocated": usage.allocated,
                    "used": usage.used,
                    "remaining": usage.remaining,
                    "items": usage.items_count,
                    "ratio": usage.usage_ratio,
                }
                for cat, usage in self._usage.items()
            },
            "estimator_calibration": token_estimator_calibration(self.provider, str(self.model_name or "")),
        }

    def can_fit(self, content: str, category: str) -> bool:
        """检查内容是否能放入指定类别的预算"""
        tokens = count_text_tokens(content, provider=self.provider, model=str(self.model_name or "")).upper_bound_tokens
        allocation = self.get_allocation()
        allocated = getattr(allocation, category, 0)

        current_usage = self._usage.get(category)
        used = current_usage.used if current_usage else 0

        return (used + tokens) <= allocated

    def get_remaining(self, category: str) -> int:
        """获取指定类别的剩余预算"""
        allocation = self.get_allocation()
        allocated = getattr(allocation, category, 0)

        current_usage = self._usage.get(category)
        used = current_usage.used if current_usage else 0

        return max(0, allocated - used)

    @property
    def context_window(self) -> int:
        """获取上下文窗口大小"""
        return self._context_window

    @property
    def total_budget(self) -> int:
        """获取总预算"""
        return self._total_budget


def create_budget_manager(
    profile: Optional[Dict[str, Any]] = None,
    model_name: Optional[str] = None,
    max_output_tokens: int = 8000,
) -> ContextBudgetManager:
    """
    创建预算管理器的工厂函数

    Args:
        profile: LLM 配置 profile（可包含 max_context_tokens 覆盖上下文窗口推断）
        model_name: 模型名称（如果没有 profile）
        max_output_tokens: 最大输出 tokens

    Returns:
        ContextBudgetManager 实例
    """
    if profile:
        model = profile.get("model", model_name)
        max_tokens = profile.get("max_tokens", max_output_tokens)
        max_context = profile.get("max_context_tokens") or 0
        provider_type = profile.get("provider")
    else:
        model = model_name
        max_tokens = max_output_tokens
        max_context = 0
        provider_type = None

    return ContextBudgetManager(
        model_name=model,
        max_output_tokens=max_tokens,
        max_context_tokens=max_context,
        provider=provider_type,
    )
