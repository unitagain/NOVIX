# -*- coding: utf-8 -*-
"""
文枢 WenShape - 深度上下文感知的智能体小说创作系统
WenShape - Deep Context-Aware Agent-Based Novel Writing System

Copyright © 2025-2026 WenShape Team
License: PolyForm Noncommercial License 1.0.0

模块说明 / Module Description:
  应用级异常层次 - 定义业务逻辑层异常的完整继承树
  Application-level Exception Hierarchy - Business logic exception definitions.
"""


from app.error_contract import DomainError, ErrorCategory


class WenShapeError(DomainError):
    """
    WenShape 业务错误的基类

    Base exception for all WenShape business errors.

    所有应用级异常都应继承此类，以便于统一错误处理。
    All application-level exceptions should inherit from this class for
    consistent error handling and propagation.
    """


class StorageError(WenShapeError):
    """
    存储操作失败异常

    Raised when a storage operation fails (read/write/delete).

    抛出时机：
    - 文件读取失败 / File read failed
    - 文件写入失败 / File write failed
    - 文件删除失败 / File deletion failed
    - 目录操作失败 / Directory operation failed
    - 数据格式错误 / Invalid data format
    """

    default_code = "storage_error"
    default_category = ErrorCategory.STORAGE
    default_retryable = True


class LLMError(WenShapeError):
    """
    LLM调用失败异常

    Raised when an LLM call fails (timeout, rate limit, bad response).

    抛出时机：
    - API调用超时 / API timeout
    - 速率限制 / Rate limit exceeded
    - 认证失败 / Authentication failed
    - 响应格式无效 / Invalid response format
    - 模型不可用 / Model unavailable
    """

    default_code = "llm_error"
    default_category = ErrorCategory.PROVIDER


class AgentError(WenShapeError):
    """
    Agent业务逻辑错误异常

    Raised when an agent encounters a business-logic error.

    抛出时机：
    - 任务执行失败 / Task execution failed
    - 上下文不足 / Insufficient context
    - 状态转换无效 / Invalid state transition
    - 输入验证失败 / Input validation failed
    """

    default_code = "agent_error"
    default_category = ErrorCategory.INTERNAL


class ValidationError(WenShapeError):
    """
    数据验证失败异常

    Raised when input validation fails beyond Pydantic checks.

    抛出时机：
    - 业务规则校验失败 / Business rule validation failed
    - 数据一致性检查失败 / Data consistency check failed
    - 自定义验证逻辑失败 / Custom validation logic failed

    Note: Pydantic ValidationError 由框架自动处理，不需要手动抛出此异常。
    """

    default_code = "validation_error"
    default_category = ErrorCategory.VALIDATION
