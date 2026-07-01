/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 *
 * 模块说明 / Module Description:
 *   智能体面板 - 配置和管理 LLM 提供商、模型选择与智能体绑定关系
 *   Agents panel for configuring LLM providers, selecting models, and managing agent assignments.
 */

/**
 * 智能体配置面板 - LLM 提供商和智能体绑定的集中管理界面
 *
 * IDE panel for configuring LLM profiles (provider and model selection) and assigning them to agents.
 * Supports visual consistency optimization without altering core business logic.
 *
 * @component
 * @example
 * return (
 *   <AgentsPanel mode="config" />
 * )
 *
 * @param {Object} props - Component props
 * @param {React.ReactNode} [props.children] - 子组件 / Child components
 * @param {string} [props.mode='assistant'] - 面板模式 / Panel mode (assistant|config)
 * @returns {JSX.Element} 智能体面板 / Agents panel element
 */
import { useState } from 'react';
import useSWR, { mutate } from 'swr';
import { Bot, Search, Edit3, Plus } from 'lucide-react';
import { AnimatePresence } from 'framer-motion';
import { configAPI } from '../../../api';
import { cn, Button } from '../../ui/core';
import LLMProfileModal from '../../../components/LLMProfileModal';
import logger from '../../../utils/logger';
import { useLocale } from '../../../i18n';

// SWR 数据获取器 / SWR data fetcher
const fetcher = (fn) => fn().then((res) => res.data);

