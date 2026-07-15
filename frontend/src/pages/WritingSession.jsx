/**
 * 文枢 WenShape - 深度上下文感知的智能体小说创作系统
 * WenShape - Deep Context-Aware Agent-Based Novel Writing System
 *
 * Copyright © 2025-2026 WenShape Team
 * License: PolyForm Noncommercial License 1.0.0
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import useSWR from 'swr';
import { useParams } from 'react-router-dom';
import { sessionAPI, draftsAPI, cardsAPI, projectsAPI, volumesAPI, configAPI } from '../api';
import { Button } from '../components/ui/core';
import { ChapterCreateDialog } from '../components/project/ChapterCreateDialog';
import { IDELayout } from '../components/ide/IDELayout';
import { IDEProvider } from '../context/IDEContext';
import { useIDE } from '../context/IDEContext';
import AnalysisReviewDialog from '../components/writing/AnalysisReviewDialog';
import PreWritingQuestionsDialog from '../components/PreWritingQuestionsDialog';
import WritingSessionAgentPanel from '../components/writing/WritingSessionAgentPanel';
import WritingSessionMainContent from '../components/writing/WritingSessionMainContent';
import { buildLineDiff, applyDiffOpsWithDecisions } from '../lib/diffUtils';
import SaveMenu from '../components/writing/SaveMenu';
import logger from '../utils/logger';
import { extractErrorDetail } from '../utils/extractError';
import { useLocale } from '../i18n';
import {
  getStreamingPreference,
  getDialogMaxCharsPreference,
  getDeepThinkingPreference,
  setDeepThinkingPreference,
} from '../components/ide/TitleBar';
import { useWritingSessionRealtime } from '../hooks/useWritingSessionRealtime';
import { normalizeChatTurnResponse, terminalStateMessage } from '../features/agent/model/agentProtocol';
import {
  fetchChapterContent,
  countWords,
  getSelectionStats,
  normalizeStars,
  parseListInput,
  formatListInput,
  stabilizeRevisionTail,
} from '../utils/writingSessionHelpers';

/**
 * WritingSessionContent - 写作会话主流程组件
 *
 * 统一的写作 IDE 界面，集成 AI 写作、编辑、分析等功能。
 * 使用 IDE Layout 提供三段式布局（活动栏、左侧面板、编辑区、右侧面板、底部状态栏）。
 *
 * 主要功能：
 * - 实时 WebSocket 连接管理和消息处理
 * - 章节内容编辑和版本管理
 * - AI 驱动的写作、编辑、分析建议
 * - 交互式对话和反馈流程
 * - 草稿保存和历史记录
 *
 * @component
 * @returns {JSX.Element} 写作会话主界面
 */
