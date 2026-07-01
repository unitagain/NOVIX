/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   UI 核心工具库 - 提供类名合并、基础按钮、输入、卡片组件
 */

import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * 类名合并工具 / Merge Tailwind classes with condition resolution
 *
 * 合并 Tailwind CSS 类名并解决冲突。负责通过 clsx 处理条件类名，
 * 再通过 twMerge 解决 Tailwind 类名冲突（例如多个 bg-* 时保留最后一个）。
 *
 * @param {...any[]} inputs - 任意数量的类名 / Class name inputs (strings, objects, arrays)
 * @returns {string} 合并后的类名字符串 / Merged class name string
 *
 * @example
 * cn('px-4', condition && 'bg-blue-500', ['text-lg', 'font-bold'])
 * // => 'px-4 text-lg font-bold [+ bg-blue-500 if condition true]'
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

/**
 * 基础按钮组件 / Base Button Component
 *
 * 提供多种样式变体和尺寸的按钮。仅负责样式与交互态，
 * 不改变业务语义，使用 forwardRef 支持外层 ref 绑定。
 *
 * @component
 * @param {Object} props - 组件 props
 * @param {string} [props.className] - 额外 CSS 类名 / Additional CSS classes
 * @param {string} [props.variant='default'] - 按钮样式 / Button style variant
 *   - 'default': 填充背景，常用主操作
 *   - 'ghost': 透明背景，用于次级操作
 *   - 'outline': 边框模式，用于可选操作
 *   - 'link': 链接样式，用于文本链接
 * @param {string} [props.size='default'] - 按钮尺寸 / Button size
 *   - 'sm': 小号 (h-9 px-3)
 *   - 'default': 标准 (h-10 px-4)
 *   - 'lg': 大号 (h-11 px-6)
 *   - 'icon': 图标按钮 (h-10 w-10)
 * @param {*} props.* - 其他 HTML button 原生属性 / Standard HTML button attributes
 * @param {React.Ref} ref - 转发给 button 元素的 ref
 * @returns {JSX.Element} 按钮元素
 *
 * @example
 * <Button variant="primary" size="lg" onClick={handleClick}>
 *   提交
 * </Button>
 */
export const Button = React.forwardRef(({ className, variant = 'default', size = 'default', ...props }, ref) => {
  const variants = {
    default:
      'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] border border-[var(--vscode-input-border)] hover:opacity-90 shadow-none',
    ghost: 'bg-transparent text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)]',
    outline:
      'bg-transparent border border-[var(--vscode-input-border)] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)]',
    link: 'text-[var(--vscode-focus-border)] underline-offset-4 hover:underline',
  };

  const sizes = {
    default: 'h-10 px-4',
    sm: 'h-9 px-3',
    lg: 'h-11 px-6',
    icon: 'h-10 w-10',
  };

  return (
    <button
      className={cn(
        'inline-flex items-center justify-center whitespace-nowrap rounded-[4px] text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--vscode-focus-border)] focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 select-none',
        variants[variant],
        sizes[size],
        className,
      )}
      ref={ref}
      {...props}
    />
  );
});
Button.displayName = 'Button';

/**
 * 基础输入框组件 / Base Input Component
 *
 * 提供原生 input 的样式封装。仅负责输入样式与焦点态，
 * 支持所有标准 HTML input 属性。
 *
 * @component
 * @param {Object} props - 组件 props
 * @param {string} [props.className] - 额外 CSS 类名 / Additional CSS classes
 * @param {string} [props.type='text'] - 输入类型 / Input type (text, password, email, etc.)
 * @param {*} props.* - 其他 HTML input 原生属性 / Standard HTML input attributes
 * @param {React.Ref} ref - 转发给 input 元素的 ref
 * @returns {JSX.Element} 输入元素
 *
 * @example
 * <Input type="email" placeholder="输入邮箱" value={email} onChange={handleChange} />
 */
export const Input = React.forwardRef(({ className, type, ...props }, ref) => {
  return (
    <input
      type={type}
      className={cn(
        'flex h-10 w-full rounded-[4px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] px-3 text-sm text-[var(--vscode-fg)] ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-[var(--vscode-fg-subtle)] focus-visible:outline-none focus-visible:border-[var(--vscode-focus-border)] focus-visible:ring-1 focus-visible:ring-[var(--vscode-focus-border)] disabled:cursor-not-allowed disabled:opacity-50 transition-colors',
        className,
      )}
      ref={ref}
      {...props}
    />
  );
});
Input.displayName = 'Input';

/**
 * 基础卡片容器组件 / Base Card Component
 *
 * 提供统一的卡片边界与背景层级。可用于包装任何内容块，
 * 仅负责视觉封装，不涉及内容语义。
 *
 * @component
 * @param {Object} props - 组件 props
 * @param {string} [props.className] - 额外 CSS 类名 / Additional CSS classes
 * @param {React.ReactNode} props.children - 卡片内容 / Card content
 * @param {*} props.* - 其他 HTML div 原生属性 / Standard HTML div attributes
 * @param {React.Ref} ref - 转发给 div 元素的 ref
 * @returns {JSX.Element} 卡片容器
 *
 * @example
 * <Card className="p-6">
 *   <h3>标题</h3>
 *   <p>内容</p>
 * </Card>
 */
export const Card = React.forwardRef(({ className, ...props }, ref) => {
  return (
    <div
      ref={ref}
      className={cn(
        'rounded-[4px] bg-[var(--vscode-bg)] text-[var(--vscode-fg)] border border-[var(--vscode-sidebar-border)] shadow-none',
        className,
      )}
      {...props}
    />
  );
});
Card.displayName = 'Card';
