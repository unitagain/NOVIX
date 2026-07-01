/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   错误边界组件 - 捕获 React 组件树的错误并显示降级界面
 *   Error boundary component for catching React errors with fallback UI.
 */

import React from 'react';
import logger from '../utils/logger';
import { t } from '../i18n';

/**
 * 错误边界组件 - React 组件错误捕获与降级处理
 *
 * Error boundary class component that catches JavaScript errors anywhere
 * in the child component tree and displays a fallback UI with error details
 * and recovery options.
 *
 * @component
 * @example
 * return (
 *   <ErrorBoundary>
 *     <YourComponent />
 *   </ErrorBoundary>
 * )
 *
 * @param {Object} props - Component props
 * @param {React.ReactNode} [props.children] - 子组件 / Child components to protect
 * @returns {JSX.Element} 错误边界或子组件 / Error boundary or child components
 */
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
  }

  /**
   * 当子组件抛出错误时更新状态以展示降级界面
   * Update state when child component throws error to show fallback UI
   */
  static getDerivedStateFromError(_error) {
    return { hasError: true };
  }

  /**
   * 记录错误详情到日志系统
   * Log error details to logging system for debugging
   */
  componentDidCatch(error, errorInfo) {
    logger.error('[ErrorBoundary] Component error:', error, errorInfo);

    this.setState({
      error,
      errorInfo,
    });

    // 可选：上报监控服务（如 Sentry）
    // Optional: Report to monitoring service (e.g., Sentry)
    // reportErrorToService(error, errorInfo);
  }

  /**
   * 处理页面重新加载 / Handle page reload
   */
  handleReload = () => {
    window.location.reload();
  };

  /**
   * 重置错误状态 / Reset error state
   */
  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
    });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-[var(--vscode-bg)] p-4 text-[var(--vscode-fg)]">
          <div className="max-w-md w-full bg-[var(--vscode-bg)] rounded-[6px] shadow-none border border-[var(--vscode-sidebar-border)] p-8 text-center">
            <div className="mb-6">
              <div className="w-16 h-16 mx-auto bg-red-50 rounded-full flex items-center justify-center mb-4 border border-red-100">
                <svg className="w-8 h-8 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                  />
                </svg>
              </div>
              <h1 className="text-2xl font-bold text-[var(--vscode-fg)] mb-2">{t('errorBoundary.title')}</h1>
              <p className="text-[var(--vscode-fg-subtle)] text-sm mb-4">{t('errorBoundary.hint')}</p>
            </div>

            {/* 开发环境：展示错误详情 */}
            {process.env.NODE_ENV === 'development' && this.state.error && (
              <details className="mb-6 text-left">
                <summary className="cursor-pointer text-sm font-medium text-[var(--vscode-fg)] hover:text-[var(--vscode-fg)] mb-2">
                  {t('errorBoundary.detail')}
                </summary>
                <div className="bg-[var(--vscode-input-bg)] rounded-[6px] p-3 text-xs font-mono text-[var(--vscode-fg)] overflow-auto max-h-40 border border-[var(--vscode-sidebar-border)]">
                  <p className="font-bold mb-1">{t('common.error')}:</p>
                  <p className="mb-2">{this.state.error.toString()}</p>
                  {this.state.errorInfo && (
                    <>
                      <p className="font-bold mb-1">Stack:</p>
                      <pre className="whitespace-pre-wrap">{this.state.errorInfo.componentStack}</pre>
                    </>
                  )}
                </div>
              </details>
            )}

            <div className="flex gap-3 justify-center">
              <button
                onClick={this.handleReset}
                className="px-4 py-2 bg-[var(--vscode-list-hover)] hover:opacity-90 text-[var(--vscode-fg)] rounded-[6px] transition-colors text-sm font-medium"
              >
                {t('errorBoundary.retry')}
              </button>
              <button
                onClick={this.handleReload}
                className="px-4 py-2 bg-[var(--vscode-list-active)] hover:opacity-90 text-[var(--vscode-list-active-fg)] rounded-[6px] transition-colors text-sm font-medium"
              >
                {t('common.retry')}
              </button>
            </div>

            <p className="mt-6 text-xs text-[var(--vscode-fg-subtle)]">{t('error.retryLater')}</p>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
