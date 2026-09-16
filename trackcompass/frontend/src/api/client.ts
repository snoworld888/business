/**
 * API 客户端：统一信封解包 + JWT 注入 + 错误提示。
 * 技术文档 §6.1：JSON 格式、JWT Bearer、统一错误码。
 */
import axios, { type AxiosRequestConfig } from 'axios'
import { message } from 'antd'
import { clearSessionState } from './uiState'
import { resetTaskStore } from './taskStore'
import type {
  AgentCatalogItem,
  Alert,
  CompanyProfile,
  CompanyProfileSummary,
  DataSource,
  DemandProbe,
  Envelope,
  Evidence,
  Experiment,
  GraphData,
  GraphInsights,
  HealthInfo,
  LoginResult,
  Opportunity,
  OpportunityScore,
  Overview,
  PainPoint,
  ProfileUsed,
  Replay,
  Report,
  Scorecard,
  ScorecardVersion,
  Signal,
  SourceAdapter,
  CollectReport,
  Trend,
  TrendScanResult,
  UserInfo,
  WorkflowLayer,
} from './types'

const TOKEN_KEY = 'trackcompass_token'
const USER_KEY = 'trackcompass_user'

export const storage = {
  getToken: () => localStorage.getItem(TOKEN_KEY),
  setToken: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  getUser: (): UserInfo | null => {
    const raw = localStorage.getItem(USER_KEY)
    try {
      return raw ? (JSON.parse(raw) as UserInfo) : null
    } catch {
      return null
    }
  },
  setUser: (u: UserInfo) => localStorage.setItem(USER_KEY, JSON.stringify(u)),
  clear: () => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    // 会话级 UI 状态（需求探针结果 / 趋势扫描结果 / 七 Agent 分析明细 / 后台任务）必须一起清：
    // 否则换个账号登录后，上一个账号的分析结果仍留在页面上
    clearSessionState()
    resetTaskStore()
  },
}

const http = axios.create({ baseURL: '/api/v1', timeout: 120_000 })

http.interceptors.request.use((config) => {
  const token = storage.getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const status = error?.response?.status
    const detail = error?.response?.data?.message
    if (status === 401) {
      storage.clear()
      if (!window.location.hash.includes('/login')) {
        message.error(detail || '登录已失效，请重新登录')
        window.location.hash = '#/login'
      }
    } else if (status === 403) {
      message.warning(detail || '当前角色无此操作权限')
    } else if (status && status >= 500) {
      message.error(detail || '服务器错误')
    } else if (!status) {
      message.error('无法连接后端服务，请确认后端已启动（默认 8020 端口）')
    }
    return Promise.reject(error)
  },
)

async function unwrap<T>(cfg: AxiosRequestConfig): Promise<T> {
  const resp = await http.request<Envelope<T>>(cfg)
  return resp.data.data
}

/* ------------------------------------------------------------------ 认证 */
export const authApi = {
  login: (email: string, password: string) =>
    unwrap<LoginResult>({ url: '/auth/login', method: 'POST', data: { email, password } }),
  me: () => unwrap<UserInfo>({ url: '/auth/me' }),
  permissions: () =>
    unwrap<{ roles: Array<{ role: string; permissions: string[] }>; all_permissions: string[] }>({
      url: '/auth/permissions',
    }),
}

/* ------------------------------------------------------------------ 元信息 */
export const metaApi = {
  health: () => unwrap<HealthInfo>({ url: '/health' }),
  agents: () => unwrap<{ agents: AgentCatalogItem[]; count: number }>({ url: '/agents' }),
  workflow: () =>
    unwrap<{ name: string; layers: WorkflowLayer[]; edges: Array<{ from: string; to: string }>; feedback_loop: string }>(
      { url: '/workflow' },
    ),
  modules: () => unwrap<Record<string, any>>({ url: '/modules' }),
  overview: () => unwrap<Overview>({ url: '/admin/overview' }),
  modelConfig: () => unwrap<Record<string, any>>({ url: '/admin/model-config' }),
}

