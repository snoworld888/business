import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { authApi, storage } from '../api/client'
import { getAuthSnapshot, setAuthSnapshot, subscribeAuth } from '../api/authStore'

/**
 * 认证状态。
 *
 * ⚠️ 状态存放在**模块级单例**（`api/authStore`）里，不再用组件内 `useState`：
 * `App`（路由守卫）与 `Login`（登录表单）都会调用本 hook，若各持一份 `useState`，
 * 登录后 `App` 那份不会更新，`navigate('/dashboard')` 会被守卫立刻弹回登录页
 * —— 现象就是「点登录不跳转」。
 */
export function useAuth() {
  const { token, user } = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getAuthSnapshot)

  const login = useCallback(async (email: string, password: string) => {
    const result = await authApi.login(email, password)
    storage.setToken(result.access_token)
    storage.setUser(result.user)
    // 一次性写入共享快照：App 立刻看到已登录并放行 /dashboard
    setAuthSnapshot(result.access_token, result.user)
    return result.user
  }, [])

  const logout = useCallback(() => {
    // storage.clear() 内部会 resetAuthSnapshot() / 清会话态 / 清后台任务
    storage.clear()
  }, [])

  const refresh = useCallback(async () => {
    const me = await authApi.me()
    storage.setUser(me)
    setAuthSnapshot(storage.getToken(), me)
    return me
  }, [])

  useEffect(() => {
    if (token && !user) {
      refresh().catch(() => logout())
    }
  }, [token, user, refresh, logout])

  const hasPermission = useCallback(
    (perm: string) => (user?.permissions ?? []).includes(perm),
    [user],
  )

  return { user, isAuthenticated: Boolean(token), login, logout, refresh, hasPermission }
}

/** 通用异步请求 Hook：统一 loading / error / 手动 refresh */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], immediate = true) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(immediate)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fn()
      setData(result)
      return result
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      return null
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    if (immediate) void run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])

  return { data, loading, error, refresh: run, setData }
}
