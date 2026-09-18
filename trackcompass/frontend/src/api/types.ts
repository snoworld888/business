/** 与后端 /api/v1 契约对齐的类型定义（技术文档 §6.2） */

export interface Envelope<T> {
  code: number
  message: string
  data: T
}

export interface UserInfo {
  id: string
  email: string
  name?: string
  role: string
  tenant_id: string
  is_active: boolean
  permissions?: string[]
}

export interface LoginResult {
  access_token: string
  token_type: string
  expires_in: number
  user: UserInfo
}

export interface CompanyProfile {
  id: string
  name: string
  industry?: string
  founded_at?: string
  scale?: string
  tech_stack: string[]
  tech_description: string
  patents: string[]
  talents: Record<string, number>
  funding: Record<string, unknown>
  channels: string[]
  customers: string[]
  supply_chain: string[]
  radar: Record<string, number>
  recommended_tracks: string[]
  boundary: Record<string, unknown>
  vector_dim?: number
  created_at?: string | null
  updated_at?: string | null
}

/** 公司画像列表项（多公司切换用，不含向量等大字段） */
export interface CompanyProfileSummary {
  id: string
  name: string
  industry?: string | null
  scale?: string | null
  vector_dim: number
  updated_at?: string | null
  /** 是否为当前生效公司（后端按「最近保存」判定） */
  is_current: boolean
}

export interface Signal {
  id: string
  signal_type: string
  source: string
  title: string
  content: string
  url?: string
  industry?: string
  keywords: string[]
  metrics: Record<string, unknown>
  sentiment: number
  published_at?: string
}

export interface Evidence {
  ref: string
  signal_id: string
  source: string
  signal_type: string
  title: string
  url?: string
  excerpt: string
  score: number
}

export interface Trend {
  id: string
  name: string
  description: string
  industry?: string
  confidence: number
  direction: 'tailwind' | 'headwind'
  score: number
  factors: Record<string, number>
  evidence: Array<Record<string, unknown>>
  evidence_count?: number
  subscribed: boolean
  in_pool: boolean
  /** 该趋势是否命中本公司能力画像（由趋势侦察 Agent 交叉核对得出） */
  capability_match?: boolean
  /** 命中的能力词（来自画像的技术栈/专利） */
  matched_capabilities?: string[]
}

/** 画像里写了、但外部信号零证据的方向 —— 情报空白，不是趋势 */
export interface CapabilityGap {
  name: string
  source: string
  evidence_count: number
  note: string
}

export interface TrendScanResult {
  scanned_signals: number
  generated: number
  top: Array<{
    name: string
    score: number
    direction: string
    confidence: number
    capability_match?: boolean
    matched_capabilities?: string[]
  }>
  capability_gaps?: CapabilityGap[]
  capability_matched?: string[]
  capability_terms?: string[]
}

export interface ScoreDimension {
  key: string
  name: string
  weight: number
  score: number
  evidence: string[]
  rationale: string
  contribution?: number
}

export interface RiskAlert {
  dimension?: string
  level: string
  title: string
  detail: string
}

/**
 * 风险预警字段的实际形态：**结构化条目或纯字符串**。
 *
 * 历史数据里真实大模型产出的是叙事性风险长句（字符串数组），
 * 后端已归一（`agents/base.py::normalize_risk_alerts`），但旧库数据仍在 ——
 * 故类型如实声明，渲染一律走 `widgets.tsx::riskItem()` 转换。
 */
export type RiskAlertLike = RiskAlert | string

export interface Verdict {
  code: string
  label: string
  color: string
}

export interface OpportunityScore {
  total_score: number
  dimensions: ScoreDimension[]
  explained?: { total_score: number; dimensions: ScoreDimension[]; weights_valid: Record<string, unknown> }
  risk_alerts: RiskAlertLike[]
  verdict?: Verdict
  conflicts?: string[]
}

