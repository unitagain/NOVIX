import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import {
  cloudFetchMe,
  cloudGetStatus,
  cloudLogin,
  cloudLogout,
  cloudRegister,
  isCloudReady,
  subscribeCloudAuthState,
} from '../utils/cloud';
import logger from '../utils/logger';

const CloudAuthContext = createContext(null);

const initialState = {
  ready: false,
  authenticated: false,
  user: null,
  baseUrl: null,
  lastError: null,
};

export function CloudAuthProvider({ children }) {
  const [state, setState] = useState(initialState);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const applyStatus = useCallback((status) => {
    if (!isMountedRef.current) return;
    if (!status) {
      setState((prev) => ({ ...prev, ready: true }));
      return;
    }
    setState((prev) => ({
      ...prev,
      ready: true,
      authenticated: Boolean(status.authenticated),
      user: status.user || null,
      baseUrl: status.baseUrl || prev.baseUrl,
    }));
  }, []);

  // Initial load + subscribe to push events
  useEffect(() => {
    if (!isCloudReady()) {
      // Web/dev environment — never ready as a desktop session.
      setState((prev) => ({ ...prev, ready: true }));
      return undefined;
    }

    let cancelled = false;
    cloudGetStatus()
      .then((status) => {
        if (cancelled) return;
        applyStatus(status);
      })
      .catch(() => {
        if (cancelled) return;
        setState((prev) => ({ ...prev, ready: true }));
      });

    const unsubscribe = subscribeCloudAuthState((payload) => {
      applyStatus(payload);
    });

    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [applyStatus]);

  const login = useCallback(async (email, password) => {
    const result = await cloudLogin(email, password);
    if (!result?.ok) {
      const message = result?.error?.message || '登录失败';
      setState((prev) => ({ ...prev, lastError: message }));
      throw new Error(message);
    }
    if (result.data?.user) {
      setState((prev) => ({
        ...prev,
        authenticated: true,
        user: result.data.user,
        lastError: null,
      }));
    }
    return result.data?.user || null;
  }, []);

  const register = useCallback(async (email, password, nickname) => {
    const result = await cloudRegister(email, password, nickname);
    if (!result?.ok) {
      const message = result?.error?.message || '注册失败';
      throw new Error(message);
    }
    return result.data || null;
  }, []);

  const logout = useCallback(async () => {
    await cloudLogout();
    setState((prev) => ({
      ...prev,
      authenticated: false,
      user: null,
      lastError: null,
    }));
  }, []);

  const refresh = useCallback(async () => {
    const result = await cloudFetchMe();
    if (result?.ok && result.data) {
      setState((prev) => ({
        ...prev,
        authenticated: true,
        user: result.data,
      }));
      return result.data;
    }
    if (result && result.ok === false && result.error?.status === 401) {
      setState((prev) => ({ ...prev, authenticated: false, user: null }));
    } else if (result && result.ok === false) {
      logger.warn('cloud refresh failed:', result.error);
    }
    return null;
  }, []);

  const value = useMemo(
    () => ({
      ...state,
      isDesktopCloud: isCloudReady(),
      login,
      logout,
      register,
      refresh,
    }),
    [state, login, logout, register, refresh],
  );

  return <CloudAuthContext.Provider value={value}>{children}</CloudAuthContext.Provider>;
}

export function useCloudAuth() {
  const ctx = useContext(CloudAuthContext);
  if (!ctx) {
    return {
      ready: true,
      authenticated: false,
      user: null,
      baseUrl: null,
      lastError: null,
      isDesktopCloud: false,
      login: async () => {
        throw new Error('CloudAuthProvider not mounted');
      },
      logout: async () => {},
      register: async () => null,
      refresh: async () => null,
    };
  }
  return ctx;
}