/* ------------------------------------------------------------------ 能力画像 */
export const profileApi = {
  /** 不传 profileId 时取「当前公司」（后端按最近保存判定） */
  get: (profileId?: string) =>
    unwrap<CompanyProfile | null>({
      url: '/company/profile',
      params: profileId ? { profile_id: profileId } : undefined,
    }),
  /** 公司画像列表（多公司切换） */
  list: () =>
    unwrap<{
      profiles: CompanyProfileSummary[]
      current_id: string | null
      count: number
    }>({ url: '/company/profiles' }),
  save: (payload: Partial<CompanyProfile>) =>
    unwrap<{
      id: string
      name: string
      /** true = 新建了一家公司；false = 更新了已有公司 */
      created: boolean
      updated_at?: string | null
      capability_vector: number[]
      vector_dim: number
      radar: Record<string, number>
      recommended_tracks: string[]
      boundary: Record<string, unknown>
    }>({ url: '/company/profile', method: 'POST', data: payload }),
  parse: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return unwrap<{ parsed: boolean; reason?: string; suggestion?: Record<string, any> }>({
      url: '/company/profile/parse',
      method: 'POST',
      data: form,
    })
  },
  tracks: () =>
    unwrap<{
      tracks: Array<{
        id: string
        name: string
        description: string
        industry?: string
        signal_count: number
        trends: Array<{ name: string; score: number; confidence: number }>
      }>
      blank_spots: Array<{ name: string; degree: number }>
    }>({ url: '/company/tracks' }),
}

/* ------------------------------------------------------------------ 信号 */
export const signalApi = {
  list: (params?: { signal_type?: string; industry?: string; limit?: number }) =>
    unwrap<{ total: number; stats: Record<string, number>; signals: Signal[] }>({
      url: '/signals',
      params,
    }),
  sources: () =>
    unwrap<{ sources: DataSource[]; connected: number; mapped_sources: number; coverage_ok: boolean }>({
      url: '/signals/sources',
    }),
  create: (payload: Record<string, unknown>) =>
    unwrap<{ id: string; indexed: boolean; duplicated: boolean }>({
      url: '/signals', method: 'POST', data: payload,
    }),
  reindex: () =>
    unwrap<{ indexed_documents: number; graph: Record<string, number> }>({
      url: '/signals/reindex',
      method: 'POST',
    }),
  /* ---- 采集器（《技术文档》§3.1 数据源接入） ---- */
  adapters: () =>
    unwrap<{
      adapters: SourceAdapter[]
      total: number
      available: number
      default_enabled: number
      coverage_ok: boolean
    }>({ url: '/signals/adapters' }),
  collect: (payload?: {
    sources?: string[]
    keywords?: string[]
    limit_per_source?: number
    industry?: string
    dry_run?: boolean
    reindex?: boolean
  }) =>
    unwrap<CollectReport>({ url: '/signals/collect', method: 'POST', data: payload ?? {} }),
}

/* ------------------------------------------------------------------ 趋势雷达 */
export const trendApi = {
  list: (params?: { industry?: string; direction?: string; limit?: number; refresh?: boolean }) =>
    unwrap<{ total: number; algorithm: string; trends: Trend[] }>({ url: '/trends', params }),
  scan: (params?: { industry?: string }) =>
    unwrap<TrendScanResult>({ url: '/trends/scan', method: 'POST', params }),
  detail: (id: string) => unwrap<Trend>({ url: `/trends/${id}` }),
  subscribe: (id: string, subscribed: boolean) =>
    unwrap<{ id: string; subscribed: boolean }>({
      url: `/trends/${id}/subscribe`,
      method: 'POST',
      data: { subscribed },
    }),
  toPool: (id: string, payload: { title?: string; summary?: string }) =>
    unwrap<{ opportunity_id: string; title: string; status: string; track?: string }>({
      url: `/trends/${id}/to-pool`,
      method: 'POST',
      data: payload,
    }),
}

