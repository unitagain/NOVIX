import { useEffect, useRef, useState } from 'react';
import { ChevronDown, DownloadCloud, LogIn, LogOut, RefreshCw, ShieldCheck, User, ExternalLink } from 'lucide-react';

import { useCloudAuth } from '../../context/CloudAuthContext';
import { cloudCheckVersion } from '../../utils/cloud';
import CloudLoginDialog from './CloudLoginDialog';

const WEB_URL = 'https://wenshape.cn';

export default function CloudAccountButton() {
  const { ready, authenticated, user, logout, refresh, isDesktopCloud } = useCloudAuth();
  const [open, setOpen] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginTab, setLoginTab] = useState('login');
  const [busy, setBusy] = useState(false);
  const [versionMsg, setVersionMsg] = useState(null);
  const ref = useRef(null);

  useEffect(() => {
    const handleClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  useEffect(() => {
    if (!open) setVersionMsg(null);
  }, [open]);

  const onCheckVersion = async () => {
    if (busy) return;
    setBusy(true);
    setVersionMsg(null);
    try {
      const res = await cloudCheckVersion({ silent: false });
      if (!res?.ok) {
        setVersionMsg({ tone: 'error', text: '检查更新失败，请稍后再试' });
        return;
      }
      const data = res.data;
      if (!data) {
        setVersionMsg({ tone: 'error', text: '无法连接到更新服务' });
      } else if (data.has_update && data.latest) {
        setVersionMsg({ tone: 'success', text: `发现新版本 v${data.latest.version}` });
      } else {
        setVersionMsg({ tone: 'info', text: '已是最新版本' });
      }
    } finally {
      setBusy(false);
    }
  };

  if (!isDesktopCloud) {
    // 非桌面环境（dev 浏览器）：保留入口但点击引导到官网
    return (
      <a
        href={`${WEB_URL}/login`}
        target="_blank"
        rel="noreferrer"
        className="flex items-center gap-1.5 rounded-[6px] px-3 py-1.5 text-sm text-[var(--vscode-fg-subtle)] transition-colors hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]"
        title="登录 wenshape.cn 账号"
      >
        <LogIn size={14} />
        <span>登录</span>
      </a>
    );
  }

  if (!ready) {
    return (
      <div className="h-7 w-16 animate-pulse rounded-[6px] bg-[var(--vscode-list-hover)]" />
    );
  }

  if (!authenticated) {
    return (
      <>
        <button
          onClick={() => {
            setLoginTab('login');
            setLoginOpen(true);
          }}
          className="flex items-center gap-1.5 rounded-[6px] px-3 py-1.5 text-sm text-[var(--vscode-fg-subtle)] transition-colors hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]"
          title="登录 WenShape 账号"
        >
          <LogIn size={14} />
          <span>登录</span>
        </button>
        <CloudLoginDialog
          open={loginOpen}
          onClose={() => setLoginOpen(false)}
          defaultTab={loginTab}
        />
      </>
    );
  }

  const displayName = user?.nickname || (user?.email ? user.email.split('@')[0] : '已登录');

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={
          'flex items-center gap-2 rounded-[6px] px-3 py-1.5 text-sm transition-colors ' +
          (open
            ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
            : 'text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]')
        }
        title={user?.email || displayName}
      >
        <span
          className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--vscode-list-active)] text-[10px] font-bold text-[var(--vscode-list-active-fg)]"
          aria-hidden
        >
          {displayName.slice(0, 1).toUpperCase()}
        </span>
        <span className="max-w-[120px] truncate">{displayName}</span>
        <ChevronDown size={12} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>

      {open && (
        <div className="glass-panel soft-dropdown absolute right-0 top-full z-50 mt-1 w-64 rounded-[6px] border border-[var(--vscode-sidebar-border)] py-1">
          <div className="border-b border-[var(--vscode-sidebar-border)] px-3 py-2">
            <div className="flex items-center gap-2 text-sm text-[var(--vscode-fg)]">
              <User size={14} className="text-[var(--vscode-fg-subtle)]" />
              <span className="truncate">{displayName}</span>
            </div>
            {user?.email && (
              <div className="mt-1 truncate text-[11px] text-[var(--vscode-fg-subtle)]">{user.email}</div>
            )}
            {user?.is_active && (
              <div className="mt-2 inline-flex items-center gap-1 rounded-[4px] bg-[var(--vscode-list-hover)] px-1.5 py-0.5 text-[10px] text-[var(--vscode-fg-subtle)]">
                <ShieldCheck size={10} />
                账户正常
              </div>
            )}
          </div>

          <button
            onClick={async () => {
              setBusy(true);
              try {
                await refresh();
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy}
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] transition-colors hover:bg-[var(--vscode-list-hover)] disabled:opacity-50"
          >
            <RefreshCw size={14} className={busy ? 'animate-spin text-[var(--vscode-fg-subtle)]' : 'text-[var(--vscode-fg-subtle)]'} />
            <span>同步账户信息</span>
          </button>

          <button
            onClick={onCheckVersion}
            disabled={busy}
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] transition-colors hover:bg-[var(--vscode-list-hover)] disabled:opacity-50"
          >
            <DownloadCloud size={14} className="text-[var(--vscode-fg-subtle)]" />
            <span>检查更新</span>
          </button>
          {versionMsg && (
            <div
              className={
                'px-3 pb-2 text-[11px] ' +
                (versionMsg.tone === 'success'
                  ? 'text-emerald-600'
                  : versionMsg.tone === 'error'
                    ? 'text-red-600'
                    : 'text-[var(--vscode-fg-subtle)]')
              }
            >
              {versionMsg.text}
            </div>
          )}

          <a
            href={`${WEB_URL}/account`}
            target="_blank"
            rel="noreferrer"
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] transition-colors hover:bg-[var(--vscode-list-hover)]"
            onClick={() => setOpen(false)}
          >
            <ExternalLink size={14} className="text-[var(--vscode-fg-subtle)]" />
            <span>在官网管理账户</span>
          </a>

          <a
            href={`${WEB_URL}/account/devices`}
            target="_blank"
            rel="noreferrer"
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] transition-colors hover:bg-[var(--vscode-list-hover)]"
            onClick={() => setOpen(false)}
          >
            <ExternalLink size={14} className="text-[var(--vscode-fg-subtle)]" />
            <span>设备管理</span>
          </a>

          <div className="my-1 border-t border-[var(--vscode-sidebar-border)]" />

          <button
            onClick={async () => {
              if (busy) return;
              if (!confirm('确认登出当前账户？\n\n本地创作不会受影响。')) return;
              setBusy(true);
              try {
                await logout();
                setOpen(false);
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy}
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-[var(--vscode-fg)] transition-colors hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
          >
            <LogOut size={14} />
            <span>登出</span>
          </button>
        </div>
      )}
    </div>
  );
}
