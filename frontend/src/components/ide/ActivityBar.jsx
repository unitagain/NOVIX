/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 */

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useIDE } from '../../context/IDEContext';
import { Files, BookOpen, Bot, Library, Info, X, Lightbulb } from 'lucide-react';
import { cn } from '../../components/ui/core';
import { useLocale } from '../../i18n';

/**
 * ActivityBar - 左侧活动栏导航
 *
 * IDE 左侧固定活动栏，提供多个面板的快速切换：资源管理器、事实全典、卡片管理等。
 * 支持面板切换、版权声明显示等功能。
 *
 * @component
 * @returns {JSX.Element} 活动栏和相关对话框
 *
 * 功能：
 * - 五个主要面板快速导航
 * - 版权声明和许可证信息弹窗
 * - 流畅的动画过渡
 */
export function ActivityBar() {
  const { state, dispatch } = useIDE();
  const { t } = useLocale();
  const [showLegalNotice, setShowLegalNotice] = useState(false);
  const [showFullLicense, setShowFullLicense] = useState(false);

  // ========================================================================
  // 活动项配置 / Activity Items Configuration
  // ========================================================================
  const icons = [
    { id: 'explorer', icon: Files, label: t('activityBar.explorer') },
    { id: 'facts', icon: Lightbulb, label: t('activityBar.facts') },
    { id: 'cards', icon: BookOpen, label: t('activityBar.cards') },
    { id: 'fanfiction', icon: Library, label: t('activityBar.fanfiction') },
    { id: 'agents', icon: Bot, label: t('activityBar.agents') },
  ];

  // 计算当前活跃按钮的位置（用于动画）
  const activeIndex = icons.findIndex((item) => item.id === state.activeActivity);

  return (
    <>
      <div className="w-10 flex flex-col items-center py-2 bg-[var(--vscode-desktop)] z-30">
        {/* 活动项按钮容器 */}
        <div className="flex-1 space-y-1 relative">
          {/* 活跃指示背景 - Animated active indicator background */}
          {activeIndex !== -1 && state.sidePanelVisible && (
            <motion.div
              className="absolute left-0.5 w-9 h-10 bg-[var(--vscode-list-hover)] rounded-[6px]"
              initial={false}
              animate={{ top: `${activeIndex * 44 + 4}px` }}
              transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            />
          )}

          {/* 活动项列表 - Activity Items */}
          {icons.map((item) => (
            <ActivityItem
              key={item.id}
              icon={item.icon}
              label={item.label}
              isActive={state.activeActivity === item.id && state.sidePanelVisible}
              onClick={() => dispatch({ type: 'SET_ACTIVE_PANEL', payload: item.id })}
            />
          ))}
        </div>

        {/* ========================================================================
            版权声明按钮 / Legal Notice Button
            ======================================================================== */}
        <button
          onClick={() => {
            setShowFullLicense(false);
            setShowLegalNotice(true);
          }}
          title={t('activityBar.legalNotice')}
          className={cn(
            'w-9 h-10 flex items-center justify-center rounded-xl transition-all duration-200 group relative z-10',
            'text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)]',
          )}
        >
          <Info size={20} strokeWidth={2} />
        </button>
      </div>

      {/* ========================================================================
          版权声明对话框 / Legal Notice Dialog
          ======================================================================== */}
      <AnimatePresence>
        {showLegalNotice && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center p-4 anti-theme"
            onClick={() => setShowLegalNotice(false)}
          >
            <motion.div
              initial={{ scale: 0.95, y: 20 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.95, y: 20 }}
              onClick={(e) => e.stopPropagation()}
              className="glass-panel rounded-[6px] border border-[var(--vscode-sidebar-border)] max-w-3xl w-full max-h-[80vh] overflow-hidden"
            >
              <div className="flex items-center justify-between p-4 border-b border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]">
                <div>
                  <h2 className="text-xl font-bold text-[var(--vscode-fg)]">{t('activityBar.legalNotice')}</h2>
                  <p className="text-xs text-[var(--vscode-fg-subtle)] mt-0.5">{t('activityBar.legalSubtitle')}</p>
                </div>
                <button
                  onClick={() => setShowLegalNotice(false)}
                  className="p-2 hover:bg-[var(--vscode-list-hover)] rounded-[6px] transition-colors"
                >
                  <X size={20} />
                </button>
              </div>

              <div className="p-6 overflow-y-auto max-h-[calc(80vh-80px)]">
                <div className="space-y-4 text-sm text-[var(--vscode-fg)]">
                  <div className="space-y-2">
                    <div className="text-xs font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                      {t('activityBar.copyright')}
                    </div>
                    <div className="text-sm leading-relaxed text-[var(--vscode-fg)]">
                      {t('activityBar.copyrightText')}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="text-xs font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                      {t('activityBar.compliance')}
                    </div>
                    <div className="text-sm leading-relaxed text-[var(--vscode-fg)]">
                      {t('activityBar.complianceText')}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="text-xs font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                      {t('activityBar.guideline')}
                    </div>
                    <div className="text-sm leading-relaxed text-[var(--vscode-fg)]">
                      {t('activityBar.guidelineText')}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="text-xs font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                      {t('activityBar.license')}
                    </div>
                    <div className="text-sm leading-relaxed text-[var(--vscode-fg)]">
                      {t('activityBar.licenseText')}
                    </div>
                    <div className="text-xs text-[var(--vscode-fg-subtle)]">
                      {t('activityBar.commercialContact')} <span className="font-mono">1467673018@qq.com</span>
                    </div>
                  </div>

                  <div className="pt-2 border-t border-[var(--vscode-sidebar-border)]">
                    <button
                      onClick={() => setShowFullLicense((v) => !v)}
                      className="text-xs px-3 py-2 rounded-[6px] border border-[var(--vscode-input-border)] hover:bg-[var(--vscode-list-hover)] transition-none text-[var(--vscode-fg)]"
                    >
                      {showFullLicense ? t('activityBar.collapseLicense') : t('activityBar.viewLicense')}
                    </button>
                    {showFullLicense && (
                      <pre className="mt-3 whitespace-pre-wrap text-xs leading-relaxed font-mono bg-[var(--vscode-input-bg)] p-4 rounded-[6px] border border-[var(--vscode-sidebar-border)] text-[var(--vscode-fg)]">
                        {`PolyForm Noncommercial License 1.0.0

https://polyformproject.org/licenses/noncommercial/1.0.0

1. Purpose

The purpose of this license is to allow the software to be used for noncommercial purposes, while reserving the right to use the software for commercial purposes for the licensor.

2. Agreement

In order to receive this license, you must agree to its rules. The rules of this license are both obligations (like a contract) and conditions (like a copyright license). You must not do anything with this software that triggers a rule that you cannot or will not follow.

3. License Grant

The licensor grants you a copyright license for the software to do everything you might do with the software that would otherwise infringe the licensor's copyright in it for any noncommercial purpose.

4. Noncommercial Purposes

Noncommercial purposes include:
*   Use for personal use, including personal research, experimentation, and testing
*   Use by a non-profit organization, such as a charity or a school, for disjoint charitable or educational purposes
*   Use for testing and evaluation of the software

Any other use is a commercial purpose. Commercial purposes strictly prohibited include, but are not limited to:
*   Use by any for-profit entity for any purpose
*   Use in any commercial product or service
*   Use for any business operation or commercial workflow
*   Distribution as part of a paid product or service

5. Notices

You must ensure that anyone who gets a copy of any part of this software from you also gets a copy of this license.

6. No Other Rights

This license does not imply any rights other than those expressly granted.

7. Disclaimer

AS FAR AS THE LAW ALLOWS, THIS SOFTWARE COMES AS IS, WITHOUT ANY WARRANTY OR CONDITION, AND THE LICENSOR WILL NOT BE LIABLE TO YOU FOR ANY DAMAGES ARISING OUT OF THIS LICENSE OR THE USE OF THIS SOFTWARE.

---
COPYRIGHT NOTICE
---
Copyright (c) 2026 Ding Yifei <1467673018@qq.com>

All Rights Reserved.
STRICTLY NO COMMERCIAL USE WITHOUT WRITTEN PERMISSION.`}
                      </pre>
                    )}
                  </div>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

function ActivityItem({ icon: Icon, label, isActive, onClick }) {
  /**
   * ActivityItem - 单个活动栏按钮
   *
   * 活动栏中的单个可点击项，支持激活状态、悬停效果和指示器动画。
   *
   * @param {Function} icon - 图标组件（Lucide 图标）
   * @param {string} label - 按钮工具提示文本
   * @param {boolean} isActive - 是否为激活状态
   * @param {Function} onClick - 点击处理函数
   */
  return (
    <button
      onClick={onClick}
      title={label}
      className={cn(
        'w-9 h-10 flex items-center justify-center rounded-xl transition-all duration-200 group relative z-10',
        isActive ? 'text-[var(--vscode-fg)]' : 'text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)]',
      )}
    >
      <Icon size={20} strokeWidth={isActive ? 2.5 : 2} />
      {isActive && (
        <motion.div
          layoutId="activity-indicator"
          className="absolute left-0 top-2 bottom-2 w-[2px] bg-[var(--vscode-focus-border)] rounded-r-full"
          transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        />
      )}
    </button>
  );
}
