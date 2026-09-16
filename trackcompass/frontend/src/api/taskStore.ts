/**
 * 应用级「后台任务」注册表 —— 让长操作可以**离开页面继续跑，回来还能拿到结果**。
 *
 * 为什么需要它
 * ------------
 * 本项目的长操作全部是「手动点击 → 等服务端算」：趋势扫描、需求探针、七 Agent 分析
 * （接真实模型约 47s）、信号采集、报告生成……用户**不应该被钉在一个页面上等**。
 *
 * 现状的两个断点：
 *   ① HTTP 请求本身**不会**因为组件卸载而中断（这是事实，也没有 abort）——
 *      服务端会把活干完并落库；
 *   ② 但**接结果的处理函数挂在页面组件上**：组件一卸载，`setState` 被丢弃，
 *      结果就没人接。于是「活干完了，用户却看不到」。
 * 本模块把「接结果」这件事从页面组件提到应用级：任务注册在模块级 Map 里，
 * 完成/失败都落到这里 + 弹一条全局通知；页面回来时从注册表**认领**结果。
 *
 * 设计要点
 * --------
 * · **同一个 key 不并发**：重复点击直接忽略，避免产生两份昂贵的调用。
 * · **刷新页面会把在飞请求杀掉**：因此残留的 `running` 一律改判为「已中断」，
 *   否则顶部指示器会永远转圈骗人。
 * · 结果同时镜像到 sessionStorage（前缀 `tc:ui:task:`），与其它会话态同生命周期，
 *   登出/401 会被 `clearSessionState()` 一并清掉。
 * · 不引入轮询/WebSocket：服务端目前是同步接口，收到响应即完成；
 *   将来若改异步任务队列，只需把 `runTask` 内部换成「提交 + 轮询」。
 */
import { notification } from 'antd'
import { useSyncExternalStore } from 'react'
import { readSessionState, writeSessionState } from './uiState'

export type TaskStatus = 'running' | 'done' | 'error'

export interface TaskState<T = unknown> {
  /** 唯一键，约定 `页面:动作`，如 `demand:probe` */
  key: string
  /** 任务自述：归属页面 / 名称 / 本次参数（参数用于「结果按什么条件算出来的」留痕） */
  meta: TaskMeta
  status: TaskStatus
  startedAt: string
  finishedAt?: string
  result?: T
  error?: string
}

export interface TaskMeta {
  page: string
  title: string
  /** 本次执行参数，如 `{keywords, industry}` —— 随任务一起留存，页面回来时能复原「条件」 */
  params?: Record<string, unknown>
}

export interface TaskOptions<T> {
  /** 完成通知的文案；不传则用「已完成」 */
  summarize?: (result: T) => string
  /** 不弹通知（界面本身已有强反馈时用） */
  silent?: boolean
}

const STORE_PREFIX = 'task:'

/* ------------------------------------------------------------------ 内部状态 */

const tasks = new Map<string, TaskState>()
const listeners = new Set<() => void>()
/** 数据版本：任何任务状态变化 +1，列表页可把它放进 `useAsync` 依赖里自动刷新 */
let dataVersion = 0
/** 供 useSyncExternalStore 使用的稳定快照（只在变化时重建）
 *  ⚠️ 必须是**缓存引用**：`getSnapshot` 每次返回新数组会让 React 无限重渲染 */
let snapshot: TaskState[] = []
let runningSnapshot: TaskState[] = []

function rebuildSnapshot() {
  snapshot = [...tasks.values()]
  runningSnapshot = snapshot.filter((t) => t.status === 'running')
}

function emit() {
  dataVersion += 1
  rebuildSnapshot()
  listeners.forEach((fn) => fn())
}

function setTask<T>(state: TaskState<T>) {
  tasks.set(state.key, state as TaskState)
  writeSessionState(STORE_PREFIX + state.key, state)
  emit()
}

/* ------------------------------------------------------------------ 读取 */

/**
 * 取任务状态。
 *
 * ⚠️ 首次读到持久化状态时会把残留的 `running` 改判为**中断**：
 * 刷新页面会杀掉在飞请求，若还报「进行中」，顶部指示器会永远转圈骗人。
 */
export function getTask<T = unknown>(key: string): TaskState<T> | undefined {
  let state = tasks.get(key)
  if (!state) {
    const stored = readSessionState<TaskState<T>>(STORE_PREFIX + key)
    if (!stored) return undefined
    if (stored.status === 'running') {
      stored.status = 'error'
      stored.error = '页面刷新导致任务中断，请重新执行'
      stored.finishedAt = new Date().toISOString()
      writeSessionState(STORE_PREFIX + key, stored)
    }
    tasks.set(key, stored as TaskState)
    state = stored as TaskState
  }
  return state as TaskState<T>
}

export function listTasks(): TaskState[] {
  if (!snapshot.length && tasks.size) rebuildSnapshot()
  return snapshot
}

export function runningTasks(): TaskState[] {
  return runningSnapshot
}

/* ------------------------------------------------------------------ 订阅 */

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** 单个任务：状态变化即重渲染 */
export function useTask<T = unknown>(key: string): TaskState<T> | undefined {
  return useSyncExternalStore(subscribe, () => getTask<T>(key))
}

/** 正在跑的任务（顶部指示器用） */
export function useRunningTasks(): TaskState[] {
  return useSyncExternalStore(subscribe, () => runningTasks())
}

/** 数据版本 —— 放进列表页 `useAsync` 的依赖里，任务完成即自动刷新 */
export function useTaskVersion(): number {
  return useSyncExternalStore(subscribe, () => dataVersion)
}

/* ------------------------------------------------------------------ 执行 */

/**
 * 启动一个后台任务。返回的 Promise 用于「页面还在时」继续走自己的逻辑，
 * 页面已经走了也无所谓 —— 结果已落在注册表里。
 *
 * 同一个 key 正在跑时直接返回 `undefined`（**不**并发、**不**排队）。
 */
export async function runTask<T>(
  key: string,
  meta: TaskMeta,
  fn: () => Promise<T>,
  options: TaskOptions<T> = {},
): Promise<T | undefined> {
  const existing = getTask(key)
  if (existing?.status === 'running') return undefined

  const started: TaskState<T> = {
    key,
    meta,
    status: 'running',
    startedAt: new Date().toISOString(),
  }
  setTask(started)

  try {
    const result = await fn()
    setTask<T>({ ...started, status: 'done', result, finishedAt: new Date().toISOString() })
    if (!options.silent) {
      notification.success({
        message: `${meta.title}完成`,
        description: options.summarize?.(result) ?? `${meta.page} 的结果已就绪`,
        duration: 8,
        placement: 'topRight',
        // 点击通知不做跳转：任务可能属于任意页面，强行导航会打断用户当前操作
      })
    }
    return result
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    setTask<T>({ ...started, status: 'error', error: msg, finishedAt: new Date().toISOString() })
    if (!options.silent) {
      notification.error({
        message: `${meta.title}失败`,
        description: msg,
        duration: 8,
        placement: 'topRight',
      })
    }
    return undefined
  }
}

/** 清掉某个任务的记录（用户点「知道了」/ 页面认领后） */
export function clearTask(key: string) {
  if (!tasks.delete(key)) return
  writeSessionState(STORE_PREFIX + key, undefined)
  emit()
}

/** 登出 / 切换账号时清空内存态（持久化那部分由 `clearSessionState` 负责） */
export function resetTaskStore() {
  tasks.clear()
  emit()
}
