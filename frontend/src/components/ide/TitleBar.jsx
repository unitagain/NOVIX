import React, { useState, useRef, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useIDE } from '../../context/IDEContext';
import useSWR, { mutate } from 'swr';
import { projectsAPI, sessionAPI } from '../../api';
import { Bot, ChevronDown, Folder, Plus, Check, Trash2, Home, Pencil, Settings, Download } from 'lucide-react';
import { cn } from '../ui/core';
import logger from '../../utils/logger';
import { useLocale } from '../../i18n';
import ExportDialog from './ExportDialog';
import CloudAccountButton from '../cloud/CloudAccountButton';
import {
  isDesktopRuntime,
  openDesktopDataDirectory,
  openDesktopLogsDirectory,
  openDesktopMainLog,
  openDesktopRuntimeDirectory,
} from '../../utils/desktop';

const fetcher = (fn) => fn().then((res) => res.data);

const STREAMING_PREF_KEY = 'wenshape_output_streaming';
const DIALOG_MAX_CHARS_PREF_KEY = 'wenshape_dialog_max_chars';
const DIALOG_MAX_CHARS_VALUES = new Set([2000, 6000]);

// Writer 反问工具策略（项目级，后端为唯一持久化 owner）：
// always=主动检查 | auto=模型自行判断 | off=不主动触发。文案复用 agentPanel.clarify*Full。
const CLARIFY_MODES = new Set(['always', 'auto', 'off']);
const CLARIFY_MODE_OPTIONS = [
  { mode: 'always', labelKey: 'agentPanel.clarifyAlwaysFull' },
  { mode: 'auto', labelKey: 'agentPanel.clarifyAutoFull' },
  { mode: 'off', labelKey: 'agentPanel.clarifyOffFull' },
];

/** Read streaming preference from localStorage (default: true) */
export function getStreamingPreference() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return true;
    }
    const val = window.localStorage.getItem(STREAMING_PREF_KEY);
    return val === null ? true : val === 'true';
  } catch {
    return true;
  }
}

/** Write streaming preference to localStorage */
function setStreamingPreference(enabled) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return;
    }
    window.localStorage.setItem(STREAMING_PREF_KEY, String(enabled));
  } catch {
    /* ignore */
  }
}

const DEEP_THINKING_PREF_KEY = 'wenshape_deep_thinking';

/** Read deep-thinking preference from localStorage (default: false) */
export function getDeepThinkingPreference() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return false;
    }
    return window.localStorage.getItem(DEEP_THINKING_PREF_KEY) === 'true';
  } catch {
    return false;
  }
}

/** Write deep-thinking preference to localStorage */
export function setDeepThinkingPreference(enabled) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return;
    }
    window.localStorage.setItem(DEEP_THINKING_PREF_KEY, String(enabled));
  } catch {
    /* ignore */
  }
}

/** Read dialog max chars preference from localStorage (default: 2000) */
export function getDialogMaxCharsPreference() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return 2000;
    }
    const raw = Number(window.localStorage.getItem(DIALOG_MAX_CHARS_PREF_KEY));
    return DIALOG_MAX_CHARS_VALUES.has(raw) ? raw : 2000;
  } catch {
    return 2000;
  }
}

/** Write dialog max chars preference to localStorage and notify same-tab listeners */
function setDialogMaxCharsPreference(maxChars) {
  const normalized = DIALOG_MAX_CHARS_VALUES.has(Number(maxChars)) ? Number(maxChars) : 2000;
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return;
    }
    window.localStorage.setItem(DIALOG_MAX_CHARS_PREF_KEY, String(normalized));
    window.dispatchEvent(new CustomEvent('wenshape:dialog-max-chars', { detail: normalized }));
  } catch {
    /* ignore */
  }
}

/**
 * TitleBar - 顶部标题栏
 * 负责项目切换与快捷操作入口，不改变业务逻辑。
 */
