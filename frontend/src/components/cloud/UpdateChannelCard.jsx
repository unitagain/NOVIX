import React, { useEffect, useState } from 'react';

import { isCloudReady, cloudGetUpdateChannel, cloudSetUpdateChannel } from '../../utils/cloud';

const CHANNELS = [
  { value: 'stable', label: '稳定版', hint: '推荐，仅接收正式发布' },
  { value: 'beta', label: '公测版', hint: '提前体验，可能不稳定' },
  { value: 'dev', label: '开发版', hint: '仅供测试' },
];

/**
 * 更新通道设置卡片（阶段 9）。仅在桌面端显示；切换后立即触发一次版本检查。
 */
export default function UpdateChannelCard() {
  const [channel, setChannel] = useState('stable');
  const [saving, setSaving] = useState(false);
  const [hint, setHint] = useState('');

  useEffect(() => {
    if (!isCloudReady()) return;
    cloudGetUpdateChannel()
      .then((res) => {
        if (res?.ok && res.data?.channel) setChannel(res.data.channel);
      })
      .catch(() => {});
  }, []);

  if (!isCloudReady()) return null; // Web / 非桌面环境不展示

  async function onSelect(next) {
    if (next === channel || saving) return;
    const prev = channel;
    setChannel(next);
    setSaving(true);
    setHint('');
    try {
      const res = await cloudSetUpdateChannel(next);
      if (res?.ok === false) throw new Error(res.error?.message || '切换失败');
      setHint('已切换更新通道，并已检查更新');
    } catch (e) {
      setChannel(prev);
      setHint(e?.message || '切换失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-4 p-4 rounded-[6px] border border-[var(--vscode-sidebar-border)] bg-[var(--vscode-sidebar-bg)]">
      <div className="text-sm font-semibold text-[var(--vscode-fg)]">更新通道</div>
      <div className="mt-1 text-xs text-[var(--vscode-fg-subtle)]">
        选择接收哪个通道的版本更新；dev / beta 仅供测试，普通用户请使用稳定版。
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {CHANNELS.map((c) => (
          <button
            key={c.value}
            type="button"
            disabled={saving}
            onClick={() => onSelect(c.value)}
            title={c.hint}
            className={
              'rounded-[4px] border px-3 py-1.5 text-xs transition-colors disabled:opacity-60 ' +
              (channel === c.value
                ? 'border-transparent bg-[var(--vscode-list-active)] text-[var(--vscode-list-active-fg)]'
                : 'border-[var(--vscode-sidebar-border)] text-[var(--vscode-fg-subtle)] hover:bg-[var(--vscode-list-hover)] hover:text-[var(--vscode-fg)]')
            }
          >
            {c.label}
          </button>
        ))}
      </div>
      {hint && <div className="mt-2 text-[11px] text-[var(--vscode-fg-subtle)]">{hint}</div>}
    </div>
  );
}
