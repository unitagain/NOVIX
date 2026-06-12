/**
 * 桌面 cloud 桥接工具：仅在 desktop 运行时可用。
 * Web/dev 环境下所有方法返回空操作或安全默认值。
 */

import { getDesktopBridge, isDesktopRuntime } from './desktop';

function getCloudBridge() {
  return getDesktopBridge()?.cloud || null;
}

export function isCloudReady() {
  return Boolean(getCloudBridge());
}

export async function cloudGetStatus() {
  const cloud = getCloudBridge();
  if (!cloud) return null;
  try {
    return await cloud.getStatus();
  } catch {
    return null;
  }
}

export async function cloudGetAppInfo() {
  const cloud = getCloudBridge();
  if (!cloud) return null;
  try {
    const res = await cloud.getAppInfo();
    return res?.ok ? res.data : null;
  } catch {
    return null;
  }
}

export async function cloudLogin(email, password) {
  const cloud = getCloudBridge();
  if (!cloud) {
    return { ok: false, error: { message: '当前不是桌面环境，无法登录云端账号' } };
  }
  return cloud.login({ email, password });
}

export async function cloudRegister(email, password, nickname) {
  const cloud = getCloudBridge();
  if (!cloud) {
    return { ok: false, error: { message: '当前不是桌面环境，无法注册账号' } };
  }
  return cloud.register({ email, password, nickname: nickname || undefined });
}

export async function cloudLogout() {
  const cloud = getCloudBridge();
  if (!cloud) return { ok: true };
  return cloud.logout();
}

export async function cloudFetchMe() {
  const cloud = getCloudBridge();
  if (!cloud) return { ok: false };
  return cloud.me();
}

export async function cloudListDevices() {
  const cloud = getCloudBridge();
  if (!cloud) return { ok: false };
  return cloud.listDevices();
}

export async function cloudRemoveDevice(deviceId) {
  const cloud = getCloudBridge();
  if (!cloud) return { ok: false };
  return cloud.removeDevice(deviceId);
}

export async function cloudCheckVersion(options = {}) {
  const cloud = getCloudBridge();
  if (!cloud) return { ok: false };
  return cloud.checkVersion(options);
}

export async function cloudDownloadUpdate() {
  const cloud = getCloudBridge();
  if (!cloud?.downloadUpdate) return { ok: false, error: { message: '当前不是桌面环境，无法自动更新' } };
  return cloud.downloadUpdate();
}

export async function cloudInstallUpdate() {
  const cloud = getCloudBridge();
  if (!cloud?.installUpdate) return { ok: false, error: { message: '当前不是桌面环境，无法安装更新' } };
  return cloud.installUpdate();
}

export async function cloudGetUpdateChannel() {
  const cloud = getCloudBridge();
  if (!cloud?.getUpdateChannel) return { ok: false };
  return cloud.getUpdateChannel();
}

export async function cloudSetUpdateChannel(channel) {
  const cloud = getCloudBridge();
  if (!cloud?.setUpdateChannel) return { ok: false };
  return cloud.setUpdateChannel(channel);
}

export function subscribeCloudAuthState(listener) {
  const cloud = getCloudBridge();
  if (!cloud?.onAuthState || typeof listener !== 'function') return () => {};
  return cloud.onAuthState(listener);
}

export function subscribeCloudUpdateAvailable(listener) {
  const cloud = getCloudBridge();
  if (!cloud?.onUpdateAvailable || typeof listener !== 'function') return () => {};
  return cloud.onUpdateAvailable(listener);
}

export function subscribeCloudUpdateProgress(listener) {
  const cloud = getCloudBridge();
  if (!cloud?.onUpdateProgress || typeof listener !== 'function') return () => {};
  return cloud.onUpdateProgress(listener);
}

export { isDesktopRuntime };
