import { useCallback, useEffect, useState } from 'react';
import { Download, RefreshCw, X, CheckCircle2, Loader2, AlertTriangle } from 'lucide-react';

import {
  subscribeCloudUpdateAvailable,
  subscribeCloudUpdateProgress,
  cloudDownloadUpdate,
  cloudInstallUpdate,
  isCloudReady,
} from '../../utils/cloud';

const DISMISS_KEY = 'wenshape:update-dismissed';
const WEB_URL = 'https://wenshape.cn';

function readDismissedVersion() {
  try {
    if (typeof window === 'undefined') return null;
    return window.localStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
}

function writeDismissedVersion(version) {
  try {
    if (typeof window === 'undefined') return;
    if (version) {
      window.localStorage.setItem(DISMISS_KEY, version);
    } else {
      window.localStorage.removeItem(DISMISS_KEY);
    }
  } catch {
    /* ignore */
  }
}

const BTN_PRIMARY =
  'inline-flex items-center gap-1 rounded-[4px] bg-[var(--vscode-list-active)] px-3 py-1.5 text-xs text-[var(--vscode-list-active-fg)] hover:opacity-90 disabled:opacity-60';
const BTN_GHOST =
  'rounded-[4px] border border-[var(--vscode-sidebar-border)] px-3 py-1.5 text-xs text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]';

/**
 * 新版本横幅：阶段 9 升级为"下载 → 进度 → 安装"状态机。
 * - 有 sha256（可校验）→ 走桌面端辅助更新（下载并更新 → 立即安装）
 * - 无 sha256 / 非桌面环境 → 退化为"前往下载"（跳官网）
 * - 强制更新（is_mandatory）→ 不提供关闭 / 不再提醒
 */
export default function UpdateAvailableBanner() {
  const [info, setInfo] = useState(null);
  const [phase, setPhase] = useState('idle'); // idle | downloading | verified | installing | error
  const [percent, setPercent] = useState(0);
  const [errorMsg, setErrorMsg] = useState('');

  const onDismiss = useCallback(
    (permanent) => {
      if (permanent && info?.latest?.version) {
        writeDismissedVersion(info.latest.version);
      }
      setInfo(null);
    },
    [info]
  );

  useEffect(() => {
    if (!isCloudReady()) return undefined;
    const unsubscribe = subscribeCloudUpdateAvailable((payload) => {
      if (!payload?.latest) return;
      const dismissed = readDismissedVersion();
      if (dismissed && dismissed === payload.latest.version) return;
      setInfo(payload);
      setPhase('idle');
      setPercent(0);
      setErrorMsg('');
    });
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!isCloudReady()) return undefined;
    const unsubscribe = subscribeCloudUpdateProgress((p) => {
      if (!p) return;
      if (p.phase === 'downloading') {
        setPhase((cur) => (cur === 'installing' ? cur : 'downloading'));
        setPercent(p.percent || 0);
      } else if (p.phase === 'verified') {
        setPhase('verified');
        setPercent(100);
      }
    });
    return unsubscribe;
  }, []);

  const onDownload = useCallback(async () => {
    setPhase('downloading');
    setPercent(0);
    setErrorMsg('');
    try {
      const res = await cloudDownloadUpdate();
      if (res?.ok === false) throw new Error(res.error?.message || '下载失败');
      setPhase('verified');
      setPercent(100);
    } catch (e) {
      setPhase('error');
      setErrorMsg(e?.message || '下载失败');
    }
  }, []);

  const onInstall = useCallback(async () => {
    setPhase('installing');
    setErrorMsg('');
    try {
      const res = await cloudInstallUpdate();
      if (res?.ok === false) throw new Error(res.error?.message || '安装失败');
      // 成功后应用会退出并拉起安装程序
    } catch (e) {
      setPhase('error');
      setErrorMsg(e?.message || '安装失败');
    }
  }, []);

  if (!info?.latest) return null;

  const { latest, current } = info;
  const isMandatory = !!latest.is_mandatory;
  const canAutoUpdate = !!latest.sha256 && isCloudReady();
  const downloadUrl = latest.download_url || `${WEB_URL}/download`;
  const busy = phase === 'downloading' || phase === 'installing';

  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[900] w-[360px] max-w-[92vw]">
      <div
        className={
          'pointer-events-auto rounded-[8px] border bg-[var(--vscode-input-bg)] p-4 shadow-float ' +
          (isMandatory ? 'border-red-300' : 'border-[var(--vscode-sidebar-border)]')
        }
      >
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[6px] bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]">
            <RefreshCw size={16} />
          </div>
          <div className="flex-1">
            <div className="flex items-center justify-between gap-2">
              <div className="font-serif text-sm font-bold text-[var(--vscode-fg)]">
                {isMandatory ? '需要更新到新版本' : '发现新版本'}
              </div>
              {!isMandatory && !busy && (
                <button
                  onClick={() => onDismiss(false)}
                  className="rounded p-1 text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]"
                  aria-label="稍后再说"
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <div className="mt-1 text-xs text-[var(--vscode-fg-subtle)]">
              当前 v{current || '?'} → 最新 v{latest.version}
              {latest.channel && ` · ${latest.channel}`}
            </div>
            {latest.release_notes && phase === 'idle' && (
              <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap rounded-[4px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-bg)] p-2 font-sans text-[11px] leading-relaxed text-[var(--vscode-fg)]">
                {latest.release_notes}
              </pre>
            )}

            {(phase === 'downloading' || phase === 'verified') && (
              <div className="mt-3">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--vscode-sidebar-border)]">
                  <div
                    className="h-full rounded-full bg-[var(--vscode-list-active)] transition-all"
                    style={{ width: `${percent}%` }}
                  />
                </div>
                <div className="mt-1 text-[11px] text-[var(--vscode-fg-subtle)]">
                  {phase === 'verified' ? '校验通过，可安装' : `下载中 ${percent}%`}
                </div>
              </div>
            )}

            {phase === 'error' && (
              <div className="mt-2 flex items-start gap-1 text-[11px] text-red-500">
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                <span>{errorMsg || '更新失败'}</span>
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              {canAutoUpdate ? (
                <>
                  {(phase === 'idle' || phase === 'error') && (
                    <button type="button" onClick={onDownload} className={BTN_PRIMARY}>
                      <Download size={12} />
                      下载并更新
                    </button>
                  )}
                  {phase === 'downloading' && (
                    <button type="button" disabled className={BTN_PRIMARY}>
                      <Loader2 size={12} className="animate-spin" />
                      下载中…
                    </button>
                  )}
                  {phase === 'verified' && (
                    <button type="button" onClick={onInstall} className={BTN_PRIMARY}>
                      <CheckCircle2 size={12} />
                      立即安装
                    </button>
                  )}
                  {phase === 'installing' && (
                    <button type="button" disabled className={BTN_PRIMARY}>
                      <Loader2 size={12} className="animate-spin" />
                      正在启动安装…
                    </button>
                  )}
                  {phase === 'error' && (
                    <a href={downloadUrl} target="_blank" rel="noreferrer" className={BTN_GHOST}>
                      前往官网下载
                    </a>
                  )}
                </>
              ) : (
                <a href={downloadUrl} target="_blank" rel="noreferrer" className={BTN_PRIMARY}>
                  <Download size={12} />
                  前往下载
                </a>
              )}

              {!isMandatory && phase === 'idle' && (
                <button type="button" onClick={() => onDismiss(true)} className={BTN_GHOST}>
                  本版本不再提醒
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
