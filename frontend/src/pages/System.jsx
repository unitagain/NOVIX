/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   系统设置页面 - 占位符页面，预留未来功能扩展入口
 */

import React from 'react';
import { motion } from 'framer-motion';
import { Settings } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { useLocale } from '../i18n';
import UpdateChannelCard from '../components/cloud/UpdateChannelCard';

/**
 * 系统设置页 / System Settings Page
 *
 * 当前为占位符实现，用于展示系统配置相关的提示信息。
 * 不改变任何业务逻辑，仅作为 UI 框架预留。
 *
 * 未来可扩展功能：
 * - 主题切换
 * - 快捷键配置
 * - 性能优化选项
 * - 数据导出/导入
 *
 * @component
 * @returns {JSX.Element} 系统设置页面容器
 */
function System() {
  const { t } = useLocale();
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="anti-theme p-6 bg-[var(--vscode-bg)] text-[var(--vscode-fg)]"
    >
      <Card className="ws-paper">
        <CardHeader>
          <CardTitle>
            <Settings size={18} /> {t('system.title')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-[var(--vscode-fg-subtle)]">
            {t('system.comingSoon')}
          </div>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.2 }}
            className="mt-4 p-4 rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]"
          >
            <div className="text-sm text-[var(--vscode-fg)] font-semibold">{t('common.default')}</div>
            <div className="text-sm text-[var(--vscode-fg-subtle)] mt-1">
              {t('system.comingSoon')}
            </div>
          </motion.div>
          <UpdateChannelCard />
        </CardContent>
      </Card>
    </motion.div>
  );
}

export default System;