export function TitleBar({ projectName, chapterTitle, currentChapter, rightActions, aiHint }) {
  const navigate = useNavigate();
  const { projectId } = useParams();
  const { state, dispatch } = useIDE();
  const { t, locale, setLocale } = useLocale();
  const [menuOpen, setMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [createMode, setCreateMode] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [creating, setCreating] = useState(false);
  const [streamingEnabled, setStreamingEnabled] = useState(getStreamingPreference);
  const [dialogMaxChars, setDialogMaxChars] = useState(getDialogMaxCharsPreference);
  const [clarifyMode, setClarifyMode] = useState('auto');
  const [exportOpen, setExportOpen] = useState(false);
  const menuRef = useRef(null);
  const settingsRef = useRef(null);
  const desktopRuntime = isDesktopRuntime();

  const { data: projects = [] } = useSWR('all-projects', () => fetcher(projectsAPI.list), { revalidateOnFocus: false });

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false);
        setCreateMode(false);
      }
      if (settingsRef.current && !settingsRef.current.contains(e.target)) {
        setSettingsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // 载入项目级反问策略（后端为唯一 owner；无 projectId 时不请求）。
  useEffect(() => {
    if (!projectId) return undefined;
    let cancelled = false;
    sessionAPI
      .getClarifySettings(projectId)
      .then((resp) => {
        if (cancelled) return;
        const mode = String(resp?.data?.settings?.auto_trigger || resp?.data?.settings?.mode || 'auto');
        if (CLARIFY_MODES.has(mode)) setClarifyMode(mode);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const languageOptions = [
    { locale: 'zh-CN', label: t('titleBar.uiLanguageZh') },
    { locale: 'en-US', label: t('titleBar.uiLanguageEn') },
  ];

  const handleSwitchLanguage = (nextLocale) => {
    if (!nextLocale || nextLocale === locale) return;
    const targetLabel = languageOptions.find((opt) => opt.locale === nextLocale)?.label || nextLocale;
    if (!confirm(t('titleBar.switchLanguageConfirm', { target: targetLabel }))) return;
    setLocale(nextLocale);
  };

  const handleToggleStreaming = (enabled) => {
    setStreamingEnabled(enabled);
    setStreamingPreference(enabled);
  };

  const handleDialogMaxCharsChange = (nextValue) => {
    const normalized = DIALOG_MAX_CHARS_VALUES.has(Number(nextValue)) ? Number(nextValue) : 2000;
    setDialogMaxChars(normalized);
    setDialogMaxCharsPreference(normalized);
  };

  const handleClarifyModeChange = async (mode) => {
    if (!projectId) return;
    const next = CLARIFY_MODES.has(mode) ? mode : 'auto';
    const prev = clarifyMode;
    if (next === prev) return;
    setClarifyMode(next);
    try {
      await sessionAPI.updateClarifySettings(projectId, { auto_trigger: next });
    } catch {
      setClarifyMode(prev);
    }
  };

  const handleDesktopAction = async (action) => {
    try {
      if (action === 'logs') {
        await openDesktopLogsDirectory();
      } else if (action === 'data') {
        await openDesktopDataDirectory();
      } else if (action === 'runtime') {
        await openDesktopRuntimeDirectory();
      } else if (action === 'main-log') {
        await openDesktopMainLog();
      }
      setSettingsOpen(false);
    } catch (error) {
      logger.error('Failed to execute desktop action:', error);
    }
  };

  const handleCreateProject = async () => {
    if (!newProjectName.trim()) return;
    setCreating(true);
    try {
      const res = await projectsAPI.create({ name: newProjectName.trim() });
      mutate('all-projects');
      setNewProjectName('');
      setCreateMode(false);
      setMenuOpen(false);
      navigate(`/project/${res.data.id}/session`);
    } catch (error) {
      logger.error('Failed to create project:', error);
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteProject = async (id, e) => {
    e.stopPropagation();
    if (!confirm(t('titleBar.deleteProjectConfirm'))) return;
    try {
      await projectsAPI.delete(id);
      mutate('all-projects');
      if (id === projectId) {
        navigate('/');
      }
    } catch (error) {
      logger.error('Failed to delete project:', error);
    }
  };

  const handleRenameProject = async (project, e) => {
    e.stopPropagation();
    const nextName = prompt(t('titleBar.renameProjectPrompt'), project?.name || '');
    if (nextName == null) return;
    const trimmed = String(nextName).trim();
    if (!trimmed) {
      alert(t('titleBar.renameProjectEmpty'));
      return;
    }
    try {
      await projectsAPI.rename(project.id, { name: trimmed });
      await mutate('all-projects');
    } catch (error) {
      const detail = error?.response?.data?.detail || error?.response?.data?.error;
      alert(t('titleBar.renameProjectFailed', { message: detail || error?.message || 'Unknown error' }));
    }
  };

  const effectiveProjectName = projectName || projects.find((p) => p?.id === projectId)?.name || '';

  return (
    <div className="h-10 min-h-[40px] bg-[var(--vscode-desktop)] flex items-center justify-between px-4 select-none flex-shrink-0">
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate('/')}
          className="flex flex-col leading-none hover:text-[var(--vscode-fg)] transition-colors"
          title={t('titleBar.backToHome')}
        >
          <span className="brand-logo text-xl text-[var(--vscode-fg)]">文枢</span>
        </button>

        {/* Project menu */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => {
              setSettingsOpen(false);
              setMenuOpen(!menuOpen);
            }}
            className={cn(
              'flex items-center gap-2 px-3 py-1.5 rounded-[6px] text-sm transition-colors',
              menuOpen
                ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                : 'text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]',
            )}
          >
            <Folder size={14} />
            <span className="max-w-[120px] truncate">{effectiveProjectName || t('titleBar.selectProject')}</span>
            <ChevronDown size={12} className={cn('transition-transform', menuOpen && 'rotate-180')} />
          </button>

          {menuOpen && (
            <div className="absolute left-0 top-full mt-1 w-64 glass-panel border border-[var(--vscode-sidebar-border)] rounded-[6px] py-1 z-50 soft-dropdown">
              <div className="px-3 py-2 text-xs font-bold text-[var(--vscode-fg-subtle)] uppercase">
                {t('titleBar.myProjects')}
              </div>

              <div className="max-h-48 overflow-y-auto">
                {projects.map((project) => (
                  <div
                    key={project.id}
                    onClick={() => {
                      navigate(`/project/${project.id}/session`);
                      setMenuOpen(false);
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors cursor-pointer group"
                  >
                    <Folder size={14} className="text-[var(--vscode-fg-subtle)] flex-shrink-0" />
                    <span className="flex-1 text-left truncate">{project.name}</span>
                    {project.id === projectId && (
                      <Check size={14} className="text-[var(--vscode-focus-border)] flex-shrink-0" />
                    )}
                    <button
                      onClick={(e) => handleRenameProject(project, e)}
                      className="opacity-70 group-hover:opacity-100 p-1 hover:bg-[var(--vscode-list-hover)] rounded text-[var(--vscode-fg-subtle)] hover:text-[var(--vscode-fg)] transition-all flex-shrink-0"
                      title={t('titleBar.renameProject')}
                      aria-label={t('titleBar.renameProject')}
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      onClick={(e) => handleDeleteProject(project.id, e)}
                      className="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-50 rounded text-[var(--vscode-fg-subtle)] hover:text-red-500 transition-all flex-shrink-0"
                      title={t('titleBar.deleteProject')}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>

              <div className="border-t border-[var(--vscode-sidebar-border)] my-1" />

              {createMode ? (
                <div className="px-3 py-2">
                  <input
                    type="text"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleCreateProject()}
                    placeholder={t('titleBar.newProjectPlaceholder')}
                    className="w-full text-xs py-1.5 px-2 border border-[var(--vscode-input-border)] rounded-[6px] focus:border-[var(--vscode-focus-border)] focus:ring-1 focus:ring-[var(--vscode-focus-border)] outline-none bg-[var(--vscode-input-bg)] text-[var(--vscode-fg)]"
                    autoFocus
                  />
                  <div className="flex gap-2 mt-2">
                    <button
                      onClick={handleCreateProject}
                      disabled={creating || !newProjectName.trim()}
                      className="flex-1 py-1.5 bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)] text-xs rounded-[6px] hover:opacity-90 disabled:opacity-50"
                    >
                      {creating ? t('titleBar.creating') : t('titleBar.createBtn')}
                    </button>
                    <button
                      onClick={() => {
                        setCreateMode(false);
                        setNewProjectName('');
                      }}
                      className="py-1.5 px-3 text-xs text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] rounded-[6px]"
                    >
                      {t('titleBar.cancelBtn')}
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setCreateMode(true)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
                >
                  <Plus size={14} className="text-[var(--vscode-fg-subtle)]" />
                  <span>{t('titleBar.newProject')}</span>
                </button>
              )}

              <button
                onClick={() => {
                  navigate('/');
                  setMenuOpen(false);
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
              >
                <Home size={14} className="text-[var(--vscode-fg-subtle)]" />
                <span>{t('titleBar.home')}</span>
              </button>
            </div>
          )}
        </div>

        {/* Settings menu */}
        <div className="relative" ref={settingsRef}>
          <button
            onClick={() => {
              setMenuOpen(false);
              setCreateMode(false);
              setSettingsOpen((prev) => !prev);
            }}
            className={cn(
              'flex items-center gap-2 px-3 py-1.5 rounded-[6px] text-sm transition-colors',
              settingsOpen
                ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                : 'text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]',
            )}
            title={t('titleBar.settings')}
            aria-label={t('titleBar.settings')}
          >
            <Settings size={14} />
            <ChevronDown size={12} className={cn('transition-transform', settingsOpen && 'rotate-180')} />
          </button>

          {settingsOpen && (
            <div className="absolute left-0 top-full mt-1 w-64 glass-panel border border-[var(--vscode-sidebar-border)] rounded-[6px] py-1 z-50 soft-dropdown">
              {/* Language section */}
              <div className="px-3 py-2 text-[10px] font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                {t('titleBar.uiLanguage')}
              </div>
              {languageOptions.map((opt) => (
                <button
                  key={opt.locale}
                  onClick={() => handleSwitchLanguage(opt.locale)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
                >
                  <span className="flex-1 text-left">{opt.label}</span>
                  {opt.locale === locale && (
                    <Check size={14} className="text-[var(--vscode-focus-border)] flex-shrink-0" />
                  )}
                </button>
              ))}

              <div className="border-t border-[var(--vscode-sidebar-border)] my-1" />

              {/* Dialog max chars section */}
              <div className="px-3 py-2 text-[10px] font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                {t('titleBar.settingsDialogMaxChars')}
              </div>
              <button
                onClick={() => handleDialogMaxCharsChange(2000)}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
              >
                <div className="flex-1 text-left">
                  <div className="text-sm">{t('titleBar.dialogMaxChars2k')}</div>
                </div>
                {dialogMaxChars === 2000 && (
                  <Check size={14} className="text-[var(--vscode-focus-border)] flex-shrink-0" />
                )}
              </button>
              <button
                onClick={() => handleDialogMaxCharsChange(6000)}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
              >
                <div className="flex-1 text-left">
                  <div className="text-sm">{t('titleBar.dialogMaxChars6k')}</div>
                </div>
                {dialogMaxChars === 6000 && (
                  <Check size={14} className="text-[var(--vscode-focus-border)] flex-shrink-0" />
                )}
              </button>

              <div className="border-t border-[var(--vscode-sidebar-border)] my-1" />

              {/* Output mode section */}
              <div className="px-3 py-2 text-[10px] font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                {t('titleBar.settingsOutputMode')}
              </div>
              <button
                onClick={() => handleToggleStreaming(true)}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
              >
                <div className="flex-1 text-left">
                  <div className="text-sm">{t('titleBar.outputStreaming')}</div>
                  <div className="text-[10px] text-[var(--vscode-fg-subtle)]">{t('titleBar.outputStreamingDesc')}</div>
                </div>
                {streamingEnabled && <Check size={14} className="text-[var(--vscode-focus-border)] flex-shrink-0" />}
              </button>
              <button
                onClick={() => handleToggleStreaming(false)}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
              >
                <div className="flex-1 text-left">
                  <div className="text-sm">{t('titleBar.outputDirect')}</div>
                  <div className="text-[10px] text-[var(--vscode-fg-subtle)]">{t('titleBar.outputDirectDesc')}</div>
                </div>
                {!streamingEnabled && <Check size={14} className="text-[var(--vscode-focus-border)] flex-shrink-0" />}
              </button>

              {projectId && (
                <>
                  <div className="border-t border-[var(--vscode-sidebar-border)] my-1" />
                  <div className="px-3 py-2 text-[10px] font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                    {t('titleBar.settingsClarifyMode')}
                  </div>
                  {CLARIFY_MODE_OPTIONS.map(({ mode, labelKey }) => (
                    <button
                      key={mode}
                      onClick={() => handleClarifyModeChange(mode)}
                      className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
                    >
                      <span className="flex-1 text-left">{t(labelKey)}</span>
                      {clarifyMode === mode && (
                        <Check size={14} className="text-[var(--vscode-focus-border)] flex-shrink-0" />
                      )}
                    </button>
                  ))}
                </>
              )}

              {desktopRuntime && (
                <>
                  <div className="border-t border-[var(--vscode-sidebar-border)] my-1" />
                  <div className="px-3 py-2 text-[10px] font-bold text-[var(--vscode-fg-subtle)] uppercase tracking-wider">
                    {t('titleBar.desktopTools')}
                  </div>
                  <button
                    onClick={() => handleDesktopAction('logs')}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
                  >
                    <span className="flex-1 text-left">{t('titleBar.desktopOpenLogs')}</span>
                  </button>
                  <button
                    onClick={() => handleDesktopAction('data')}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
                  >
                    <span className="flex-1 text-left">{t('titleBar.desktopOpenData')}</span>
                  </button>
                  <button
                    onClick={() => handleDesktopAction('runtime')}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
                  >
                    <span className="flex-1 text-left">{t('titleBar.desktopOpenRuntime')}</span>
                  </button>
                  <button
                    onClick={() => handleDesktopAction('main-log')}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] hover:bg-[var(--vscode-list-hover)] transition-colors"
                  >
                    <span className="flex-1 text-left">{t('titleBar.desktopOpenMainLog')}</span>
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        <button
          onClick={() => setExportOpen(true)}
          disabled={!projectId}
          className="flex items-center gap-2 px-3 py-1.5 rounded-[6px] text-sm text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)] transition-colors disabled:opacity-50"
          title={t('titleBar.export')}
          aria-label={t('titleBar.export')}
        >
          <Download size={14} />
        </button>
      </div>

      <div className="flex-1 flex items-center justify-center gap-2 text-sm">
        {state.unsavedChanges && <span className="text-yellow-600 text-lg">•</span>}
        {aiHint && (
          <span className="text-[10px] px-2 py-0.5 rounded-[999px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] text-[var(--vscode-fg-subtle)]">
            {aiHint}
          </span>
        )}
        {chapterTitle && <span className="text-[var(--vscode-fg)] font-medium">{chapterTitle}</span>}
      </div>

      <div className="flex items-center gap-2">
        {rightActions}
        <CloudAccountButton />
        <button
          onClick={() => dispatch({ type: 'TOGGLE_RIGHT_PANEL' })}
          className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-[6px] text-sm transition-colors',
            state.rightPanelVisible
              ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
              : 'text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]',
          )}
          title={state.rightPanelVisible ? t('titleBar.closeAiPanel') : t('titleBar.openAiPanel')}
          aria-label={state.rightPanelVisible ? t('titleBar.closeAiPanel') : t('titleBar.openAiPanel')}
        >
          <Bot size={14} />
        </button>
      </div>

      <ExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        projectId={projectId}
        currentChapter={currentChapter}
      />
    </div>
  );
}
