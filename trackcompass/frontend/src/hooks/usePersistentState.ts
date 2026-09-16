/**
 * `useState` 的会话级持久化版本 —— 值同步写入 sessionStorage，跨路由保留。
 *
 * 存储细节与「为什么用 sessionStorage」见 `api/uiState.ts`。
 * 典型用法（需求探针 / 趋势扫描 / 七 Agent 分析的产物）：
 *
 * ```ts
 * const [result, setResult] = usePersistentState<ProbeResult | null>('demand:result', null)
 * ```
 *
 * 键名约定 `页面:语义`，避免两页共用同一个键互相覆盖。
 */
import {
  useCallback,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react'
import { readSessionState, writeSessionState } from '../api/uiState'

/**
 * @param key     会话态键名（不含前缀），如 `demand:result`
 * @param initial 初始值，或惰性求值函数（与 `useState` 一致）
 */
export function usePersistentState<T>(
  key: string,
  initial: T | (() => T),
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    const cached = readSessionState<T>(key)
    if (cached !== undefined) return cached
    return typeof initial === 'function' ? (initial as () => T)() : initial
  })

  /** 用 ref 同步跟住最新值，避免把副作用（写存储）塞进 setState 的 updater */
  const latest = useRef<T>(value)
  const keyRef = useRef(key)

  const set = useCallback<Dispatch<SetStateAction<T>>>((next) => {
    const resolved =
      typeof next === 'function' ? (next as (prev: T) => T)(latest.current) : next
    latest.current = resolved
    writeSessionState(keyRef.current, resolved)
    setValue(resolved)
  }, [])

  return [value, set]
}
