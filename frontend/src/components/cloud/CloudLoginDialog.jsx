import { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';

import { useCloudAuth } from '../../context/CloudAuthContext';

const TABS = [
  { key: 'login', label: '登录' },
  { key: 'register', label: '注册' },
];

export default function CloudLoginDialog({ open, onClose, defaultTab = 'login' }) {
  const { login, register } = useCloudAuth();

  const [tab, setTab] = useState(defaultTab);
  const [email, setEmail] = useState('');
  const [nickname, setNickname] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);

  useEffect(() => {
    if (open) {
      setTab(defaultTab);
      setEmail('');
      setNickname('');
      setPassword('');
      setConfirm('');
      setError(null);
      setInfo(null);
    }
  }, [open, defaultTab]);

  if (!open) return null;

  const onSubmit = async (event) => {
    event.preventDefault();
    setError(null);
    setInfo(null);

    if (!email.trim() || !password) {
      setError('请输入邮箱与密码');
      return;
    }

    if (tab === 'register') {
      if (password.length < 8) {
        setError('密码至少 8 位');
        return;
      }
      if (password !== confirm) {
        setError('两次输入的密码不一致');
        return;
      }
    }

    setLoading(true);
    try {
      if (tab === 'register') {
        await register(email.trim(), password, nickname.trim() || undefined);
        // 注册成功后自动登录
        await login(email.trim(), password);
        onClose?.();
        return;
      }

      await login(email.trim(), password);
      onClose?.();
    } catch (e) {
      setError(e?.message || '操作失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/35"
      onClick={(e) => {
        if (e.target === e.currentTarget && !loading) onClose?.();
      }}
    >
      <div className="ws-paper relative w-[420px] max-w-[90vw] p-6 shadow-elevate animate-fade-in">
        <button
          type="button"
          onClick={() => !loading && onClose?.()}
          className="absolute right-3 top-3 rounded p-1 text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]"
          aria-label="关闭"
        >
          <X size={16} />
        </button>

        <div className="mb-1 text-center">
          <span className="brand-logo text-3xl text-[var(--vscode-fg)]">文枢</span>
        </div>
        <p className="mb-5 text-center text-xs text-[var(--vscode-fg-subtle)]">登录账号以同步设备与版本通知</p>

        <div className="mb-4 flex gap-1 rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] p-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={`flex-1 rounded-[4px] px-2 py-1 text-sm transition-colors ${
                tab === t.key
                  ? 'bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                  : 'text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <form onSubmit={onSubmit} className="space-y-3">
          <Field label="邮箱">
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputClass}
              disabled={loading}
            />
          </Field>

          {tab === 'register' && (
            <Field label="昵称（可选）">
              <input
                type="text"
                maxLength={64}
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
                placeholder="留空则使用邮箱前缀"
                className={inputClass}
                disabled={loading}
              />
            </Field>
          )}

          <Field label="密码">
            <input
              type="password"
              autoComplete={tab === 'register' ? 'new-password' : 'current-password'}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputClass}
              disabled={loading}
            />
          </Field>

          {tab === 'register' && (
            <Field label="确认密码">
              <input
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className={inputClass}
                disabled={loading}
              />
            </Field>
          )}

          {error && (
            <div className="rounded-[4px] border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
          )}
          {info && (
            <div className="rounded-[4px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-input-bg)] px-3 py-2 text-xs text-[var(--vscode-fg-subtle)]">
              {info}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="flex h-10 w-full items-center justify-center gap-2 rounded-[6px] bg-[var(--vscode-list-active)] text-sm text-[var(--vscode-list-active-fg)] transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {loading && <Loader2 size={14} className="animate-spin" />}
            {tab === 'register' ? '创建账号并登录' : '登录'}
          </button>

          <p className="text-center text-[11px] text-[var(--vscode-fg-subtle)]">
            登录即表示你同意以邮箱接收账户通知与版本提醒。
            <br />
            未登录也可继续以游客模式使用全部本地创作功能。
          </p>
        </form>
      </div>
    </div>
  );
}

const inputClass =
  'w-full h-9 rounded-[4px] border border-[var(--vscode-input-border)] bg-[var(--vscode-input-bg)] px-3 text-sm text-[var(--vscode-fg)] outline-none transition-colors focus:border-[var(--vscode-focus-border)] focus:ring-1 focus:ring-[var(--vscode-focus-border)] disabled:opacity-60';

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-[var(--vscode-fg-subtle)]">
        {label}
      </span>
      {children}
    </label>
  );
}