const AgentsPanel = ({ children, mode = 'assistant' }) => {
  // mode: 'assistant'（右侧 AI 面板）| 'config'（左侧：仅配置）
  // mode: 'assistant' (right AI panel) | 'config' (left: config only)

  const { t } = useLocale();

  const BINDING_ROLES = [
    { id: 'archivist', label: t('panels.agents.roleArchivist'), desc: t('panels.agents.roleArchivistDesc') },
    { id: 'writing', label: t('panels.agents.roleWriting'), desc: t('panels.agents.roleWritingDesc') },
  ];

  // 数据获取
  const { data: profiles = [], isLoading: loadingProfiles } = useSWR(
    'llm-profiles',
    () => fetcher(configAPI.getProfiles),
    { revalidateOnFocus: false },
  );

  const { data: assignments = {}, isLoading: loadingAssignments } = useSWR(
    'agent-assignments',
    () => fetcher(configAPI.getAssignments),
    { revalidateOnFocus: false },
  );

  const isLoading = loadingProfiles || loadingAssignments;
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedProfile, setSelectedProfile] = useState(null);

  // 事件处理
  const handleAssignmentChange = async (roleIds, profileId) => {
    const ids = Array.isArray(roleIds) ? roleIds : [roleIds];
    const newAssignments = { ...assignments };
    ids.forEach((id) => {
      newAssignments[id] = profileId;
    });
    mutate('agent-assignments', newAssignments, false);
    try {
      await configAPI.updateAssignments(newAssignments);
      mutate('agent-assignments');
    } catch (e) {
      logger.error('Failed to update assignment', e);
      mutate('agent-assignments');
    }
  };

  const handleEditProfile = (profile) => {
    setSelectedProfile(profile);
    setIsModalOpen(true);
  };

  const handleCreateProfile = () => {
    setSelectedProfile(null);
    setIsModalOpen(true);
  };

  const handleSaveProfile = async (profileData) => {
    try {
      await configAPI.saveProfile(profileData);
      setIsModalOpen(false);
      setSelectedProfile(null);
      mutate('llm-profiles');
      mutate('agent-assignments');
    } catch (e) {
      logger.error('Failed to save profile', e);
    }
  };

  const handleDeleteProfile = async (id) => {
    await configAPI.deleteProfile(id);
    mutate('llm-profiles');
    mutate('agent-assignments');
  };

  // --- 渲染：配置模式（左侧栏）---
  if (mode === 'config') {
    return (
      <div className="anti-theme flex flex-col h-full bg-[var(--vscode-bg)] text-[var(--vscode-fg)] overflow-hidden">
        <div className="p-4 space-y-6 overflow-y-auto h-full scrollbar-hide">
          {isLoading ? (
            [1, 2, 3, 4].map((i) => (
              <div key={i} className="h-24 bg-[var(--vscode-list-hover)] animate-pulse rounded-[6px]" />
            ))
          ) : (
            <>
              {/* 角色模型绑定 */}
              <div className="space-y-3">
                <div className="text-xs font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider mb-2">
                  {t('panels.agents.roleBinding')}
                </div>
                {BINDING_ROLES.map((role) => {
                  const assignedProfileId =
                    role.id === 'writing'
                      ? assignments.writer && assignments.writer === assignments.editor
                        ? assignments.writer
                        : assignments.writer || assignments.editor || ''
                      : assignments[role.id];
                  const writingMismatch =
                    role.id === 'writing'
                      ? Boolean(assignments.writer && assignments.editor && assignments.writer !== assignments.editor)
                      : false;

                  // 动态图标选择
                  const Icon = role.id === 'archivist' ? Search : Edit3;

                  return (
                    <div
                      key={role.id}
                      className="group flex flex-col gap-3 p-3 rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] hover:bg-[var(--vscode-list-hover)] transition-none"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <div
                            className={cn(
                              'p-2 rounded-[6px] text-[var(--vscode-fg)] bg-[var(--vscode-list-hover)]',
                              role.id === 'writing' && 'font-semibold',
                            )}
                          >
                            <Icon size={16} />
                          </div>
                          <div>
                            <div className="text-sm font-bold leading-none text-[var(--vscode-fg)]">{role.label}</div>
                            <div className="text-[10px] text-[var(--vscode-fg-subtle)] mt-1">{role.desc}</div>
                          </div>
                        </div>
                      </div>

                      <div>
                        <div className="relative">
                          <select
                            value={assignedProfileId || ''}
                            onChange={(e) => {
                              const next = e.target.value;
                              if (role.id === 'writing') {
                                handleAssignmentChange(['writer', 'editor'], next);
                                return;
                              }
                              handleAssignmentChange(role.id, next);
                            }}
                            className="w-full text-xs py-2 pl-2 pr-6 bg-[var(--vscode-input-bg)] border border-[var(--vscode-input-border)] rounded-[6px] focus:border-[var(--vscode-focus-border)] focus:ring-2 focus:ring-[var(--vscode-focus-border)] outline-none transition-none appearance-none cursor-pointer text-[var(--vscode-fg)] font-mono truncate"
                          >
                            <option value="" disabled>
                              {t('panels.agents.selectModel')}
                            </option>
                            {profiles.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name}
                              </option>
                            ))}
                          </select>
                          <div className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-[var(--vscode-fg-subtle)]">
                            <Bot size={12} />
                          </div>
                        </div>
                        {writingMismatch && (
                          <div className="mt-2 text-[10px] text-[var(--vscode-fg-subtle)]">
                            {t('panels.agents.writingMismatch')}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* 模型库 */}
              <div className="pt-4 border-t border-[var(--vscode-sidebar-border)]">
                <div className="flex items-center justify-between mb-3">
                  <div className="text-xs font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                    {t('panels.agents.modelLibrary')}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={handleCreateProfile}
                    className="h-6 text-[10px] px-2 border border-[var(--vscode-input-border)] text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] shadow-none"
                  >
                    <Plus size={12} className="mr-1" /> {t('panels.agents.addModel')}
                  </Button>
                </div>

                <div className="grid grid-cols-1 gap-2">
                  {profiles.map((p) => (
                    <div
                      key={p.id}
                      onClick={() => handleEditProfile(p)}
                      className="bg-[var(--vscode-bg)] border border-[var(--vscode-sidebar-border)] rounded-[6px] p-3 text-xs flex flex-row items-center justify-between hover:bg-[var(--vscode-list-hover)] cursor-pointer transition-none group"
                    >
                      <div className="flex flex-col gap-0.5">
                        <span className="font-bold text-[var(--vscode-fg)]">{p.name}</span>
                        <span className="text-[var(--vscode-fg-subtle)] font-mono text-[10px]">{p.provider}</span>
                      </div>
                      <span className="font-mono bg-[var(--vscode-input-bg)] px-2 py-1 rounded-[4px] text-[10px] text-[var(--vscode-fg-subtle)]">
                        {p.model}
                      </span>
                    </div>
                  ))}
                  {profiles.length === 0 && (
                    <div className="text-center py-6 text-xs text-[var(--vscode-fg-subtle)] border border-dashed border-[var(--vscode-sidebar-border)] rounded-[6px]">
                      {t('panels.agents.noModels')}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {/* 弹窗 */}
        <AnimatePresence>
          {isModalOpen && (
            <LLMProfileModal
              open={isModalOpen}
              profile={selectedProfile}
              onClose={() => {
                setIsModalOpen(false);
                setSelectedProfile(null);
              }}
              onSave={handleSaveProfile}
              onDelete={async (id) => {
                await handleDeleteProfile(id);
                setIsModalOpen(false);
                setSelectedProfile(null);
              }}
            />
          )}
        </AnimatePresence>
      </div>
    );
  }

  // --- 渲染：助手模式（右侧栏）---
  return (
    <div className="anti-theme flex flex-col h-full bg-[var(--vscode-bg)] text-[var(--vscode-fg)]">
      {/* 内容区 */}
      <div className="flex-1 overflow-hidden relative bg-[var(--vscode-bg)]">
        <div className="h-full flex flex-col overflow-hidden">{children}</div>
      </div>
    </div>
  );
};

export default AgentsPanel;
