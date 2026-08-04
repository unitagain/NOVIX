import { useCallback, useEffect, useRef, useState } from 'react';
import { FileText, Loader2, Check, AlertCircle } from 'lucide-react';

import { outlineAPI } from '../../api';
import { useLocale } from '../../i18n';

/**
 * 大纲编辑视图 —— 全文规划资产（非章节，内容不参与事实提取）。
 * IDE 式健壮自动保存：防抖写入 + 末次写入生效（不传 expected_revision → 不会冲突/丢内容）+
 * 卸载时 flush + pagehide/beforeunload keepalive 落盘，防意外退出丢失。用户无需手动保存。
 */
export default function OutlineView({ projectId }) {
  const { t } = useLocale();
  const [content, setContent] = useState('');
  const [settings, setSettings] = useState({ enabled: true, require_consult: false });
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState('idle'); // idle | saving | saved | error
  const contentRef = useRef('');
  const dirtyRef = useRef(false);
  const savingRef = useRef(false);
  const saveTimerRef = useRef(null);
  const retryTimerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    outlineAPI
      .get(projectId)
      .then((resp) => {
        if (cancelled) return;
        const data = resp?.data || {};
        const text = String(data.outline?.content || '');
        setContent(text);
        contentRef.current = text;
        dirtyRef.current = false;
        setSettings({
          enabled: data.settings?.enabled !== false,
          require_consult: Boolean(data.settings?.require_consult),
        });
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // 末次写入生效：不传 expected_revision，单文档单用户永不冲突、永不丢内容。
  const flushSave = useCallback(async () => {
    if (!dirtyRef.current || savingRef.current) return;
    const snapshot = contentRef.current;
    savingRef.current = true;
    dirtyRef.current = false;
    setSaveState('saving');
    try {
      await outlineAPI.save(projectId, { content: snapshot });
      savingRef.current = false;
      // 保存期间又有新输入 → 立即再存一轮，保证最终一致。
      if (contentRef.current !== snapshot) {
        dirtyRef.current = true;
        void flushSave();
      } else {
        setSaveState('saved');
        setTimeout(() => setSaveState((s) => (s === 'saved' ? 'idle' : s)), 1500);
      }
    } catch (_e) {
      savingRef.current = false;
      dirtyRef.current = true; // 保留脏标记，稍后重试，不丢内容。
      setSaveState('error');
      if (retryTimerRef.current) window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = window.setTimeout(() => {
        retryTimerRef.current = null;
        void flushSave();
      }, 2500);
    }
  }, [projectId]);

  // Agent 通过 edit_outline 改写大纲后同步到编辑器；用户正在编辑（dirty）时不覆盖其输入。
  const refreshFromServer = useCallback(async () => {
    if (dirtyRef.current || savingRef.current) return;
    try {
      const resp = await outlineAPI.get(projectId);
      if (dirtyRef.current) return;
      const text = String(resp?.data?.outline?.content || '');
      setContent(text);
      contentRef.current = text;
    } catch (_e) {
      /* 刷新失败保持当前内容，不打断编辑 */
    }
  }, [projectId]);

  useEffect(() => {
    const onOutlineUpdated = () => {
      void refreshFromServer();
    };
    window.addEventListener('wenshape:outline-updated', onOutlineUpdated);
    return () => window.removeEventListener('wenshape:outline-updated', onOutlineUpdated);
  }, [refreshFromServer]);

  const onChange = (value) => {
    setContent(value);
    contentRef.current = value;
    dirtyRef.current = true;
    setSaveState('idle');
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(flushSave, 800); // 防抖自动保存
  };

  // 卸载（切走大纲/关项目）时 flush 未落盘内容。
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      if (retryTimerRef.current) window.clearTimeout(retryTimerRef.current);
      if (dirtyRef.current) void flushSave();
    };
  }, [flushSave]);

  // 意外退出（关窗/切后台）时用 keepalive 落盘，防丢失。
  useEffect(() => {
    const persistOnExit = () => {
      if (!dirtyRef.current || !projectId) return;
      try {
        void fetch(`/api/projects/${encodeURIComponent(projectId)}/outline`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: contentRef.current }),
          keepalive: true,
        }).catch(() => {});
      } catch (_e) {
        /* noop */
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') persistOnExit();
    };
    window.addEventListener('pagehide', persistOnExit);
    window.addEventListener('beforeunload', persistOnExit);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('pagehide', persistOnExit);
      window.removeEventListener('beforeunload', persistOnExit);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [projectId]);

  const toggleSetting = async (key) => {
    const next = { ...settings, [key]: !settings[key] };
    setSettings(next);
    try {
      await outlineAPI.updateSettings(projectId, { [key]: next[key] });
    } catch (_e) {
      setSettings(settings); // 回滚
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <div className="flex items-center gap-2">
          <FileText size={16} className="text-primary" />
          <span className="font-serif text-lg font-bold text-ink-900">{t('outline.title') || '大纲'}</span>
          <span className="text-xs text-ink-400">{t('outline.subtitle') || '全文规划 · 不参与事实提取'}</span>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <label className="flex cursor-pointer items-center gap-1.5 text-ink-500" title={t('outline.enabledHint') || '禁用后 AI 不再查阅大纲'}>
            <input type="checkbox" checked={settings.enabled} onChange={() => toggleSetting('enabled')} />
            {t('outline.enabled') || '启用大纲'}
          </label>
          <label className="flex cursor-pointer items-center gap-1.5 text-ink-500" title={t('outline.requireConsultHint') || '开启后每次撰写都会把大纲推给 AI'}>
            <input
              type="checkbox"
              checked={settings.require_consult}
              disabled={!settings.enabled}
              onChange={() => toggleSetting('require_consult')}
            />
            {t('outline.requireConsult') || '撰写前必查'}
          </label>
          <span className="flex w-14 items-center gap-1 text-ink-400">
            {saveState === 'saving' ? (
              <>
                <Loader2 size={12} className="animate-spin" /> {t('common.saving') || '保存中'}
              </>
            ) : saveState === 'saved' ? (
              <>
                <Check size={12} className="text-green-600" /> {t('common.saved') || '已保存'}
              </>
            ) : saveState === 'error' ? (
              <>
                <AlertCircle size={12} className="text-amber-500" /> {t('common.saveRetrying') || '重试中'}
              </>
            ) : null}
          </span>
        </div>
      </div>
      {loading ? (
        <div className="flex flex-1 items-center justify-center text-ink-400">
          <Loader2 size={18} className="animate-spin" />
        </div>
      ) : (
        <textarea
          value={content}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => dirtyRef.current && flushSave()}
          placeholder={
            t('outline.placeholder') ||
            '在这里规划全文：整体结构、每卷走向、关键伏笔与回收、人物弧光……\n大纲只作规划，不会被当成已发生的事实提取进 Canon。'
          }
          className="editor-canvas h-full w-full flex-1 resize-none overflow-y-auto border-none bg-transparent px-[clamp(16px,3.5vw,56px)] py-5 font-serif text-[length:var(--editor-font-size,16px)] leading-[1.9] text-ink-900 outline-none placeholder:text-ink-300 focus:ring-0"
        />
      )}
    </div>
  );
}