/* ------------------------------------------------------------------ 机会 */
export const opportunityApi = {
  list: (params?: { track_id?: string; status?: string; sort?: string; limit?: number }) =>
    unwrap<{ total: number; opportunities: Opportunity[] }>({ url: '/opportunities', params }),
  detail: (id: string) => unwrap<Opportunity>({ url: `/opportunities/${id}` }),
  create: (payload: { title: string; track_id?: string; summary?: string }) =>
    unwrap<{ id: string; title: string; status: string }>({
      url: '/opportunities',
      method: 'POST',
      data: payload,
    }),
  analyze: (id: string, payload: Record<string, unknown>) =>
    unwrap<{
      opportunity_id: string
      status: string
      total_score: number
      score: Record<string, any>
      evidence: Evidence[]
      trace: Array<{ agent: string; agent_name: string; latency_ms: number }>
      loops: number
      errors: Array<{ agent: string; error: string }>
      signal_count: number
      /** 本次分析依据的公司能力（技术栈 + 专利抽词 → 命中的趋势） */
      profile_used: ProfileUsed
    }>({ url: `/opportunities/${id}/analyze`, method: 'POST', data: payload }),
  score: (id: string) => unwrap<OpportunityScore & { title: string }>({ url: `/opportunities/${id}/score` }),
  preview: (id: string, weights: Record<string, number>) =>
    unwrap<{ total_score: number; explained: OpportunityScore['explained']; risk_alerts: any[] }>({
      url: `/opportunities/${id}/score/preview`,
      method: 'POST',
      data: { opportunity_id: id, weights },
    }),
  graph: (rebuild = false) => unwrap<GraphData>({ url: '/graph', params: { rebuild } }),
  graphInsights: () => unwrap<GraphInsights>({ url: '/graph/insights' }),
  ragSearch: (q: string, top_k = 8) =>
    unwrap<{ query: string; hits: Array<Record<string, any>>; citation_ratio: number }>({
      url: '/rag/search',
      params: { q, top_k },
    }),
}

/* ------------------------------------------------------------------ 评分卡 */
export const scorecardApi = {
  default: (withRanking = true) =>
    unwrap<Scorecard & { ranking?: Array<{ id: string; title: string; total_score: number; rank: number; status: string }> }>(
      { url: '/scorecards/default', params: { with_ranking: withRanking } },
    ),
  updateWeights: (id: string, weights: Record<string, number>, change_reason: string) =>
    unwrap<{
      id: string
      version: string
      diff: Record<string, { from?: number; to?: number; delta?: number }>
      validation: { ok: boolean; total: number; message: string }
      recalculated_opportunities: number
    }>({ url: `/scorecards/${id}/weights`, method: 'PUT', data: { weights, change_reason } }),
  versions: (id: string) =>
    unwrap<{
      current: string
      count: number
      versions: ScorecardVersion[]
      comparison: Array<{ from_version: string; to_version: string; changes: Array<Record<string, any>>; reason?: string }>
    }>({ url: `/scorecards/${id}/versions` }),
  calibrationPreview: () =>
    unwrap<{
      suggestions: Array<{ dimension: string; from: number; to: number; delta: number; reason: string }>
      current_weights: Record<string, number>
      weights: Record<string, number>
      diff: Record<string, { from: number; to: number; delta: number; reason?: string }>
      validation: { ok: boolean; message: string }
    }>({ url: '/scorecards/calibration/preview' }),
  applyCalibration: (change_reason: string) =>
    unwrap<{ version: string; weights: Record<string, number>; recalculated: number; validation: { ok: boolean } }>({
      url: '/scorecards/calibration/apply',
      method: 'POST',
      data: { change_reason, suggestions: [] },
    }),
}

/* ------------------------------------------------------------------ 需求探针 */
export const demandApi = {
  probe: (payload: { keywords: string[]; sources?: string[]; industry?: string }) =>
    unwrap<DemandProbe>({ url: '/demand/probe', method: 'POST', data: payload }),
  get: (taskId: string) => unwrap<DemandProbe>({ url: `/demand/probe/${taskId}` }),
  painPoints: (industry?: string) =>
    unwrap<{
      pain_points: PainPoint[]
      demand_score_5: number
      coverage: Record<string, number>
      sample_size: number
    }>({ url: '/demand/pain-points', params: { industry } }),
}

