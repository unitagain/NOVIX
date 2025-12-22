"""
Token Budgeter / Token预算控制器
Manages token allocation for different context components
管理不同上下文组件的token分配
"""

from typing import Dict
from app.config import config


class TokenBudgeter:
    """
    Manages token budget allocation
    管理token预算分配
    """
    
    def __init__(self):
        """Initialize budgeter with configuration / 使用配置初始化预算控制器"""
        budget_config = config.get("context_budget", {})
        self.total_tokens = budget_config.get("total_tokens", 128000)
        
        # Budget allocations / 预算分配
        self.allocations = {
            "system_rules": budget_config.get("system_rules", 0.05),
            "cards": budget_config.get("cards", 0.15),
            "canon": budget_config.get("canon", 0.10),
            "summaries": budget_config.get("summaries", 0.20),
            "current_draft": budget_config.get("current_draft", 0.30),
            "output_reserve": budget_config.get("output_reserve", 0.20)
        }
    
    def get_budget(self, component: str) -> int:
        """
        Get token budget for a component
        获取组件的token预算
        
        Args:
            component: Component name / 组件名称
            
        Returns:
            Allocated token count / 分配的token数
        """
        allocation = self.allocations.get(component, 0.0)
        return int(self.total_tokens * allocation)
    
    def get_all_budgets(self) -> Dict[str, int]:
        """
        Get all budget allocations
        获取所有预算分配
        
        Returns:
            Dictionary of component budgets / 组件预算字典
        """
        return {
            component: self.get_budget(component)
            for component in self.allocations.keys()
        }
    
    def calculate_usage(self, text: str) -> int:
        """
        Calculate token usage for text
        计算文本的token使用量
        
        Args:
            text: Text to calculate / 要计算的文本
            
        Returns:
            Estimated token count / 估算的token数
        """
        # Rough estimation: 1 token ≈ 2 characters
        # 粗略估算：1 token ≈ 2字符
        return len(text) // 2
    
    def fits_budget(self, text: str, component: str) -> bool:
        """
        Check if text fits within component budget
        检查文本是否在组件预算内
        
        Args:
            text: Text to check / 要检查的文本
            component: Component name / 组件名称
            
        Returns:
            True if within budget / 是否在预算内
        """
        usage = self.calculate_usage(text)
        budget = self.get_budget(component)
        return usage <= budget
    
    def get_overflow(self, text: str, component: str) -> int:
        """
        Get amount of tokens over budget
        获取超出预算的token数
        
        Args:
            text: Text to check / 要检查的文本
            component: Component name / 组件名称
            
        Returns:
            Overflow amount (0 if within budget) / 超出量（预算内则为0）
        """
        usage = self.calculate_usage(text)
        budget = self.get_budget(component)
        return max(0, usage - budget)