function WritingSessionContent() {
  const { t, locale } = useLocale();
  const requestLanguage = locale === 'en-US' ? 'en' : 'zh';
  const { projectId } = useParams();
  const { state, dispatch } = useIDE();

  // ========================================================================
  // 项目和会话基本信息 / Project and Session Information
  // ========================================================================
  // 项目数据状态 / Project data from API
  const [project, setProject] = useState(null);
  const writingLanguage = project?.language === 'en' ? 'en' : 'zh';
  const prevProjectIdRef = useRef(null);
  const chatHydratedRef = useRef(null);

  useEffect(() => {
    if (projectId) {
      projectsAPI.get(projectId).then((res) => setProject(res.data));
      dispatch({ type: 'SET_PROJECT_ID', payload: projectId });
    }
  }, [projectId, dispatch]);

  // 项目切换时清理所有会话状态，防止数据污染
  // 使用 useRef 判断 projectId 是否真正变化，避免不必要的清理
  useEffect(() => {
    if (prevProjectIdRef.current && prevProjectIdRef.current !== projectId) {
      // 项目真正切换了：清理所有写作会话状态
      setDiffReview(null);
      setDiffDecisions({});
      setCurrentDraft(null);
      setManualContent('');
      setManualContentByChapter({});
      setMessagesByChapter({});
      setProgressEventsByChapter({});
      setDraftV1(null);
      setSceneBrief(null);
      setFeedback('');
      setChapterInfo({ chapter: null, chapter_title: null, content: null });
      setStatus('idle');
      setSelectionInfo({ start: 0, end: 0, text: '' });
      setAttachedSelection(null);
      setEditScope('document');
      setAiLockedChapter(null);
      if (streamingRef.current?.timer) {
        streamingRef.current.timer();
      }
      streamingRef.current = null;
      setStreamingState({ active: false, progress: 0, current: 0, total: 0 });
    }
    prevProjectIdRef.current = projectId;
  }, [projectId]);

  // UI State
  const [showChapterDialog, setShowChapterDialog] = useState(false);
  const [chapters, setChapters] = useState([]);

  // Save/Analyze UI
  const [isSaving, setIsSaving] = useState(false);
  const [analysisDialogOpen, setAnalysisDialogOpen] = useState(false);
  const [analysisItems, setAnalysisItems] = useState([]);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisSaving, setAnalysisSaving] = useState(false);

  // Proposal State
  const [, setProposals] = useState([]);

  // Logic State
  const [status, setStatus] = useState('idle'); // idle, starting, editing, waiting_feedback, completed
  const [messagesByChapter, setMessagesByChapter] = useState({});
  const [progressEventsByChapter, setProgressEventsByChapter] = useState({});
  const [, setCurrentDraft] = useState(null);
  const [manualContent, setManualContent] = useState(''); // Textarea content
  const [manualContentByChapter, setManualContentByChapter] = useState({});
  const [selectionInfo, setSelectionInfo] = useState({ start: 0, end: 0, text: '' });
  const [attachedSelection, setAttachedSelection] = useState(null); // { start, end, text }
  const [editScope, setEditScope] = useState('document'); // document | selection
  const [, setSceneBrief] = useState(null);
  const [, setDraftV1] = useState(null);
  const [feedback, setFeedback] = useState('');
  const [dialogMaxChars, setDialogMaxChars] = useState(getDialogMaxCharsPreference);
  const [diffReview, setDiffReview] = useState(null);
  const [diffDecisions, setDiffDecisions] = useState({});
  const lastFeedbackRef = useRef('');
  const lastGeneratedByChapterRef = useRef({});
  const streamBufferByChapterRef = useRef({});
  const streamTextByChapterRef = useRef({});
  const streamFlushRafByChapterRef = useRef({});
  const serverStreamActiveRef = useRef(false);
  const serverStreamUsedRef = useRef(false);
  const streamingChapterKeyRef = useRef(null);
  const streamOriginalByChapterRef = useRef({}); // 流式前各章原文快照，用于结束时生成 diff 提议

  const [showPreWriteDialog, setShowPreWriteDialog] = useState(false);
  const [preWriteQuestions, setPreWriteQuestions] = useState([]);
  const [pendingStartPayload, setPendingStartPayload] = useState(null);

  useEffect(() => {
    const onDialogMaxCharsChanged = (event) => {
      const next = Number(event?.detail);
      setDialogMaxChars(next === 6000 ? 6000 : 2000);
    };
    const onStorage = (event) => {
      if (event?.key !== 'wenshape_dialog_max_chars') return;
      const next = Number(event?.newValue);
      setDialogMaxChars(next === 6000 ? 6000 : 2000);
    };
    window.addEventListener('wenshape:dialog-max-chars', onDialogMaxCharsChanged);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener('wenshape:dialog-max-chars', onDialogMaxCharsChanged);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const manualContentByChapterRef = useRef(manualContentByChapter);
  useEffect(() => {
    manualContentByChapterRef.current = manualContentByChapter;
  }, [manualContentByChapter]);

  // AI 锁定章：写作/编辑进行中时，右侧面板锁死在该章节（中央可切换查看/手改其他章节）
  const [aiLockedChapter, setAiLockedChapter] = useState(null);
  const aiLockedChapterRef = useRef(aiLockedChapter);
  useEffect(() => {
    aiLockedChapterRef.current = aiLockedChapter;
  }, [aiLockedChapter]);

  // 轻提示（不打断、不强跳转）
  const [notice, setNotice] = useState(null);
  const noticeTimerRef = useRef(null);
  const pushNotice = useCallback((text) => {
    if (!text) return;
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setNotice({ id, text: String(text) });
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), 2600);
  }, []);
  useEffect(() => {
    return () => {
      if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    };
  }, []);

  // WebSocket
  const wsRef = useRef(null);
  const traceWsRef = useRef(null);
  const wsStatusRef = useRef('disconnected');
  const [isGenerating, setIsGenerating] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const streamingRef = useRef(null);
  const [streamingState, setStreamingState] = useState({
    active: false,
    progress: 0,
    current: 0,
    total: 0,
  });

  // Trace Events for AgentTimeline
  const [traceEvents, setTraceEvents] = useState([]);
  const [agentTraces, setAgentTraces] = useState([]);

  // Chapter Info
  const [chapterInfo, setChapterInfo] = useState({
    chapter: null,
    chapter_title: null,
    content: null,
  });

  const NO_CHAPTER_KEY = '__no_chapter__';
  const activeChapterKey = chapterInfo.chapter ? String(chapterInfo.chapter) : NO_CHAPTER_KEY;

  const activeChapterKeyRef = useRef(activeChapterKey);
  useEffect(() => {
    activeChapterKeyRef.current = activeChapterKey;
  }, [activeChapterKey]);

  // Draft version state
  const [currentDraftVersion, setCurrentDraftVersion] = useState('v1');

  // Agent mode (for AgentStatusPanel)
  const [agentMode, setAgentMode] = useState('create'); // 'create' | 'edit'
  const [contextDebugByChapter, setContextDebugByChapter] = useState({});

  const agentBusy =
    Boolean(aiLockedChapter) &&
    (Boolean(diffReview) ||
      showPreWriteDialog ||
      status === 'starting' ||
      status === 'waiting_user_input' ||
      isGenerating ||
      streamingState.active);

  const agentChapterKey = agentBusy ? String(aiLockedChapter) : activeChapterKey;

  const isStreamingForActiveChapter = streamingState.active && streamingChapterKeyRef.current === activeChapterKey;

  const isDiffReviewForActiveChapter = Boolean(diffReview) && String(diffReview?.chapterKey || '') === activeChapterKey;

  const lockedOnActiveChapter = agentBusy && String(aiLockedChapter || '') === activeChapterKey;

  const canUseWriter =
    countWords(
      agentBusy ? (manualContentByChapter[String(aiLockedChapter || '')] ?? '') : manualContent,
      writingLanguage,
    ) === 0;

  // 对话以「项目」为单位（而非章节）：整个项目一份长青对话，AI 的撰写/编辑动作仍作用于当前激活章节。
  const projectChatKey = String(projectId || '');
  const messages = useMemo(() => messagesByChapter[projectChatKey] || [], [messagesByChapter, projectChatKey]);
  const progressEvents = useMemo(
    () => progressEventsByChapter[projectChatKey] || [],
    [progressEventsByChapter, projectChatKey],
  );
  const contextDebug = contextDebugByChapter[projectChatKey] || null;

  // 深度思考开关：仅当「写作角色」绑定的模型支持参数级 thinking 切换时，对话栏才显示按钮（能力门控）。
  const { data: llmProfilesData } = useSWR('llm-profiles', () => configAPI.getProfiles().then((r) => r.data), {
    revalidateOnFocus: false,
  });
  const { data: agentAssignmentsData } = useSWR(
    'agent-assignments',
    () => configAPI.getAssignments().then((r) => r.data),
    { revalidateOnFocus: false },
  );
  const writerProfileId = agentAssignmentsData?.writer;
  const writerSupportsThinking = Boolean(
    (Array.isArray(llmProfilesData) ? llmProfilesData.find((p) => p?.id === writerProfileId) : null)?.supports_thinking,
  );
  const [deepThinking, setDeepThinking] = useState(getDeepThinkingPreference);
  const toggleDeepThinking = useCallback(() => {
    setDeepThinking((v) => {
      const next = !v;
      setDeepThinkingPreference(next);
      return next;
    });
  }, []);
  // 待执行的 plan（仿 Claude Code：生成后展示步骤 + 等用户「执行」批准；执行走串行编排器）。
  const [pendingPlan, setPendingPlan] = useState(null);
  const [planExecuting, setPlanExecuting] = useState(false);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [agentTurnMeta, setAgentTurnMeta] = useState(null);

  // 本地长存 + Git-Native 持久化：开项目时优先从后端加载对话历史（扛刷新/重启/清缓存/换机），
  // 失败或空则回退 localStorage 缓存。progressEvents（过程轨迹）仍走本地缓存。
  useEffect(() => {
    if (!projectId) return;
    const key = projectChatKey;
    let cancelled = false;

    const fromLocal = () => {
      try {
        const raw = window.localStorage.getItem(`wenshape_chat_v1_${key}`);
        if (raw) {
          const parsed = JSON.parse(raw);
          const msgs = Array.isArray(parsed?.messages)
            ? parsed.messages.map((m) => ({ ...m, time: m?.time ? new Date(m.time) : new Date() }))
            : [];
          // 过滤掉旧的连接状态事件（已废弃的「连接中断/重连」提示，避免 localStorage 残留再显示）。
          const evs = Array.isArray(parsed?.progressEvents)
            ? parsed.progressEvents.filter((e) => e?.stage !== 'connection')
            : [];
          return { msgs, evs };
        }
      } catch {
        /* 忽略损坏的本地缓存 */
      }
      return { msgs: [], evs: [] };
    };

    const hydrate = async () => {
      let serverMsgs = null;
      try {
        const resp = await sessionAPI.getHistory(key);
        const list = Array.isArray(resp?.data?.messages) ? resp.data.messages : [];
        if (list.length) {
          serverMsgs = list.map((m) => ({
            type: m?.type === 'error' ? 'error' : m?.role || 'system',
            content: String(m?.content ?? ''),
            time: m?.ts ? new Date(m.ts) : new Date(),
          }));
        }
      } catch {
        /* 后端不可用 → 回退本地缓存 */
      }
      if (cancelled) return;
      const local = fromLocal();
      setMessagesByChapter((prev) => ({ ...(prev || {}), [key]: serverMsgs !== null ? serverMsgs : local.msgs }));
      setProgressEventsByChapter((prev) => ({ ...(prev || {}), [key]: local.evs }));
      chatHydratedRef.current = key;
    };

    hydrate();
    return () => {
      cancelled = true;
    };
  }, [projectId, projectChatKey]);

  useEffect(() => {
    if (!projectId) return;
    if (chatHydratedRef.current !== projectChatKey) return; // 等水合完成再写，避免覆盖
    try {
      window.localStorage.setItem(`wenshape_chat_v1_${projectChatKey}`, JSON.stringify({ messages, progressEvents }));
    } catch {
      /* 忽略配额/序列化异常 */
    }
  }, [messages, progressEvents, projectId, projectChatKey]);


  useEffect(() => {
    if (!isGenerating && !canUseWriter && agentMode === 'create') {
      setAgentMode('edit');
    }
    if (!isGenerating && canUseWriter && agentMode === 'edit') {
      setAgentMode('create');
    }
  }, [canUseWriter, agentMode, isGenerating]);

  useEffect(() => {
    if (agentMode !== 'edit') return;
    if (!attachedSelection?.text?.trim()) {
      if (editScope === 'selection') setEditScope('document');
      return;
    }
    if (editScope === 'document') setEditScope('selection');
  }, [agentMode, attachedSelection, editScope]);

  useEffect(() => {
    if (!aiLockedChapter) return;
    if (agentBusy) return;
    setAiLockedChapter(null);
  }, [aiLockedChapter, agentBusy]);

  // Card State
  const [activeCard, setActiveCard] = useState(null);
  const [cardForm, setCardForm] = useState({
    name: '',
    description: '',
    aliases: '',
    stars: 1,
    category: '',
  });

  // SWR for Chapter Content
  const { data: loadedContent, mutate: mutateChapter } = useSWR(
    chapterInfo.chapter ? ['chapter', projectId, chapterInfo.chapter] : null,
    fetchChapterContent,
    {
      revalidateOnFocus: false,
      dedupingInterval: 60000, // Cache for 1 minute before checking again
      keepPreviousData: false, // Don't show previous chapter data while loading (we handle this with manualContent update)
    },
  );

  const { data: volumes = [] } = useSWR(
    // Keep SWR key consistent across the app so volume creation immediately updates all views.
    projectId ? [projectId, 'volumes'] : null,
    () => volumesAPI.list(projectId).then((res) => res.data),
    { revalidateOnFocus: false },
  );

  // Sync SWR data to manualContent
  useEffect(() => {
    if (loadedContent === undefined || state.unsavedChanges) {
      return;
    }
    if (isStreamingForActiveChapter || lockedOnActiveChapter || isDiffReviewForActiveChapter) {
      return;
    }

    const chapterKey = activeChapterKey;
    if (chapterKey === NO_CHAPTER_KEY) return;

    const lastGeneratedForChapter = Boolean(lastGeneratedByChapterRef.current?.[chapterKey]);
    if (lastGeneratedForChapter && manualContent && !(loadedContent || '').trim()) {
      return;
    }

    setManualContentByChapter((prev) => ({ ...(prev || {}), [chapterKey]: loadedContent }));
    setManualContent(loadedContent);
    dispatch({ type: 'SET_WORD_COUNT', payload: countWords(loadedContent, writingLanguage) });
    dispatch({ type: 'SET_SELECTION_COUNT', payload: 0 });
    lastGeneratedByChapterRef.current[chapterKey] = false;
    // Only center cursor if we just switched chapters (optional optimization)
    // dispatch({ type: 'SET_CURSOR_POSITION', payload: { line: 1, column: 1 } });
  }, [
    NO_CHAPTER_KEY,
    activeChapterKey,
    dispatch,
    isDiffReviewForActiveChapter,
    isStreamingForActiveChapter,
    loadedContent,
    lockedOnActiveChapter,
    manualContent,
    state.unsavedChanges,
    writingLanguage,
  ]);

  const loadChapters = useCallback(async () => {
    try {
      const resp = await draftsAPI.listChapters(projectId);
      const list = resp.data || [];
      setChapters(list);
    } catch (e) {
      logger.error('Failed to load chapters:', e);
    }
  }, [projectId]);

  useEffect(() => {
    loadChapters();
  }, [loadChapters]);

  useEffect(() => {
    let active = true;
    const loadTitle = async () => {
      if (!projectId || !chapterInfo.chapter) return;
      if (chapterInfo.chapter_title && chapterInfo.chapter_title.trim()) return;
      try {
        const summaryResp = await draftsAPI.getSummary(projectId, chapterInfo.chapter);
        const summary = summaryResp.data || {};
        const title = summary.title || summary.chapter_title || '';
        if (active && title) {
          setChapterInfo((prev) => ({ ...prev, chapter_title: title }));
        }
      } catch (e) {
        // ignore missing summary
      }
    };
    loadTitle();
    return () => {
      active = false;
    };
  }, [projectId, chapterInfo.chapter, chapterInfo.chapter_title]);

  // 监听 Context 中的 Dialog 状态
  useEffect(() => {
    if (state.createChapterDialogOpen !== showChapterDialog) {
      setShowChapterDialog(state.createChapterDialogOpen);
    }
  }, [showChapterDialog, state.createChapterDialogOpen]);

  const clearDiffReview = useCallback(() => {
    setDiffReview(null);
    setDiffDecisions({});
  }, []);

  const stopStreaming = useCallback(() => {
    if (streamingRef.current?.timer) {
      streamingRef.current.timer();
    }
    streamingRef.current = null;
    setStreamingState({
      active: false,
      progress: 0,
      current: 0,
      total: 0,
    });
  }, []);

  // 将一次完整生成（写全章 / 续写）落为差异提议：原文 vs 生成文 → DiffReviewView 审阅。
  // 与编辑(handleSubmitFeedback)统一为同一种 diff 采纳交互——真相在文件、提议可弃（红线 2）。
  const finalizeDraftAsDiff = useCallback(
    (chapterKey, finalText) => {
      const key = String(chapterKey || '');
      if (!key) return;
      const original = String(streamOriginalByChapterRef.current[key] ?? '');
      const revised = String(finalText || '');
      const isActive = activeChapterKeyRef.current === key;

      // 退化：空结果或与原文一致 → 直接落地，不打扰（稳健兜底）。
      if (!revised.trim() || revised === original) {
        setManualContentByChapter((prev) => ({ ...(prev || {}), [key]: revised }));
        if (isActive) {
          setManualContent(revised);
          dispatch({ type: 'SET_WORD_COUNT', payload: countWords(revised, writingLanguage) });
          dispatch({ type: 'SET_SELECTION_COUNT', payload: 0 });
        }
        delete streamOriginalByChapterRef.current[key];
        return;
      }

      const diff = buildLineDiff(original, revised, { contextLines: 2 });
      const hunksWithReason = (diff.hunks || []).map((hunk) => ({
        ...hunk,
        reason: t('writingSession.draftDiffReason'),
      }));
      const initialDecisions = hunksWithReason.reduce((acc, hunk) => {
        acc[hunk.id] = 'accepted';
        return acc;
      }, {});
      setDiffDecisions(initialDecisions);
      setDiffReview({
        ...diff,
        hunks: hunksWithReason,
        originalContent: original,
        revisedContent: revised,
        chapterKey: key,
      });
      // 正文暂留原文，待用户采纳后才落地修订（接受全部 / 逐块 / 放弃）。
      setManualContentByChapter((prev) => ({ ...(prev || {}), [key]: original }));
      if (isActive) setManualContent(original);
      delete streamOriginalByChapterRef.current[key];
    },
    [dispatch, t, writingLanguage],
  );

  const handleChapterSelect = useCallback(
    async (chapter, presetTitle = '') => {
      const nextChapterKey = chapter ? String(chapter) : NO_CHAPTER_KEY;
      const lockedKey = aiLockedChapterRef.current ? String(aiLockedChapterRef.current) : null;
      const preserveAgent = Boolean(lockedKey) && agentBusy;

      // 缓存当前章节内容，避免切章丢失
      if (chapterInfo.chapter) {
        const currentKey = String(chapterInfo.chapter);
        setManualContentByChapter((prev) => ({ ...(prev || {}), [currentKey]: manualContent }));
      }

      // 非写作/编辑进行中：切章时清理流式与差异态
      if (!preserveAgent) {
        stopStreaming();
        clearDiffReview();
        setStatus('editing');
      } else if (lockedKey && nextChapterKey !== lockedKey) {
        pushNotice(t('writingSession.chapterLockedNotice').replace('{n}', lockedKey).replace('{m}', nextChapterKey));
      }

      // Just set the chapter, let SWR handle fetching
      setChapterInfo({ chapter, chapter_title: presetTitle || '', content: '' }); // content will be filled by SWR
      setSelectionInfo({ start: 0, end: 0, text: '' });
      setAttachedSelection(null);
      setEditScope('document');

      // 优先使用本地缓存，减少切章时的"空白闪烁"
      if (nextChapterKey && nextChapterKey !== NO_CHAPTER_KEY) {
        const cached = manualContentByChapterRef.current?.[nextChapterKey];
        if (typeof cached === 'string') {
          setManualContent(cached);
          dispatch({ type: 'SET_WORD_COUNT', payload: countWords(cached, writingLanguage) });
          dispatch({ type: 'SET_SELECTION_COUNT', payload: 0 });
        } else {
          setManualContent('');
          dispatch({ type: 'SET_WORD_COUNT', payload: 0 });
          dispatch({ type: 'SET_SELECTION_COUNT', payload: 0 });
        }
      }
      try {
        const summaryResp = await draftsAPI.getSummary(projectId, chapter);
        const summary = summaryResp.data || {};
        const normalizedChapter = summary.chapter || chapter;
        const title = summary.title || summary.chapter_title || '';
        setChapterInfo((prev) => ({
          ...prev,
          chapter: normalizedChapter,
          chapter_title: title || prev.chapter_title || '',
        }));
        if (normalizedChapter !== chapter) {
          dispatch({
            type: 'SET_ACTIVE_DOCUMENT',
            payload: { type: 'chapter', id: normalizedChapter, title: title || presetTitle || '' },
          });
        }
      } catch (e) {
        // Summary may not exist yet.
      }
    },
    [
      NO_CHAPTER_KEY,
      agentBusy,
      chapterInfo.chapter,
      clearDiffReview,
      dispatch,
      manualContent,
      projectId,
      pushNotice,
      stopStreaming,
      t,
      writingLanguage,
    ],
  );

  const handleChapterCreate = async (chapterData) => {
    // Handle object from ChapterCreateDialog or direct arguments
    const chapterNum = typeof chapterData === 'object' ? chapterData.id : chapterData;
    const chapterTitle = typeof chapterData === 'object' ? chapterData.title : arguments[1];

    // Persist the new chapter immediately
    setIsSaving(true);
    let normalizedChapter = chapterNum;
    try {
      const resp = await draftsAPI.updateContent(projectId, chapterNum, {
        content: '',
        title: chapterTitle,
      });
      normalizedChapter = resp.data?.chapter || chapterNum;
      addMessage('system', t('writingSession.chapterCreated').replace('{id}', normalizedChapter), normalizedChapter);
      dispatch({
        type: 'SET_ACTIVE_DOCUMENT',
        payload: { type: 'chapter', id: normalizedChapter, title: chapterTitle || '' },
      });
    } catch (e) {
      addMessage('error', t('writingSession.chapterCreateFailed') + extractErrorDetail(e));
    } finally {
      setIsSaving(false);
    }

    setChapterInfo({ chapter: normalizedChapter, chapter_title: chapterTitle, content: '' });
    setManualContent('');
    stopStreaming();
    clearDiffReview();
    setShowChapterDialog(false);
    setStatus('idle');
    await loadChapters();
  };

  const addMessage = useCallback(
    (type, content) => {
      const key = projectChatKey;
      if (!key) {
        return;
      }
      setMessagesByChapter((prev) => {
        const next = { ...(prev || {}) };
        const existing = Array.isArray(next[key]) ? next[key] : [];
        next[key] = [...existing, { type, content, time: new Date() }].slice(-200);
        return next;
      });
      // Git-Native 持久化：追加到后端会话历史（fire-and-forget，失败不影响交互；localStorage 仍作离线缓存）。
      const role = type === 'user' || type === 'assistant' || type === 'system' ? type : 'system';
      sessionAPI
        .appendHistory(key, {
          role,
          content: String(content ?? ''),
          type: role === type ? undefined : type,
          ts: Date.now(),
        })
        .catch(() => {});
    },
    [projectChatKey],
  );

  const appendProgressEvent = useCallback(
    (partial) => {
      const key = projectChatKey;
      if (!key) {
        return;
      }
      setProgressEventsByChapter((prev) => {
        const next = { ...(prev || {}) };
        const existing = Array.isArray(next[key]) ? next[key] : [];
        const last = existing[existing.length - 1];
        // 修复：连续的流式 thinking token 累积到同一思考块（一个折叠头 + 完整推理），
        // 而非每个 token 渲染成独立的「思考」块。被其它事件（工具调用等）打断后再思考则另起一块。
        if (partial.stage === 'thinking' && last && last.stage === 'thinking') {
          const mergedNote = `${last.note || ''}${partial.note || ''}`;
          const updated = {
            ...last,
            note: mergedNote,
            message: mergedNote.length > 80 ? `${mergedNote.slice(0, 80)}…` : mergedNote,
          };
          next[key] = [...existing.slice(0, -1), updated];
          return next;
        }
        const event = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          timestamp: Date.now(),
          ...partial,
        };
        next[key] = [...existing.slice(-199), event];
        return next;
      });
    },
    [projectChatKey],
  );

  // Auto Save（类似 VSCode：检测到变更后自动保存）
  const autosaveTimerRef = useRef(null);
  const autosaveInFlightRef = useRef(false);
  const autosaveLastPayloadRef = useRef({ chapter: null, content: null, title: null });

  useEffect(() => {
    if (!state.unsavedChanges) return;
    if (!projectId || !chapterInfo.chapter) return;
    if (isStreamingForActiveChapter || lockedOnActiveChapter || isDiffReviewForActiveChapter) return;

    const nextContent = String(manualContent || '');
    const nextTitle = String(chapterInfo.chapter_title || '').trim() || null;

    const last = autosaveLastPayloadRef.current || {};
    const sameChapter = String(last.chapter || '') === String(chapterInfo.chapter);
    const sameContent = sameChapter && String(last.content || '') === nextContent;
    const sameTitle = sameChapter && (last.title || null) === nextTitle;
    if (sameContent && sameTitle) return;

    if (autosaveTimerRef.current) {
      window.clearTimeout(autosaveTimerRef.current);
    }

    autosaveTimerRef.current = window.setTimeout(async () => {
      if (autosaveInFlightRef.current) return;
      autosaveInFlightRef.current = true;
      try {
        const payload = { content: nextContent };
        if (nextTitle) payload.title = nextTitle;

        const resp = await draftsAPI.autosaveContent(projectId, chapterInfo.chapter, payload);
        if (resp.data?.success) {
          autosaveLastPayloadRef.current = { chapter: chapterInfo.chapter, content: nextContent, title: nextTitle };
          await mutateChapter(nextContent, false);
          dispatch({ type: 'SET_AUTOSAVED' });
        }
      } catch (e) {
        dispatch({ type: 'SET_UNSAVED' });
        addMessage('error', t('writingSession.autoSaveFailed') + extractErrorDetail(e));
      } finally {
        autosaveInFlightRef.current = false;
      }
    }, 1200);

    return () => {
      if (autosaveTimerRef.current) {
        window.clearTimeout(autosaveTimerRef.current);
        autosaveTimerRef.current = null;
      }
    };
  }, [
    addMessage,
    chapterInfo.chapter,
    chapterInfo.chapter_title,
    dispatch,
    isDiffReviewForActiveChapter,
    isStreamingForActiveChapter,
    lockedOnActiveChapter,
    manualContent,
    mutateChapter,
    projectId,
    state.unsavedChanges,
    t,
  ]);

  // 当资源管理器清空/删除当前章节时，主动回到空态，避免编辑区残留旧章节内容
  useEffect(() => {
    if (state.activeDocument) return;
    stopStreaming();
    clearDiffReview();
    setActiveCard(null);
    setChapterInfo({ chapter: null, chapter_title: null, content: null });
    setManualContent('');
    setStatus('idle');
  }, [clearDiffReview, state.activeDocument, stopStreaming]);

  const startStreamingDraft = useCallback(
    (targetText, options = {}) => {
      const { onComplete, chapterKey } = options;
      const resolvedChapterKey = chapterKey ? String(chapterKey) : activeChapterKeyRef.current;
      stopStreaming();
      streamingChapterKeyRef.current = resolvedChapterKey;

      const safeText = targetText || '';
      if (!safeText) {
        setManualContentByChapter((prev) => ({ ...(prev || {}), [resolvedChapterKey]: '' }));
        if (activeChapterKeyRef.current === resolvedChapterKey) {
          setManualContent('');
        }
        setIsGenerating(false);
        streamingChapterKeyRef.current = null;
        onComplete?.();
        return;
      }

      dispatch({ type: 'SET_UNSAVED' });
      lastGeneratedByChapterRef.current[resolvedChapterKey] = true;

      // 快照流式前原文（仅读取 prev，不改状态），结束时据此生成 diff 提议。
      setManualContentByChapter((prev) => {
        streamOriginalByChapterRef.current[resolvedChapterKey] = String(prev?.[resolvedChapterKey] ?? '');
        return prev;
      });

      // 直出模式：无逐字动画，直接生成 diff 交付审阅。
      if (!getStreamingPreference()) {
        setStreamingState({ active: false, progress: 100, current: safeText.length, total: safeText.length });
        setIsGenerating(false);
        streamingChapterKeyRef.current = null;
        finalizeDraftAsDiff(resolvedChapterKey, safeText);
        if (activeChapterKeyRef.current !== resolvedChapterKey) {
          pushNotice(t('writingSession.chapterDone').replace('{n}', resolvedChapterKey));
        }
        onComplete?.();
        return;
      }

      // 流式动画模式：新文在 diff 绿块内逐字增长，结束转 DiffReviewView 审阅。
      setIsGenerating(true);
      const total = safeText.length;
      const charsPerSecond = Math.min(420, Math.max(180, Math.round(total / 3)));
      let index = 0;
      let lastTs = performance.now();
      let rafId = null;

      setManualContentByChapter((prev) => ({ ...(prev || {}), [resolvedChapterKey]: '' }));
      if (activeChapterKeyRef.current === resolvedChapterKey) {
        setManualContent('');
      }
      setStreamingState({
        active: true,
        progress: 0,
        current: 0,
        total,
      });
      lastGeneratedByChapterRef.current[resolvedChapterKey] = true;

      const initialBurst = Math.min(total, Math.max(12, Math.floor(total * 0.03)));
      if (initialBurst > 0) {
        index = initialBurst;
        const burstText = safeText.slice(0, index);
        setManualContentByChapter((prev) => ({ ...(prev || {}), [resolvedChapterKey]: burstText }));
        if (activeChapterKeyRef.current === resolvedChapterKey) {
          setManualContent(burstText);
        }
        setStreamingState({
          active: index < total,
          progress: Math.round((index / total) * 100),
          current: index,
          total,
        });
      }

      const tick = (ts) => {
        const delta = Math.max(0, ts - lastTs);
        const increment = Math.max(1, Math.floor((delta / 1000) * charsPerSecond));
        index = Math.min(total, index + increment);
        lastTs = ts;

        const partial = safeText.slice(0, index);
        setManualContentByChapter((prev) => ({ ...(prev || {}), [resolvedChapterKey]: partial }));
        if (activeChapterKeyRef.current === resolvedChapterKey) {
          setManualContent(partial);
        }
        setStreamingState({
          active: index < total,
          progress: Math.round((index / total) * 100),
          current: index,
          total,
        });

        if (index >= total) {
          streamingRef.current = null;
          setIsGenerating(false);
          streamingChapterKeyRef.current = null;
          finalizeDraftAsDiff(resolvedChapterKey, safeText);
          if (activeChapterKeyRef.current !== resolvedChapterKey) {
            pushNotice(t('writingSession.chapterDone').replace('{n}', resolvedChapterKey));
          }
          onComplete?.();
          return;
        }

        rafId = window.requestAnimationFrame(tick);
      };

      rafId = window.requestAnimationFrame(tick);
      streamingRef.current = {
        timer: () => {
          if (rafId) window.cancelAnimationFrame(rafId);
        },
      };
    },
    [dispatch, pushNotice, stopStreaming, t, finalizeDraftAsDiff],
  );

  useEffect(() => {
    return () => {
      stopStreaming();
    };
  }, [stopStreaming]);

  // 监听 Context 中的文档选择（章节或卡片）
  useEffect(() => {
    if (!state.activeDocument) return;

    if (state.activeDocument.type === 'chapter' && state.activeDocument.id) {
      setActiveCard(null); // Clear card state
      const presetTitle =
        state.activeDocument.data?.title ||
        state.activeDocument.data?.chapter_title ||
        state.activeDocument.title ||
        state.activeDocument.chapter_title ||
        '';
      handleChapterSelect(state.activeDocument.id, presetTitle);
    } else if (['character', 'world'].includes(state.activeDocument.type)) {
      // Switch to Card Mode
      stopStreaming();
      clearDiffReview();
      setChapterInfo({ chapter: null, chapter_title: null, content: null });

      // Initial setup with basic info
      const cardData = state.activeDocument.data || { name: state.activeDocument.id };
      const originalName = state.activeDocument.id || cardData.name || '';
      const isNew = Boolean(state.activeDocument.isNew || cardData.isNew || !originalName);
      setActiveCard({
        ...cardData,
        type: state.activeDocument.type,
        isNew,
        originalName,
      });
      setCardForm({
        name: cardData.name || '',
        description: '',
        aliases: formatListInput(cardData.aliases),
        stars: normalizeStars(cardData.stars),
        category: cardData.category || '',
      });
      setStatus('card_editing');

      // Fetch full details
      const fetchCardDetails = async () => {
        try {
          let resp;
          if (state.activeDocument.type === 'character') {
            resp = await cardsAPI.getCharacter(projectId, state.activeDocument.id);
          } else {
            resp = await cardsAPI.getWorld(projectId, state.activeDocument.id);
          }
          const fullData = resp?.data || {};
          setCardForm({
            name: fullData.name || cardData.name || '',
            description: fullData.description || '',
            aliases: formatListInput(fullData.aliases),
            stars: normalizeStars(fullData.stars),
            category: fullData.category || '',
          });
        } catch (e) {
          logger.error('Failed to fetch card details', e);
          addMessage('error', t('writingSession.loadCardFailed') + extractErrorDetail(e));
        }
      };

      if (state.activeDocument.id) {
        fetchCardDetails();
      }
    }
  }, [addMessage, clearDiffReview, handleChapterSelect, projectId, state.activeDocument, stopStreaming, t]);

  // Handlers
  const handleStart = async (chapter, mode, instruction = null) => {
    if (!chapter) {
      alert(t('writingSession.selectChapterFirst'));
      return;
    }
    const chapterKey = String(chapter);
    setAiLockedChapter(chapterKey);
    setManualContentByChapter((prev) => {
      const next = { ...(prev || {}) };
      if (next[chapterKey] === undefined) {
        next[chapterKey] = manualContent;
      }
      return next;
    });

    stopStreaming();
    clearDiffReview();
    serverStreamActiveRef.current = false;
    serverStreamUsedRef.current = false;
    setStatus('starting');
    setIsGenerating(true);
    setContextDebugByChapter((prev) => ({ ...(prev || {}), [projectChatKey]: null }));

    setAgentMode('create');
    appendProgressEvent({ stage: 'session_start', message: t('writingSession.preparingContext') }, chapterKey);

    try {
      const payload = {
        language: requestLanguage,
        chapter: String(chapter),
        chapter_title: chapterInfo.chapter_title || t('writingSession.chapterFallback').replace('{n}', chapter),
        chapter_goal: instruction || 'Auto-generation based on context',
        target_word_count: 3000,
        dialog_max_chars: dialogMaxChars,
      };

      const resp = await sessionAPI.start(projectId, payload);
      const result = resp.data;

      if (!result.success) {
        if (result.cancelled) return; // 用户主动取消，静默退出
        throw new Error(result.error || t('writingSession.sessionStartFailed'));
      }
      if (result.status === 'waiting_user_input' && result.questions?.length) {
        if (result.scene_brief) {
          setSceneBrief(result.scene_brief);
          appendProgressEvent(
            { stage: 'scene_brief', message: t('writingSession.sceneBriefGenerated'), payload: result.scene_brief },
            chapterKey,
          );
        }
        setContextDebugByChapter((prev) => ({ ...(prev || {}), [projectChatKey]: result.context_debug || null }));
        setPreWriteQuestions(result.questions);
        setPendingStartPayload(payload);
        setShowPreWriteDialog(true);
        setStatus('waiting_user_input');
        setIsGenerating(false);
        return;
      }

      if (result.scene_brief) {
        setSceneBrief(result.scene_brief);
        appendProgressEvent(
          { stage: 'scene_brief', message: t('writingSession.sceneBriefGenerated'), payload: result.scene_brief },
          chapterKey,
        );
      }
      setContextDebugByChapter((prev) => ({ ...(prev || {}), [projectChatKey]: result.context_debug || null }));

      if (result.draft_v1) {
        setDraftV1(result.draft_v1);
      }

      const finalDraft = result.draft_v2 || result.draft_v1;
      const shouldUseHttpDraft = !serverStreamActiveRef.current && !serverStreamUsedRef.current;
      if (finalDraft && shouldUseHttpDraft) {
        setCurrentDraft(finalDraft);
        setCurrentDraftVersion(result.draft_v2 ? 'v2' : 'v1');
        startStreamingDraft(finalDraft.content || '', { chapterKey });
      } else if (shouldUseHttpDraft) {
        setIsGenerating(false);
      }

      if (result.proposals) {
        setProposals(result.proposals);
      }

      setStatus('waiting_feedback');
      if (!serverStreamActiveRef.current && !serverStreamUsedRef.current) {
        addMessage('assistant', t('writingSession.draftGenerated'), chapterKey);
      }
      setPendingStartPayload(null);
    } catch (e) {
      addMessage('error', t('writingSession.startFailed') + extractErrorDetail(e), chapterKey);
      setStatus('idle');
      setIsGenerating(false);
    }
  };

  const handleCancel = async () => {
    if (isCancelling || !projectId) return;
    setIsCancelling(true);
    // 立即关闭写作前面板，防止取消后面板残留
    setShowPreWriteDialog(false);
    setPreWriteQuestions([]);
    setPendingStartPayload(null);
    // 同时清理编辑状态，防止编辑指令在后续流程中被重复应用
    setFeedback('');
    lastFeedbackRef.current = '';
    clearDiffReview();
    try {
      await sessionAPI.cancel(projectId);
    } catch (e) {
      // 即使请求失败也重置前端状态，避免界面卡死
    } finally {
      stopStreaming();
      setIsGenerating(false);
      setStatus('idle');
      setAiLockedChapter(null);
      setIsCancelling(false);
    }
  };

  const handlePreWriteConfirm = async (answers) => {
    if (!pendingStartPayload) return;
    const startPayload = pendingStartPayload;
    const chapterKey = startPayload?.chapter ? String(startPayload.chapter) : activeChapterKey;
    if (chapterKey && chapterKey !== NO_CHAPTER_KEY) {
      setAiLockedChapter(chapterKey);
    }
    setShowPreWriteDialog(false);
    stopStreaming();
    clearDiffReview();
    serverStreamActiveRef.current = false;
    serverStreamUsedRef.current = false;
    setIsGenerating(true);

    try {
      const resp = await sessionAPI.answerQuestions(projectId, {
        ...startPayload,
        answers,
      });
      const result = resp.data;

      if (!result.success) {
        if (result.cancelled) return; // 用户主动取消，静默退出
        throw new Error(result.error || t('writingSession.answerFailed'));
      }

      if (result.status === 'waiting_user_input' && result.questions?.length) {
        setContextDebugByChapter((prev) => ({ ...(prev || {}), [projectChatKey]: result.context_debug || null }));
        if (result.scene_brief) {
          setSceneBrief(result.scene_brief);
          appendProgressEvent(
            { stage: 'scene_brief', message: t('writingSession.sceneBriefGenerated'), payload: result.scene_brief },
            chapterKey,
          );
        }
        setPreWriteQuestions(result.questions);
        setPendingStartPayload(startPayload);
        setShowPreWriteDialog(true);
        setStatus('waiting_user_input');
        setIsGenerating(false);
        return;
      }

      if (result.scene_brief) {
        setSceneBrief(result.scene_brief);
        appendProgressEvent(
          { stage: 'scene_brief', message: t('writingSession.sceneBriefGenerated'), payload: result.scene_brief },
          chapterKey,
        );
      }
      setContextDebugByChapter((prev) => ({ ...(prev || {}), [projectChatKey]: result.context_debug || null }));
      if (result.draft_v1) {
        setDraftV1(result.draft_v1);
      }

      const finalDraft = result.draft_v2 || result.draft_v1;
      const shouldUseHttpDraft = !serverStreamActiveRef.current && !serverStreamUsedRef.current;
      if (finalDraft && shouldUseHttpDraft) {
        setCurrentDraft(finalDraft);
        setCurrentDraftVersion(result.draft_v2 ? 'v2' : 'v1');
        startStreamingDraft(finalDraft.content || '', { chapterKey });
      } else if (shouldUseHttpDraft) {
        setIsGenerating(false);
      }

      if (result.proposals) {
        setProposals(result.proposals);
      }

      setStatus('waiting_feedback');
      if (!serverStreamActiveRef.current && !serverStreamUsedRef.current) {
        addMessage('assistant', t('writingSession.draftGenerated'), chapterKey);
      }
      setPendingStartPayload(null);
    } catch (e) {
      addMessage('error', t('writingSession.generateFailed') + extractErrorDetail(e), chapterKey);
      setStatus('idle');
      setIsGenerating(false);
    }
  };

  const handlePreWriteSkip = () => {
    handlePreWriteConfirm([]);
  };

  const handleSceneBrief = useCallback(
    (data, chapterOverride = null) => {
      setSceneBrief(data);
      appendProgressEvent(
        { stage: 'scene_brief', message: t('writingSession.sceneBriefGenerated'), payload: data },
        chapterOverride,
      );
    },
    [appendProgressEvent, t],
  );

  const handleDraftV1 = useCallback(
    (data, chapterOverride = null) => {
      if (serverStreamActiveRef.current || serverStreamUsedRef.current) {
        return;
      }
      setDraftV1(data);
      clearDiffReview();
      const chapterKey = chapterOverride ? String(chapterOverride) : activeChapterKeyRef.current;
      if (chapterKey && chapterKey !== NO_CHAPTER_KEY) {
        setAiLockedChapter(chapterKey);
      }
      startStreamingDraft(data.content || '', {
        chapterKey,
      });
      setStatus('waiting_feedback');
      addMessage('assistant', t('writingSession.draftGenerated'), chapterOverride);
    },
    [NO_CHAPTER_KEY, addMessage, clearDiffReview, startStreamingDraft, t],
  );

  const handleFinalDraft = useCallback(
    (data, chapterOverride = null) => {
      if (serverStreamActiveRef.current || serverStreamUsedRef.current) {
        return;
      }
      setCurrentDraft(data);
      clearDiffReview();
      const chapterKey = chapterOverride ? String(chapterOverride) : activeChapterKeyRef.current;
      if (chapterKey && chapterKey !== NO_CHAPTER_KEY) {
        setAiLockedChapter(chapterKey);
      }
      startStreamingDraft(data.content || '', {
        chapterKey,
      });
      setStatus('completed');
      addMessage('assistant', t('writingSession.finalDraftDone'), chapterOverride);
    },
    [NO_CHAPTER_KEY, addMessage, clearDiffReview, startStreamingDraft, t],
  );

  useWritingSessionRealtime({
    projectId,
    noChapterKey: NO_CHAPTER_KEY,
    addMessage,
    appendProgressEvent,
    clearDiffReview,
    currentDraftVersion,
    dispatch,
    handleDraftV1,
    handleFinalDraft,
    handleSceneBrief,
    pushNotice,
    serverStreamActiveRef,
    serverStreamUsedRef,
    setAgentTraces,
    setAiLockedChapter,
    setCurrentDraft,
    setCurrentDraftVersion,
    setIsGenerating,
    setManualContent,
    setManualContentByChapter,
    setProposals,
    setStatus,
    setStreamingState,
    setTraceEvents,
    stopStreaming,
    streamBufferByChapterRef,
    streamFlushRafByChapterRef,
    streamingChapterKeyRef,
    streamTextByChapterRef,
    t,
    traceWsRef,
    wsRef,
    wsStatusRef,
    activeChapterKeyRef,
    lastGeneratedByChapterRef,
    writingLanguage,
    streamOriginalByChapterRef,
    onStreamFinalize: finalizeDraftAsDiff,
  });

  const handleSubmitFeedback = async (feedbackOverride, { skipUserMessage = false } = {}) => {
    const textToSubmit = typeof feedbackOverride === 'string' ? feedbackOverride : feedback;
    if (!textToSubmit?.trim()) return;

    try {
      const normalizeLineEndings = (text) => String(text || '').replace(/\r\n/g, '\n');
      const baseContent = normalizeLineEndings(manualContent);
      const chapterKey = chapterInfo.chapter ? String(chapterInfo.chapter) : activeChapterKey;
      if (chapterKey && chapterKey !== NO_CHAPTER_KEY) {
        setAiLockedChapter(chapterKey);
      }
      setIsGenerating(true);
      setStatus('editing');

      setAgentMode('edit');

      stopStreaming();
      clearDiffReview();
      lastFeedbackRef.current = textToSubmit;

      if (!skipUserMessage) addMessage('user', t('writingSession.editInstruction') + textToSubmit);
      appendProgressEvent({ stage: 'edit_suggest', message: t('writingSession.generatingDiff') });
      setFeedback('');

      const payload = {
        chapter: chapterInfo.chapter ? String(chapterInfo.chapter) : null,
        content: baseContent,
        instruction: textToSubmit,
        dialog_max_chars: dialogMaxChars,
      };

      // Cursor 式选区即编辑目标：优先用已附加选区；否则若编辑器有实时选区也直接用它；
      // 两者皆无 → 整篇编辑（与历史行为一致）。让用户"划词 + 说改法"即可，无需手动附加。
      const scopedSelection =
        editScope === 'selection' && attachedSelection?.text?.trim()
          ? attachedSelection
          : selectionInfo?.text?.trim()
            ? selectionInfo
            : null;
      if (scopedSelection) {
        const selectionText = String(scopedSelection.text || '');
        const selectionStart = Math.max(0, Math.min(Number(scopedSelection.start || 0), baseContent.length));
        const selectionEnd = Math.max(0, Math.min(Number(scopedSelection.end || 0), baseContent.length));
        payload.selection_text = selectionText;
        payload.selection_start = Math.min(selectionStart, selectionEnd);
        payload.selection_end = Math.max(selectionStart, selectionEnd);
      }

      const resp = await sessionAPI.suggestEdit(projectId, payload);

      const result = resp.data;
      if (result.success) {
        let nextContent = normalizeLineEndings(result.revised_content);
        const tailFix = stabilizeRevisionTail(baseContent, nextContent, textToSubmit);
        if (tailFix.applied) {
          nextContent = normalizeLineEndings(tailFix.text);
          addMessage('system', t('writingSession.diffTruncationWarning'));
        }

        const diff = buildLineDiff(baseContent, nextContent, { contextLines: 2 });
        const hasChanges = Boolean((diff.stats?.additions || 0) + (diff.stats?.deletions || 0));

        if (!hasChanges) {
          throw new Error(t('writingSession.diffGenerateFailed'));
        }

        appendProgressEvent({
          stage: 'edit_suggest_done',
          message: t('writingSession.diffGenerated')
            .replace('{add}', diff.stats.additions || 0)
            .replace('{del}', diff.stats.deletions || 0),
        });

        // 统一收口：edit 与 write 走同一条流式 diff 管线。editor 的精确 patch 结果(nextContent)
        // 作为目标正文，经 startStreamingDraft 流式呈现 + finalizeDraftAsDiff(原文 vs 目标 → diff 审阅)，
        // 保留 editor 精确性的同时，体验与写全章完全统一(对齐 AI coding 的统一编辑形态)。
        setManualContentByChapter((prev) => ({ ...(prev || {}), [chapterKey]: baseContent }));
        startStreamingDraft(nextContent, { chapterKey });
        setStatus('waiting_feedback');
        addMessage('assistant', t('writingSession.diffReady'));
      } else {
        throw new Error(result.error || 'Edit failed');
      }
    } catch (e) {
      addMessage('error', t('writingSession.editFailed') + extractErrorDetail(e));
      setIsGenerating(false);
      setStatus('waiting_feedback');
    }
  };

  const handleAcceptAllDiff = () => {
    if (!diffReview) return;
    const nextContent = diffReview.revisedContent || '';
    if ((loadedContent ?? '') !== nextContent) {
      dispatch({ type: 'SET_UNSAVED' });
    }
    setManualContent(nextContent);
    if (diffReview.chapterKey) {
      const key = String(diffReview.chapterKey);
      setManualContentByChapter((prev) => ({ ...(prev || {}), [key]: nextContent }));
    }
    dispatch({ type: 'SET_WORD_COUNT', payload: countWords(nextContent, writingLanguage) });
    dispatch({ type: 'SET_SELECTION_COUNT', payload: 0 });
    clearDiffReview();
  };

  const handleRejectAllDiff = () => {
    if (!diffReview) return;
    const nextContent = diffReview.originalContent || '';
    if ((loadedContent ?? '') !== nextContent) {
      dispatch({ type: 'SET_UNSAVED' });
    }
    setManualContent(nextContent);
    if (diffReview.chapterKey) {
      const key = String(diffReview.chapterKey);
      setManualContentByChapter((prev) => ({ ...(prev || {}), [key]: nextContent }));
    }
    dispatch({ type: 'SET_WORD_COUNT', payload: countWords(nextContent, writingLanguage) });
    dispatch({ type: 'SET_SELECTION_COUNT', payload: 0 });
    clearDiffReview();
  };

  const handleAcceptDiffHunk = (hunkId) => {
    setDiffDecisions((prev) => {
      const next = { ...(prev || {}) };
      const current = next[hunkId];
      next[hunkId] = current === 'accepted' ? 'pending' : 'accepted';
      return next;
    });
  };

  const handleRejectDiffHunk = (hunkId) => {
    setDiffDecisions((prev) => {
      const next = { ...(prev || {}) };
      const current = next[hunkId];
      next[hunkId] = current === 'rejected' ? 'pending' : 'rejected';
      return next;
    });
  };

  const handleApplySelectedDiff = () => {
    if (!diffReview) return;
    const originalLines = diffReview.originalLines || (diffReview.originalContent || '').split('\n');
    const ops = diffReview.ops || [];
    const hasDecisions = Object.keys(diffDecisions || {}).length > 0;
    const nextContent = hasDecisions
      ? applyDiffOpsWithDecisions(originalLines, ops, diffDecisions)
      : diffReview.revisedContent || '';
    if ((loadedContent ?? '') !== nextContent) {
      dispatch({ type: 'SET_UNSAVED' });
    }
    setManualContent(nextContent);
    if (diffReview.chapterKey) {
      const key = String(diffReview.chapterKey);
      setManualContentByChapter((prev) => ({ ...(prev || {}), [key]: nextContent }));
    }
    dispatch({ type: 'SET_WORD_COUNT', payload: countWords(nextContent, writingLanguage) });
    dispatch({ type: 'SET_SELECTION_COUNT', payload: 0 });
    clearDiffReview();
  };

  const saveDraftContent = async () => {
    if (!chapterInfo.chapter) return { success: false };
    const trimmedTitle = String(chapterInfo.chapter_title || '').trim();
    const payload = { content: manualContent };
    if (trimmedTitle) {
      payload.title = trimmedTitle;
    }
    const resp = await draftsAPI.updateContent(projectId, chapterInfo.chapter, payload);
    if (resp.data?.success) {
      const normalizedChapter = resp.data?.chapter || chapterInfo.chapter;
      if (normalizedChapter && normalizedChapter !== chapterInfo.chapter) {
        setChapterInfo((prev) => ({ ...prev, chapter: normalizedChapter }));
        dispatch({ type: 'SET_ACTIVE_DOCUMENT', payload: { type: 'chapter', id: normalizedChapter } });
        await loadChapters();
      }
      if (typeof resp.data?.title === 'string' && resp.data.title.trim()) {
        setChapterInfo((prev) => ({ ...prev, chapter_title: resp.data.title }));
      }
      dispatch({ type: 'SET_SAVED' });
      mutateChapter(manualContent, false);
    }
    return resp.data;
  };

  const handleManualSave = async () => {
    if (!chapterInfo.chapter) return;
    setIsSaving(true);
    try {
      const result = await saveDraftContent();
      if (result?.success) {
        addMessage('system', '\u8349\u7a3f\u5df2\u4fdd\u5b58');
      }
    } catch (e) {
      addMessage('error', '\u4fdd\u5b58\u5931\u8d25: ' + extractErrorDetail(e));
    } finally {
      setIsSaving(false);
    }
  };

  const handleAnalyzeAndSave = async () => {
    if (!chapterInfo.chapter) return;
    setAnalysisLoading(true);
    try {
      const saved = await saveDraftContent();
      if (!saved?.success) {
        throw new Error(saved?.message || '\u4fdd\u5b58\u5931\u8d25');
      }
      const normalizedChapter = saved?.chapter || chapterInfo.chapter;
      const resp = await sessionAPI.analyze(projectId, {
        language: requestLanguage,
        chapter: normalizedChapter,
        content: manualContent,
        chapter_title: chapterInfo.chapter_title || '',
      });
      if (resp.data?.success) {
        setAnalysisItems([{ chapter: normalizedChapter, analysis: resp.data.analysis || {} }]);
        setAnalysisDialogOpen(true);
        addMessage('system', '\u5206\u6790\u5b8c\u6210，\u8bf7\u786e\u8ba4\u5e76\u4fdd\u5b58\u3002');
      } else {
        throw new Error(resp.data?.error || '\u5206\u6790\u5931\u8d25');
      }
    } catch (e) {
      addMessage('error', '\u5206\u6790\u5931\u8d25: ' + extractErrorDetail(e));
    } finally {
      setAnalysisLoading(false);
    }
  };

  const handleSaveAnalysis = async (payload) => {
    setAnalysisSaving(true);
    try {
      if (Array.isArray(payload)) {
        const resp = await sessionAPI.saveAnalysisBatch(projectId, {
          language: requestLanguage,
          items: payload,
          overwrite: true,
        });
        if (!resp.data?.success) {
          throw new Error(resp.data?.error || '\u5206\u6790\u5931\u8d25');
        }
      } else if (chapterInfo.chapter) {
        const resp = await sessionAPI.saveAnalysis(projectId, {
          language: requestLanguage,
          chapter: chapterInfo.chapter,
          analysis: payload,
          overwrite: true,
        });
        if (!resp.data?.success) {
          throw new Error(resp.data?.error || '\u5206\u6790\u5931\u8d25');
        }
      }
      addMessage('system', '\u5206\u6790\u4fdd\u5b58\u5b8c\u6210');
      setAnalysisDialogOpen(false);
      setAnalysisItems([]);
    } catch (e) {
      addMessage('error', '\u4fdd\u5b58\u5931\u8d25: ' + extractErrorDetail(e));
    } finally {
      setAnalysisSaving(false);
    }
  };

  // Phase 4.3: Handle user answer for AskUser
  // Card Handlers
  const handleCardSave = async () => {
    if (!activeCard) return;
    setIsSaving(true);
    try {
      const name = (cardForm.name || '').trim();
      if (!name) {
        throw new Error(t('writingSession.cardNameRequired'));
      }
      const stars = normalizeStars(cardForm.stars);
      const aliases = parseListInput(cardForm.aliases);
      if (activeCard.type === 'character') {
        const payload = {
          name,
          description: cardForm.description || '',
          aliases,
          stars,
        };
        if (activeCard.isNew || !activeCard.originalName) {
          await cardsAPI.createCharacter(projectId, payload);
        } else if (activeCard.originalName !== name) {
          await cardsAPI.createCharacter(projectId, payload);
          await cardsAPI.deleteCharacter(projectId, activeCard.originalName);
        } else {
          await cardsAPI.updateCharacter(projectId, activeCard.originalName, payload);
        }
      } else {
        const payload = {
          name,
          description: cardForm.description || '',
          aliases,
          category: (cardForm.category || '').trim(),
          stars,
        };
        if (activeCard.isNew || !activeCard.originalName) {
          await cardsAPI.createWorld(projectId, payload);
        } else if (activeCard.originalName !== name) {
          await cardsAPI.createWorld(projectId, payload);
          await cardsAPI.deleteWorld(projectId, activeCard.originalName);
        } else {
          await cardsAPI.updateWorld(projectId, activeCard.originalName, payload);
        }
      }
      try {
        const refreshed =
          activeCard.type === 'character'
            ? await cardsAPI.getCharacter(projectId, name)
            : await cardsAPI.getWorld(projectId, name);
        const refreshedData = refreshed?.data;
        if (refreshedData?.name) {
          setActiveCard({
            ...refreshedData,
            type: activeCard.type,
            isNew: false,
            originalName: refreshedData.name,
          });
          setCardForm({
            name: refreshedData.name || '',
            description: refreshedData.description || '',
            aliases: formatListInput(refreshedData.aliases),
            stars: normalizeStars(refreshedData.stars),
            category: refreshedData.category || '',
          });
        }
      } catch (error) {
        logger.error('Failed to refresh card data', error);
      }
      addMessage('system', t('writingSession.cardUpdated'));
      dispatch({ type: 'SET_SAVED' });
    } catch (e) {
      addMessage('error', t('writingSession.cardSaveFailed') + extractErrorDetail(e));
    } finally {
      setIsSaving(false);
    }
  };

  const handleCardFormChange = useCallback((patch) => {
    setCardForm((prev) => ({ ...prev, ...patch }));
  }, []);

  const handleCloseCardEditor = useCallback(() => {
    setStatus('idle');
    setActiveCard(null);
  }, []);

  const handleManualSelectionChange = useCallback(
    (value, selectionStart, selectionEnd) => {
      const stats = getSelectionStats(value, selectionStart, selectionEnd, writingLanguage);
      dispatch({ type: 'SET_SELECTION_COUNT', payload: stats.selectionCount });
      setSelectionInfo({
        start: stats.selectionStart,
        end: stats.selectionEnd,
        text: stats.selectionText || '',
      });
      const lines = stats.cursorText.split('\n');
      dispatch({
        type: 'SET_CURSOR_POSITION',
        payload: {
          line: lines.length,
          column: lines[lines.length - 1].length + 1,
        },
      });
    },
    [dispatch, writingLanguage],
  );

  const handleManualContentChange = useCallback(
    (nextValue, selectionStart, selectionEnd) => {
      setManualContent(nextValue);
      if (chapterInfo.chapter) {
        const key = String(chapterInfo.chapter);
        setManualContentByChapter((prev) => ({ ...(prev || {}), [key]: nextValue }));
      }
      dispatch({ type: 'SET_WORD_COUNT', payload: countWords(nextValue, writingLanguage) });
      handleManualSelectionChange(nextValue, selectionStart, selectionEnd);
      dispatch({ type: 'SET_UNSAVED' });
    },
    [chapterInfo.chapter, dispatch, handleManualSelectionChange, writingLanguage],
  );

  // Phase 16（vibe writing）：单输入框统一入口——后端自判意图（write/edit/continue/plan）后路由，
  // 替代前端 agentMode 手动开关。复用既有 handleStart / handleSubmitFeedback（已验证的写/改/反问/diff 流）。
  const handlePlanFromChat = async (goal) => {
    try {
      addMessage('system', t('writingSession.planGenerating') || '正在规划执行步骤…');
      const resp = await sessionAPI.plan(projectId, { goal, context_hint: chapterInfo.chapter || '' });
      const plan = resp?.data?.plan;
      const steps = Array.isArray(plan?.steps) ? plan.steps : [];
      if (resp?.data?.success && plan?.id && steps.length) {
        // 不直接执行：展示步骤 + 等用户在对话栏点「执行」批准（approval gate）。
        setPendingPlan({ id: plan.id, goal: plan.goal || goal, steps });
        return true;
      }
    } catch (err) {
      console.warn('plan generation failed', err);
    }
    return false;
  };

  const handleExecutePlan = async () => {
    if (!pendingPlan?.id || planExecuting) return;
    setPlanExecuting(true);
    addMessage('system', t('writingSession.planExecuting') || '开始执行计划…');
    try {
      const resp = await sessionAPI.executePlan(projectId, pendingPlan.id);
      const plan = resp?.data?.plan;
      const ok = resp?.data?.success;
      const stepsArr = Array.isArray(plan?.steps) ? plan.steps : [];
      const done = stepsArr.filter((s) => s.status === 'done').length;
      const total = stepsArr.length;
      const failed = stepsArr.find((s) => s.status === 'failed');
      if (ok) {
        addMessage('assistant', `${t('writingSession.planDone') || '计划已执行完成'}（${done}/${total}）`);
      } else {
        addMessage(
          'error',
          `${t('writingSession.planFailed') || '计划执行中断'}（${done}/${total}）${failed ? `：${failed.error || ''}` : ''}`,
        );
      }
    } catch (err) {
      addMessage('error', (t('writingSession.planFailed') || '计划执行中断') + extractErrorDetail(err));
    } finally {
      setPlanExecuting(false);
      setPendingPlan(null);
    }
  };

  const handleDismissPlan = () => {
    if (planExecuting) return;
    setPendingPlan(null);
  };

  const handleChatSubmit = async (text, approval = null) => {
    if (!approval) addMessage('user', text);
    const chapterKey = chapterInfo.chapter ? String(chapterInfo.chapter) : '';

    // 无章节：仅复杂规划可行（plan 不落具体章节）；写/改需章节作目标 → 友好引导。
    if (!chapterKey) {
      const planned = await handlePlanFromChat(text);
      if (!planned) {
        addMessage(
          'system',
          t('writingSession.needChapterForWrite') ||
            '撰写或编辑需先选择 / 新建一章作为目标；复杂规划可直接说，我来拆解步骤。',
        );
      }
      return;
    }

    // 有章节：统一交给后端 agent 主循环（write/edit/continue/plan 自判 + 自主用 write_content/edit_lines + 检索）。
    // 对齐 AI coding：不再前端预判分流。agent 写作内容经 WS 流式 diff（useWritingSessionRealtime → finalizeDraftAsDiff）；
    // 能力降级（provider 不支持工具）→ 后端返回 fallback 信号 → 前端用成熟的 write/edit 流程兜底（含反问 / 精确 diff）。
    setIsGenerating(true);
    setAiLockedChapter(chapterKey);
    try {
      const resp = await sessionAPI.chat(projectId, {
        chapter: chapterKey,
        message: text,
        has_selection: Boolean(attachedSelection?.text?.trim() || selectionInfo?.text?.trim()),
        has_draft: Boolean(chapterKey) && !canUseWriter,
        thinking: writerSupportsThinking && deepThinking,
        ...(approval
          ? {
              fallback_approval_action_id: approval.id,
              fallback_approval_token: approval.token,
            }
          : {}),
      });
      const data = resp?.data || {};
      const turnView = normalizeChatTurnResponse(data);
      setAgentTurnMeta(turnView.contextPlan || turnView.runtime ? turnView : null);

      // fallback 的执行权属于后端。前端只展示审批，并将单次 token 原样回传。
      if (turnView.terminalState === 'requires_approval' && turnView.pendingAction) {
        setPendingApproval({ ...turnView.pendingAction, message: text, chapter: chapterKey });
        setIsGenerating(false);
        return;
      }
      setPendingApproval(null);

      const terminalCopy = terminalStateMessage(turnView);
      if (terminalCopy) {
        addMessage(turnView.terminalState === 'failed' ? 'error' : 'system', terminalCopy);
        setIsGenerating(false);
        return;
      }

      if (data.action === 'plan' && data.plan) {
        const steps = Array.isArray(data.plan.steps) ? data.plan.steps : [];
        const lines = steps.map((s, i) => `${i + 1}. [${s.action}] ${s.description}`);
        addMessage('system', `${t('writingSession.planReady') || '已生成执行计划'}：\n${lines.join('\n')}`);
        setIsGenerating(false);
        return;
      }

      if (data.action === 'reply') {
        addMessage('assistant', data.message || '');
        setIsGenerating(false);
        return;
      }

      // agentic_write：内容已经/将经 WS 流式 diff 推送（前端被动 finalizeDraftAsDiff），
      // isGenerating 由 WS stream_end 收尾；未走流式的异常情形兜底关闭。
      if (!data.changed) {
        setIsGenerating(false);
      }
    } catch (e) {
      addMessage('error', (t('writingSession.editFailed') || '操作失败：') + extractErrorDetail(e));
      setIsGenerating(false);
      setStatus('waiting_feedback');
    }
  };

  const handleApproveFallback = () => {
    if (!pendingApproval || isGenerating) return;
    handleChatSubmit(pendingApproval.message, pendingApproval);
  };

  const handleDismissFallback = () => {
    if (isGenerating) return;
    setPendingApproval(null);
    addMessage('system', '已取消本次授权，未执行变更。');
  };

  const rightPanelContent = (
    <WritingSessionAgentPanel
      vm={{
        traceEvents,
        agentTraces,
        agentMode,
        setAgentMode,
        canUseWriter,
        agentBusy,
        aiLockedChapter,
        activeChapterKey,
        t,
        isCancelling,
        handleCancel,
        selectionInfo,
        attachedSelection,
        setAttachedSelection,
        setEditScope,
        editScope,
        contextDebug,
        progressEvents,
        messages,
        chapterInfo,
        diffReview,
        agentChapterKey,
        diffDecisions,
        handleAcceptAllDiff,
        handleRejectAllDiff,
        handleApplySelectedDiff,
        addMessage,
        handleStart,
        handleSubmitFeedback,
        handleChatSubmit,
        countWords,
        writingLanguage,
        dialogMaxChars,
        deepThinkingEnabled: writerSupportsThinking ? deepThinking : false,
        deepThinkingSupported: writerSupportsThinking,
        onToggleDeepThinking: toggleDeepThinking,
        pendingPlan,
        pendingApproval,
        agentTurnMeta,
        planExecuting,
        onExecutePlan: handleExecutePlan,
        onDismissPlan: handleDismissPlan,
        onApproveFallback: handleApproveFallback,
        onDismissFallback: handleDismissFallback,
      }}
    />
  );

  const saveBusy = isSaving || analysisLoading || analysisSaving;
  const showSaveAction = (chapterInfo.chapter || status === 'card_editing') && !lockedOnActiveChapter;
  const saveAction = showSaveAction ? (
    status === 'card_editing' ? (
      <Button onClick={handleCardSave} disabled={isSaving} className="shadow-sm" size="sm">
        {isSaving ? '\u4fdd\u5b58\u4e2d...' : '\u4fdd\u5b58'}
      </Button>
    ) : (
      <SaveMenu
        disabled={!chapterInfo.chapter || saveBusy}
        busy={saveBusy}
        onSaveOnly={handleManualSave}
        onAnalyzeSave={handleAnalyzeAndSave}
      />
    )
  ) : null;

  const titleBarProps = {
    projectName: project?.name,
    currentChapter: chapterInfo.chapter,
    rightActions: saveAction,
    // Show Card Name in Title if card editing
    chapterTitle:
      status === 'card_editing'
        ? cardForm.name
        : chapterInfo.chapter
          ? chapterInfo.chapter_title || t('writingSession.chapterFallback').replace('{n}', chapterInfo.chapter)
          : null,
    aiHint:
      agentBusy && aiLockedChapter
        ? t('writingSession.aiLockedStatusHint').replace('{n}', String(aiLockedChapter))
        : null,
  };

  return (
    <IDELayout rightPanelContent={rightPanelContent} titleBarProps={titleBarProps}>
      <div className="w-full h-full px-8 py-6">
        <WritingSessionMainContent
          vm={{
            activeActivity: state.activeActivity,
            dispatch,
            status,
            activeCard,
            cardForm,
            onCardFormChange: handleCardFormChange,
            onCloseCardEditor: handleCloseCardEditor,
            chapterInfo,
            setChapterInfo,
            manualContent,
            writingLanguage,
            t,
            state,
            diffReview,
            diffDecisions,
            onAcceptDiffHunk: handleAcceptDiffHunk,
            onRejectDiffHunk: handleRejectDiffHunk,
            isDiffReviewForActiveChapter,
            isStreamingForActiveChapter,
            streamOriginalContent: streamOriginalByChapterRef.current[activeChapterKey] || '',
            lockedOnActiveChapter,
            onManualContentChange: handleManualContentChange,
            onManualSelectionChange: handleManualSelectionChange,
          }}
        />
      </div>

      {notice ? (
        <div
          key={notice.id}
          className="fixed bottom-4 right-4 z-[60] max-w-[420px] rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] px-3 py-2 text-xs text-[var(--vscode-fg)] shadow-md"
        >
          {notice.text}
        </div>
      ) : null}

      <ChapterCreateDialog
        open={showChapterDialog}
        onClose={() => {
          setShowChapterDialog(false);
          dispatch({ type: 'CLOSE_CREATE_CHAPTER_DIALOG' });
        }}
        onConfirm={handleChapterCreate}
        existingChapters={chapters.map((c) => ({ id: c, title: '' }))}
        volumes={volumes}
        defaultVolumeId={state.selectedVolumeId || 'V1'}
      />

      <PreWritingQuestionsDialog
        open={showPreWriteDialog}
        questions={preWriteQuestions}
        onConfirm={handlePreWriteConfirm}
        onSkip={handlePreWriteSkip}
      />

      <AnalysisReviewDialog
        open={analysisDialogOpen}
        analyses={analysisItems}
        onCancel={() => {
          setAnalysisDialogOpen(false);
          setAnalysisItems([]);
        }}
        onSave={handleSaveAnalysis}
        saving={analysisSaving}
      />
    </IDELayout>
  );
}

/**
 * WritingSession - 写作会话入口
 * 提供 IDE 上下文并渲染主容器。
 */
export default function WritingSession(props) {
  const { projectId } = useParams();
  return (
    <IDEProvider projectId={projectId}>
      <WritingSessionContent {...props} />
    </IDEProvider>
  );
}
