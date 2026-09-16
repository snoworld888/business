/**
 * 认证状态的**模块级单例**。
 *
 * 为什么必须共享，而不是每个组件各自的 `useState`
 * -------------------------------------------------
 * `App`（路由守卫）与 `Login`（登录表单）都会调用 `useAuth()`。
 * 若各自持有 `useState`，登录时只有 `Login` 那一份被更新，`App` 的
 * `isAuthenticated` 仍为 `false` ⇒ `navigate('/dashboard')` 之后立刻被守卫的
 * `<Navigate to="/login" replace />` 弹回登录页 —— 现象是
 * **「点了登录不跳转，手动刷新一下才进去」**（刷新时模块重新加载，
 * 才会从 localStorage 读到 token）。
 *
 * 实现与 `taskStore.ts` 同构：模块级快照 + `useSyncExternalStore` 订阅。
 * 好处是 `useAuth()` 的**调用签名完全不变**，所有页面零改动。
 *
 * 存储键定义在这里而非 `client.ts`，是为了让 `client.ts` 能反向调用
 * `resetAuthSnapshot()` 而又不产生循环依赖。
 */

import type { UserInfo } from './types'

export const TOKEN_KEY = 'trackcompass_token'
export const USER_KEY = 'trackcompass_user'

export interface AuthSnapshot {
  token: string | null
  user: UserInfo | null
}

const listeners = new Set<() => void>()

function readStorage(): AuthSnapshot {
  try {
    const token = localStorage.getItem(TOKEN_KEY)
    const raw = localStorage.getItem(USER_KEY)
    return { token: token || null, user: raw ? (JSON.parse(raw) as UserInfo) : null }
  } catch {
    // 隐私模式 / 存储被禁用：退化为未登录，不影响应用启动
    return { token: null, user: null }
  }
}

/**
 * 快照必须**引用稳定**（只在真正变化时重建），
 * 否则 `useSyncExternalStore` 会判定为「每次都变了」而无限重渲染。
 */
let snapshot: AuthSnapshot = readStorage()

function emit(): void {
  for (const listener of listeners) listener()
}

export function getAuthSnapshot(): AuthSnapshot {
  return snapshot
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** 登录成功 / 刷新用户信息后调用 */
export function setAuthSnapshot(token: string | null, user: UserInfo | null): void {
  if (snapshot.token === token && snapshot.user === user) return
  snapshot = { token, user }
  emit()
}

/** 登出 / 401 清空存储后调用，保证内存快照与 localStorage 一致 */
export function resetAuthSnapshot(): void {
  setAuthSnapshot(null, null)
}
