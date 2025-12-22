"""
Context Compressor / 上下文压缩器
Compresses context to fit within token budget
压缩上下文以适应token预算
"""

from typing import List, Dict, Any
from app.llm_gateway import LLMGateway


class ContextCompressor:
    """
    Compresses context items to reduce token usage
    压缩上下文项以减少token使用
    """
    
    def __init__(self, gateway: LLMGateway):
        """
        Initialize compressor
        
        Args:
            gateway: LLM gateway instance / 大模型网关实例
        """
        self.gateway = gateway
    
    async def compress_summaries(
        self,
        summaries: List[Dict[str, Any]],
        target_length: int = 500
    ) -> str:
        """
        Compress multiple chapter summaries into one
        将多个章节摘要压缩为一个
        
        Args:
            summaries: List of chapter summaries / 章节摘要列表
            target_length: Target length in characters / 目标字符长度
            
        Returns:
            Compressed summary text / 压缩后的摘要文本
        """
        if not summaries:
            return ""
        
        # If already short enough, just concatenate / 如果已经足够短，直接拼接
        total_length = sum(len(s.get("summary", "")) for s in summaries)
        if total_length <= target_length:
            return "\n\n".join([
                f"Chapter {s['chapter']}: {s['title']}\n{s['summary']}"
                for s in summaries
            ])
        
        # Otherwise, use LLM to compress / 否则使用大模型压缩
        summaries_text = "\n\n".join([
            f"Chapter {s['chapter']}: {s['title']}\n{s['summary']}"
            for s in summaries
        ])
        
        messages = [
            {
                "role": "system",
                "content": "You are a text compressor. Compress the given summaries while preserving key information."
            },
            {
                "role": "user",
                "content": f"""Compress these chapter summaries to approximately {target_length} characters:

{summaries_text}

Focus on:
- Key plot developments
- Character changes
- Important facts

Output only the compressed summary, no explanation.
将这些章节摘要压缩到约{target_length}字，聚焦于关键情节、角色变化和重要事实。只输出压缩后的摘要。"""
            }
        ]
        
        response = await self.gateway.chat(messages, provider="openai", temperature=0.3)
        return response["content"]
    
    def truncate_items(
        self,
        items: List[Any],
        max_items: int
    ) -> List[Any]:
        """
        Truncate list to maximum number of items
        截断列表到最大项数
        
        Args:
            items: List of items / 项列表
            max_items: Maximum number of items / 最大项数
            
        Returns:
            Truncated list / 截断后的列表
        """
        if len(items) <= max_items:
            return items
        
        # Keep most recent items / 保留最新的项
        return items[-max_items:]
    
    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text
        估算文本的token数
        
        Args:
            text: Text to estimate / 要估算的文本
            
        Returns:
            Estimated token count / 估算的token数
        """
        # Rough estimation: 1 token ≈ 4 characters for English, 1 token ≈ 1.5 characters for Chinese
        # 粗略估算：英文1 token ≈ 4字符，中文1 token ≈ 1.5字符
        # Use average: 1 token ≈ 2 characters / 使用平均值：1 token ≈ 2字符
        return len(text) // 2