/* ------------------------------------------------------------------ 实验 */
export const experimentApi = {
  templates: () =>
    unwrap<{ templates: Array<Record<string, any>>; count: number; requirement_met: boolean }>({
      url: '/experiments/templates',
    }),
  list: (params?: { status?: string; opportunity_id?: string }) =>
    unwrap<{ total: number; grouped: Record<string, Experiment[]>; experiments: Experiment[] }>({
      url: '/experiments',
      params,
    }),
  detail: (id: string) => unwrap<Experiment>({ url: `/experiments/${id}` }),
  create: (payload: { opportunity_id?: string; hypothesis: string; method: string; title?: string }) =>
    unwrap<{ id: string; status: string; method_name: string; metrics: Record<string, any> }>({
      url: '/experiments',
      method: 'POST',
      data: payload,
    }),
  update: (id: string, payload: Record<string, unknown>) =>
    unwrap<Experiment>({ url: `/experiments/${id}`, method: 'PATCH', data: payload }),
  writeback: (id: string) =>
    unwrap<{
      experiment_id: string
      achievement_rate: number
      opportunity_status: string
      new_total_score: number
      dimensions: any[]
    }>({ url: `/experiments/${id}/writeback`, method: 'POST' }),
  exportCsv: (id: string) =>
    unwrap<{ content: string; filename: string }>({ url: `/experiments/${id}/export` }),
}

/* ------------------------------------------------------------------ 报告 */
export const reportApi = {
  templates: () =>
    unwrap<{ templates: Array<{ key: string; name: string; sections: string[]; tone: string }>; formats: string[] }>({
      url: '/reports/templates',
    }),
  list: (opportunity_id?: string) =>
    unwrap<{ total: number; reports: Report[] }>({ url: '/reports', params: { opportunity_id } }),
  create: (payload: { opportunity_id: string; template: string; format: string; title?: string }) =>
    unwrap<Report & { file_path?: string; sections: string[]; sections_detail: Array<{ key: string; title: string; markdown: string; citations: string[] }> }>(
      { url: '/reports', method: 'POST', data: payload },
    ),
  detail: (id: string) => unwrap<Report>({ url: `/reports/${id}` }),
  exportAs: (id: string, fmt: string) =>
    unwrap<{ id: string; format: string; path?: string; url?: string; error?: string }>({
      url: `/reports/${id}/export`,
      method: 'POST',
      params: { fmt },
    }),
}

/* ------------------------------------------------------------------ 监控 */
export const monitorApi = {
  list: (params?: { status?: string; level?: string }) =>
    unwrap<{
      total: number
      severity: Record<string, number>
      grouped: Record<string, Alert[]>
      alerts: Alert[]
    }>({ url: '/alerts', params }),
  action: (id: string, action: 'handle' | 'ignore' | 'reopen', note = '') =>
    unwrap<{ id: string; status: string; handled_at?: string }>({
      url: `/alerts/${id}/action`,
      method: 'POST',
      data: { action, note },
    }),
  scan: () =>
    unwrap<{ scanned_signals: number; generated: number; new_alerts: number; calibration: any[] }>({
      url: '/alerts/scan',
      method: 'POST',
    }),
  replay: (payload: { decision_id: string; outcome: Record<string, unknown>; review: string }) =>
    unwrap<{ replay: Replay; scorecard_version: string; can_apply: boolean }>({
      url: '/alerts/replay',
      method: 'POST',
      data: payload,
    }),
  replays: () => unwrap<{ total: number; replays: Replay[] }>({ url: '/replays' }),
  applyReplay: (id: string) =>
    unwrap<{ version: string; weights: Record<string, number>; recalculated: number }>({
      url: `/replays/${id}/apply`,
      method: 'POST',
    }),
}

/* ------------------------------------------------------------------ 管理 */
export const adminApi = {
  users: () =>
    unwrap<{ total: number; users: Array<{ id: string; email: string; name?: string; role: string; is_active: boolean; created_at?: string }> }>(
      { url: '/admin/users' },
    ),
  createUser: (payload: { email: string; name?: string; role: string }) =>
    unwrap<{ id: string; email: string; role: string; initial_password?: string }>({
      url: '/admin/users',
      method: 'POST',
      data: payload,
    }),
  roles: () =>
    unwrap<{
      roles: Array<{ name: string; display_name?: string; permissions: string[] }>
      matrix: Array<{ role: string; permissions: string[] }>
      all_permissions: string[]
    }>({ url: '/admin/roles' }),
  auditLogs: (limit = 100) =>
    unwrap<{ total: number; retention_days: number; logs: Array<Record<string, any>> }>({
      url: '/admin/audit-logs',
      params: { limit },
    }),
}
