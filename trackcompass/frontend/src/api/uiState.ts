/**
 * 会话级 UI 状态存取（sessionStorage）—— 与 React 无关的纯存储层。
 *
 * 为什么需要它
 * ------------
 * 本应用用 React Router 切换页面，**路由一变，页面组件即被卸载**，
 * 组件内的 `useState` 全部归零。而需求探针 / 趋势扫描 / 七 Agent 分析
 * 都属于「用户主动点击、耗时数秒到数十秒」的动作：
 * 切到别的菜单看一眼再回来，结果就没了，只能重跑。
 * 这一层解决的就是这类**内存态丢失**，不改后端、不改路由。
 *
 * 为什么是 sessionStorage 而不是 localStorage
 * -------------------------------------------
 * 分析结果有**时效性**：今天跑的探针结论，明天打开应用还杵在页面上，
 * 会让人误以为是新结果。会话级（关闭标签页即失效）正好匹配这个语义。
 *
 * 与后端的关系
 * ------------
 * 这里**不是**缓存层，不替代任何后端查询：需求探针已有 `task_id`、
 * 七 Agent 分析已 `persist=true` 落库，后端数据才是真值。
 * 本层只保留「用户上一次手动触发的产物」用于展示，避免重复等待。
 *
 * React 侧入口见 `hooks/usePersistentState.ts`。
 */

/** 统一前缀：便于登出时一次性清空，也避免与本应用其它键冲突 */
const PREFIX = 'tc:ui:'

function safeSession(): Storage | null {
  try {
    // 隐私模式 / 禁用存储时访问会抛异常，此处静默降级为纯内存态（功能仍可用）
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

/** 读取会话态；读不到或解析失败一律返回 undefined（调用方回退到初始值） */
export function readSessionState<T>(key: string): T | undefined {
  const store = safeSession()
  if (!store) return undefined
  try {
    const raw = store.getItem(PREFIX + key)
    return raw === null ? undefined : (JSON.parse(raw) as T)
  } catch {
    return undefined
  }
}

/** 写入会话态；写失败（配额/序列化）不抛错 —— 持久化是增强，不是必需 */
export function writeSessionState(key: string, value: unknown): void {
  const store = safeSession()
  if (!store) return
  try {
    if (value === undefined) {
      store.removeItem(PREFIX + key)
      return
    }
    store.setItem(PREFIX + key, JSON.stringify(value))
  } catch {
    /* 超出配额或含不可序列化值时忽略：内存态仍已更新 */
  }
}

/**
 * 清空本应用写入的全部会话态。
 *
 * **登出 / 登录态失效时必须调用** —— 否则换账号登录后，
 * 页面上仍会显示上一个账号的分析结果。
 */
export function clearSessionState(): void {
  const store = safeSession()
  if (!store) return
  try {
    const keys: string[] = []
    for (let i = 0; i < store.length; i += 1) {
      const k = store.key(i)
      if (k && k.startsWith(PREFIX)) keys.push(k)
    }
    keys.forEach((k) => store.removeItem(k))
  } catch {
    /* 忽略 */
  }
}
