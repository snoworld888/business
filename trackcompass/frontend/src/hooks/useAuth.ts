import { useCallback, useEffect, useState } from 'react'
import { authApi, storage } from '../api/client'
import type { UserInfo } from '../api/types'

export function useAuth() {
  const [user, setUser] = useState<UserInfo | null>(() => storage.getUser())
  const token = storage.getToken()

  const login = useCallback(async (email: string, password: string) => {
    const result = await authApi.login(email, password)
    storage.setToken(result.access_token)
    storage.setUser(result.user)
    setUser(result.user)
    return result.user
  }, [])

  const logout = useCallback(() => {
    storage.clear()
    setUser(null)
  }, [])

  const refresh = useCallback(async () => {
    const me = await authApi.me()
    storage.setUser(me)
    setUser(me)
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