export interface Opportunity {
  id: string
  title: string
  summary: string
  status: string
  track_id?: string
  track?: string
  total_score?: number
  verdict?: Verdict
  score?: OpportunityScore | Record<string, never>
  evidence: Evidence[]
  risk_alerts: RiskAlertLike[]
  analysis?: AnalysisResult
  evidence_count?: number
  risk_count?: number
  rank?: number
  created_at?: string
  updated_at?: string
}

export interface PainPoint {
  name: string
  freq: number
  sentiment: number
  willingness: number
  urgency: number
  demand_score: number
  samples: string[]
  sources: string[]
  evidence_ref?: string
  experiments: string[]
}

export interface DemandProbe {
  task_id: string
  status: string
  matched_signals?: number
  pain_points: PainPoint[]
  suggested_experiments: string[]
  demand_score_5: number
  coverage: Record<string, number>
}

export interface AnalysisResult {
  trend?: { trends: Array<Record<string, unknown>>; scanned_signals: number }
  demand?: {
    pain_points: PainPoint[]
    suggested_experiments: string[]
    demand_score_5: number
    coverage: Record<string, number>
    sample_size: number
  }
  market?: {
    TAM: number
    SAM: number
    SOM: number
    growth_rate: number
    method: Record<string, unknown>
    assumptions: string[]
    unit_economics?: { total_customers?: number; arpu?: number; industry_size?: number }
  }
  competition?: {
    players: string[]
    player_count: number
    major_player_count: number
    threat_score: number
    threat_level: string
    barrier_score_5: number
    gaps: string[]
    barriers: Array<{ type: string; level: string; detail: string }>
  }
  tech?: {
    dimensions: Array<{ key: string; name: string; score: number; normalized: number; anchor?: string }>
    overall: number
    overall_9: number
    overall_5: number
    weakest: string
    strongest: string
  }
  decision?: {
    dimensions: ScoreDimension[]
    total_score: number
    risk_alerts: RiskAlertLike[]
    conflicts: string[]
    verdict: Verdict
  }
  monitor?: {
    alerts: Array<Record<string, unknown>>
    alert_count: number
    severity: Record<string, number>
    calibration: CalibrationSuggestion[]
  }
}

export interface CalibrationSuggestion {
  dimension: string
  from: number
  to: number
  delta: number
  reason: string
}

export interface Scorecard {
  id: string
  name: string
  weights: Record<string, number>
  dimensions: Array<{ key: string; name: string; weight: number }>
  is_default: boolean
  version: string
  status: string
  validation?: { ok: boolean; total: number; message: string }
}

export interface ScorecardVersion {
  version: string
  weights: Record<string, number>
  diff: Record<string, { from?: number; to?: number; delta?: number }>
  reason: string
  created_at?: string
}

export interface ExperimentMetric {
  current?: number
  target?: number
  unit?: string
}

export interface Experiment {
  id: string
  title: string
  hypothesis: string
  method: string
  method_name: string
  metrics: Record<string, ExperimentMetric>
  result: Record<string, unknown>
  conclusion: string
  status: string
  progress: number
  applied_to_score: boolean
  opportunity_id?: string
  allowed_transitions?: string[]
  progress_bars?: Array<{ metric: string; current?: number; target?: number; unit: string; achievement?: number }>
  started_at?: string
  ended_at?: string
}

export interface Report {
  id: string
  title: string
  template: string
  version: string
  status: string
  format: string
  evidence_count: number
  page_count: number
  download_url?: string
  opportunity_id?: string
  created_at?: string
  content?: { sections: Array<{ key: string; title: string; markdown: string; citations: string[] }> }
}

export interface Alert {
  id: string
  level: string
  rule_name: string
  title: string
  summary: string
  status: string
  signal_type: string
  track?: string
  opportunity_id?: string
  payload: Record<string, unknown>
  advice?: string
  created_at?: string
  handled_at?: string
}

