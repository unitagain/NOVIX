/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   同人导入面板 - 引导用户使用中间栏完成 Wiki 页面提取和卡片生成
 *   Fanfiction panel for guiding users through wiki page extraction and card generation.
 */

import { useLocale } from '../../../i18n';
import { SidebarPanelHeader } from '../SidebarPanelHeader';

/**
 * 同人导入面板 - Wiki 导入工作流的指引面板
 *
 * Informational panel that guides users to complete fanfiction import workflow
 * in the center column (search terms, extract entries, edit cards).
 *
 * @component
 * @example
 * return (
 *   <FanfictionPanel />
 * )
 *
 * @returns {JSX.Element} 同人导入面板 / Fanfiction panel element
 */
const FanfictionPanel = () => {
  const { t } = useLocale();
  return (
    <div className="anti-theme flex flex-col h-full bg-[var(--vscode-bg)] text-[var(--vscode-fg)]">
      {/* 面板头部 / Panel header */}
      <SidebarPanelHeader title={t('activityBar.fanfiction')} />
      {/* 指引说明 / Usage guide */}
      <div className="flex-1 p-4 text-xs text-[var(--vscode-fg-subtle)] leading-relaxed">
        {t('panels.fanfiction.hint')}
      </div>
    </div>
  );
};

export default FanfictionPanel;