export interface Replay {
  id: string
  decision_id: string
  title: string
  decision_at?: string
  basis: Record<string, unknown>
  outcome: Record<string, unknown>
  review: string
  calibration_suggestions: CalibrationSuggestion[]
  applied?: boolean
}

export interface DataSource {
  key: string
  name: string
  connected: boolean
  last_sync?: string
  signal_count: number
}

export interface GraphData {
  nodes: Array<{ data: { id: string; label: string; type: string } }>
  edges: Array<{ data: { id: string; source: string; target: string; label: string } }>
  node_count: number
  edge_count: number
  legend: { node_types: string[]; edge_types: string[] }
}

export interface GraphInsights {
  blank_spots: Array<{ name: string; degree: number; industry?: string }>
  centrality: Array<{ name: string; label: string; centrality: number }>
  communities: string[][]
  suggestions: Array<{ track: string; reason: string; action: string }>
}

export interface Overview {
  opportunities: number
  opportunities_verifying: number
  alerts_pending: number
  experiments_running: number
  reports: number
  trends: number
  signals: number
  signals_by_type: Record<string, number>
  generated_at: string
}

export interface AgentTraceItem {
  /** 唯一序号（反馈循环重跑时同一 agent 会多次出现，不能拿 agent 当 key） */
  seq?: number
  agent: string
  agent_name: string
  provider: string
  model?: string | null
  /** rule-engine = 确定性规则引擎；llm = 真实大模型 */
  engine?: 'rule-engine' | 'llm'
  cached?: boolean
  latency_ms: number
  prompt_chars?: number
  error?: string | null
}

/** 画像中一条原始条目 → 抽出的能力词（用于展示「这个词哪来的」） */
export interface ProfileTermSource {
  field: string
  raw: string
  terms: string[]
}

/**
 * 本次分析依据的公司能力。
 *
 * 趋势雷达的定位是「外部信号生成趋势、公司画像只做标注」——
 * 该结构把「标注」这一步变得可核查：哪些词来自技术栈、哪些来自专利抽词、
 * 最终命中了哪条趋势。
 */
export interface ProfileUsed {
  company: string | null
  terms: string[]
  by_source: ProfileTermSource[]
  matched: { term: string; trends: string[] }[]
  note?: string
}

export interface LLMHealth {
  provider: string
  remote_ready: boolean
  routes: Record<string, string>
  cache_entries: number
  cache_ttl_hours: number
}

export interface HealthInfo {
  status: string
  app: string
  env: string
  database: { ok: boolean; backend: string }
  llm: LLMHealth
  vector_store: { backend: string; documents: number }
  rag_documents: number
}

export interface WorkflowLayer {
  layer: number
  parallel: boolean
  agents: Array<{ key: string; name: string }>
}

export interface AgentCatalogItem {
  key: string
  name: string
  responsibility: string
  inputs: string[]
  outputs: string[]
}

/* ------------------------------------------------ 信号采集（技术文档 §3.1） */
export interface SourceAdapter {
  key: string
  name: string
  signal_type: string
  homepage: string
  frequency: string
  requires_key: boolean
  key_env?: string | null
  enabled_by_default: boolean
  available: boolean
  unavailable_reason?: string | null
}

export interface CollectSourceResult {
  key: string
  name: string
  signal_type: string
  status: 'ok' | 'failed' | 'skipped'
  fetched: number
  normalized: number
  dropped: number
  inserted: number
  duplicated: number
  elapsed_ms: number
  error?: string | null
  note?: string | null
}

export interface CollectReport {
  started_at: string
  elapsed_ms: number
  keywords: string[]
  industry?: string | null
  sources: CollectSourceResult[]
  total_fetched: number
  total_inserted: number
  total_duplicated: number
  total_dropped: number
  indexed_documents: number
  graph: Record<string, unknown>
  dry_run: boolean
  ok_sources: number
  failed_sources: number
  skipped_sources: number
}
