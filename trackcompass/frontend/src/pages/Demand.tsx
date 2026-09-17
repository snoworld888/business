import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import PageHead from '../components/PageHead'
import { EmptyState, SIGNAL_TYPE_LABEL, formatClock, safeText } from '../components/widgets'
import { usePersistentState } from '../hooks/usePersistentState'
import { useAsync } from '../hooks/useAuth'
import { runTask, clearTask, useTask } from '../api/taskStore'
import { readSessionState } from '../api/uiState'
import { demandApi, signalApi } from '../api/client'
import type { DemandProbe } from '../api/types'

const SOURCES = [
  { value: 'social', label: '社媒' },
  { value: 'forum', label: '论坛' },
  { value: 'review', label: '应用评论' },
  { value: 'bidding', label: '招标' },
  { value: 'recruit', label: '招聘' },
]

/** 需求侧类型 —— 用户/客户**自己发声**，是「需求强度」的主证据（与后端
 *  `agents/demand.py::DEMAND_SOURCE_TYPES` 对齐）。其余类型作为补充证据。 */
const DEMAND_SIDE = new Set(SOURCES.map((s) => s.value))

const INDUSTRIES = ['AI+制造', 'AI+医疗', '新能源科技', '具身智能']

/** 上一次探针的检索条件快照 —— 结果会跨路由保留，必须能说清「它是按什么条件跑出来的」 */
interface DemandRun {
  at: string
  keywords: string[]
  sources: string[]
  industry?: string
}

/** P06 需求探针（《原型设计》§5.6）
 *
 * 状态全部走 `usePersistentState`（会话级）：用户切到别的菜单再回来，
 * 关键词 / 数据源 / 行业 / 上一次结果都还在，不会变空白、不需要重跑。
 */
export default function Demand() {
  const navigate = useNavigate()
  const [keywords, setKeywords] = usePersistentState<string[]>('demand:keywords', ['质检'])
  const [sources, setSources] = usePersistentState<string[]>('demand:sources', [
    'social',
    'forum',
    'review',
    'bidding',
    'recruit',
  ])
  const [industry, setIndustry] = usePersistentState<string | undefined>(
    'demand:industry',
    'AI+制造',
  )
  const [progress, setProgress] = useState(0)
  const [result, setResult] = usePersistentState<DemandProbe | null>('demand:result', null)
  const [lastRun, setLastRun] = usePersistentState<DemandRun | null>('demand:lastRun', null)
  /** 探针跑在应用级任务注册表里 —— 切页不会中断，回来仍能认领结果 */
  const probe = useTask<DemandProbe>('demand:probe')
  const loading = probe?.status === 'running'

  /** 信号库里各类型的存量 —— 用来解释「为什么结果是 0 条」，也用于补默认数据源 */
  const { data: signalPool } = useAsync(() => signalApi.list({ limit: 1 }), [])
  const poolCounts = signalPool?.stats ?? {}
  const poolSummary = Object.entries(poolCounts)
    .map(([k, v]) => `${SIGNAL_TYPE_LABEL[k] ?? k} ${v} 条`)
    .join(' · ')

  /**
   * 「用户是否手动改过数据源」—— 初始值直接读会话态：
   * 改过就**不再**自动补默认，避免把用户的选择覆盖掉。
   */
  const sourcesTouched = useRef(readSessionState<string[]>('demand:sources') !== undefined)

  const pickSources = (values: string[]) => {
    sourcesTouched.current = true
    setSources(values)
  }

  /**
   * ① 默认数据源 = 设计文档的需求侧五类 **∪ 信号库里真实存在的类型**。
   *
   * 为什么必须扩（用户裁决①，2026-09-16）：需求侧五类在库里**一条都没有**
   * （实测 48 条全是 产业媒体/论文/政策）⇒ 只给设计文档的默认值，
   * 无论关键词填什么命中恒为 0，页面看起来永远「空白」（L-15）。
   * 只在用户没动过时补一次，之后完全尊重用户选择。
   */
  useEffect(() => {
    if (sourcesTouched.current || !signalPool) return
    const available = Object.keys(signalPool.stats ?? {})
    if (!available.length) return
    sourcesTouched.current = true
    setSources(Array.from(new Set([...SOURCES.map((s) => s.value), ...available])))
  }, [signalPool, setSources])

  /**
   * 数据源选项：设计侧五类 ∪ 信号库实际类型 ∪ 已选值，**按证据强度分组**。
   *
   * 分组是 ③「放宽口径」在界面上的表达：告诉用户哪些是用户发声（主证据）、
   * 哪些是补充信号，而不是把 8 个类型混成一排让人猜。
   */
  const sourceOptions = useMemo(() => {
    const values = Array.from(
      new Set([...SOURCES.map((s) => s.value), ...Object.keys(poolCounts), ...sources]),
    )
    const opt = (v: string) => {
      const name = SOURCES.find((s) => s.value === v)?.label ?? SIGNAL_TYPE_LABEL[v] ?? v
      return { value: v, label: signalPool ? `${name}（${poolCounts[v] ?? 0}）` : name }
    }
    const groups: Array<{
      label: string
      options: Array<{ value: string; label: string }>
    }> = []
    const demandSide = values.filter((v) => DEMAND_SIDE.has(v))
    const contextSide = values.filter((v) => !DEMAND_SIDE.has(v))
    if (demandSide.length) {
      groups.push({ label: '需求侧信号（用户发声，主证据）', options: demandSide.map(opt) })
    }
    if (contextSide.length) {
      groups.push({ label: '补充信号（同样承载需求信息）', options: contextSide.map(opt) })
    }
    return groups
  }, [poolCounts, signalPool, sources])

  /**
   * 检索条件快照 vs 表单当前条件 —— 不一致说明结果是「过期」的，必须提示，
   * 否则用户会以为结果对应的是刚改完的条件。
   */
  const stale = useMemo(() => {
    if (!lastRun) return false
    return (
      lastRun.keywords.join('\u0000') !== keywords.join('\u0000') ||
      lastRun.sources.join('\u0000') !== sources.join('\u0000') ||
      (lastRun.industry ?? '') !== (industry ?? '')
    )
  }, [lastRun, keywords, sources, industry])

  /**
   * 跑探针。**不等待、不阻塞页面**：任务挂在应用级注册表上，
   * 用户随时可以切走，跑完会弹全局通知，回到本页自动认领结果。
   */
  const run = async () => {
    // 一旦开始跑，就当成「用户已确定数据源」：
    // 否则「首屏自动补全默认数据源」可能在请求返回后**把条件改掉**，
    // 让刚出的结果立刻变成「条件已改动」的过期结果。
    sourcesTouched.current = true
    setProgress(10)
    await runTask<DemandProbe>(
      'demand:probe',
      {
        page: '需求探针',
        title: '需求分析',
        params: { keywords, sources, industry },
      },
      () => demandApi.probe({ keywords, sources, industry }),
      {
        summarize: (r) =>
          `匹配 ${r.matched_signals ?? 0} 条信号，聚类 ${(r.pain_points ?? []).length} 类痛点，` +
          `综合强度 ${r.demand_score_5}`,
      },
    )
  }

  /** 进度条只是「还在跑」的视觉反馈，不做精确进度（切页回来同样能重新拉起） */
  useEffect(() => {
    if (!loading) {
      setProgress(0)
      return
    }
    setProgress(10)
    const timer = setInterval(() => setProgress((p) => Math.min(92, p + 12)), 600)
    return () => clearInterval(timer)
  }, [loading])

  /**
   * 认领任务结果 —— 无论任务是「本页完成」还是「用户离开期间完成」都走这里，
   * 因此结果与它对应的检索条件（任务自带的 `meta.params`）一起落到页面状态。
   */
  useEffect(() => {
    if (!probe || probe.status !== 'done' || !probe.result) return
    const p = probe.meta.params as Partial<DemandRun> | undefined
    setResult(probe.result)
    setLastRun({
      at: probe.finishedAt ?? probe.startedAt,
      keywords: p?.keywords ?? [],
      sources: p?.sources ?? [],
      industry: p?.industry,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [probe?.finishedAt, probe?.status])

  const clearResult = () => {
    setResult(null)
    setLastRun(null)
    setProgress(0)
    // 连任务一起清：否则下次进来「认领任务结果」会把刚清掉的结果又捞回来
    clearTask('demand:probe')
  }

  /**
   * 结果为 0 时的**归因**：是「选中的源根本没数据」，还是「有数据但关键词没命中」。
   * 这两件事的下一步动作完全不同，混成一句「未聚类到痛点」等于什么都没说。
   */
  const zeroReason = (() => {
    const withStock = sources.filter((v) => (poolCounts[v] ?? 0) > 0)
    if (!withStock.length) {
      return `所选数据源在信号库里暂无存量（当前库内：${poolSummary || '空库'}）。` +
        '请改选有存量的数据源，或先去信号库采集对应类型的信号。'
    }
    const total = withStock.reduce((acc, v) => acc + (poolCounts[v] ?? 0), 0)
    return `所选数据源有 ${withStock.length} 类、共 ${total} 条信号，但关键词没有命中任何一条 —— ` +
      '换用更贴近标题用词的检索词，或加入更多数据源。'
  })()

  const createExperiment = (method: string) => {
    const map: Record<string, string> = {
      落地页测试: 'landing_page',
      客户访谈: 'interview',
      问卷调研: 'survey',
      POC: 'poc',
      预售: 'presale',
      定价测试: 'presale',
      渠道测试: 'channel_test',
    }
    const m = map[method] ?? 'interview'
    navigate(`/experiments?method=${m}&hypothesis=${encodeURIComponent(`客户需要解决：${keywords.join('、')}`)}`)
  }

  const painPoints = result?.pain_points ?? []

  return (
    <>
      <PageHead
        title="需求探针"
        desc={
          <span>
            扫描外部信号聚类痛点并输出需求强度。<b>需求侧信号（社媒/评论/论坛/招标/招聘）为主证据</b>，
            产业媒体 / 论文 / 政策作为补充（政策强制要求、技术未解痛点、行业扩产诉求同样是需求信息），
            取值时按「需求侧优先」排序。公式{' '}
            <code className="tc-mono">
              0.3*freq + 0.3*负面情感 + 0.2*付费意愿 + 0.2*紧迫度
            </code>
          </span>
        }
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap align="center">
            <Typography.Text style={{ width: 62 }}>关键词</Typography.Text>
            <Select
              mode="tags"
              style={{ minWidth: 320, flex: 1 }}
              value={keywords}
              onChange={setKeywords}
              placeholder="输入关键词后回车，如：质检 / 漏检 / 部署成本"
              tokenSeparators={[',', '，']}
            />
            <Select
              allowClear
              placeholder="行业"
              style={{ width: 160 }}
              value={industry}
              onChange={setIndustry}
              options={INDUSTRIES.map((v) => ({ value: v, label: v }))}
            />
          </Space>
          <Space wrap align="center">
            <Typography.Text style={{ width: 62 }}>数据源</Typography.Text>
            <Select
              mode="multiple"
              style={{ minWidth: 420 }}
              value={sources}
              onChange={pickSources}
              options={sourceOptions}
            />
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={loading}
              onClick={run}
              disabled={keywords.length === 0 || sources.length === 0}
            >
              开始分析
            </Button>
          </Space>

          {loading || progress > 0 ? (
            <div>
              <Progress percent={progress} status={progress === 100 ? 'success' : 'active'} />
              <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                {progress < 40
                  ? '正在检索多源信号…'
                  : progress < 75
                    ? '正在聚类痛点…'
                    : progress < 100
                      ? '正在计算需求强度…'
                      : '分析完成'}
              </Typography.Text>
            </div>
          ) : null}
        </Space>
      </Card>

      {result ? (
        <>
          <Space
            style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }}
            wrap
          >
            <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
              上次分析 {formatClock(lastRun?.at)}
              {lastRun
                ? `（关键词 ${lastRun.keywords.join('、') || '—'} · 数据源 ${lastRun.sources.length} 个${
                    lastRun.industry ? ` · ${lastRun.industry}` : ''
                  }）`
                : ''}
            </Typography.Text>
            <a onClick={clearResult} style={{ fontSize: 12 }}>
              清除结果
            </a>
          </Space>

          {stale ? (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="检索条件已改动，下方仍是上一次分析的结果"
              description="点击「开始分析」按新条件重跑；否则请以「上次分析」括号内的条件为准。"
            />
          ) : null}

          <Row gutter={[16, 16]}>
          <Col xs={24} lg={16}>
            <Card title={`痛点聚类（${painPoints.length} 类）`} size="small">
              {painPoints.length === 0 ? (
                <EmptyState
                  title="未聚类到痛点"
                  description={
                    (result.matched_signals ?? 0) === 0
                      ? zeroReason
                      : '命中信号不足以聚类出痛点，尝试更换关键词或扩大数据源'
                  }
                  actionText="去信号库采集"
                  onAction={() => navigate('/signals')}
                />
              ) : (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {painPoints.map((p) => (
                    <Card key={p.name} size="small" styles={{ body: { padding: 12 } }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
                        <Space>
                          <Typography.Text strong>{p.name}</Typography.Text>
                          <Tag className="tc-tag-soft">频次 {p.freq}</Tag>
                          <Tag color="volcano" className="tc-tag-soft">
                            情感 {p.sentiment}
                          </Tag>
                          <Tag color="blue" className="tc-tag-soft">
                            付费意愿 {p.willingness}
                          </Tag>
                          <Tag color="orange" className="tc-tag-soft">
                            紧迫度 {p.urgency}
                          </Tag>
                        </Space>
                        <Typography.Text strong style={{ color: '#2563EB' }}>
                          强度 {p.demand_score}
                        </Typography.Text>
                      </Space>
                      <div style={{ marginTop: 8 }}>
                        <Progress
                          percent={p.demand_score * 20}
                          showInfo={false}
                          strokeColor={p.demand_score >= 4 ? '#10B981' : '#2563EB'}
                          size="small"
                        />
                      </div>
                      <div style={{ marginTop: 8 }}>
                        <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                          样本评论：
                        </Typography.Text>
                        {(p.samples ?? []).map((s, i) => (
                          <div key={i} className="tc-muted" style={{ fontSize: 12, paddingLeft: 8 }}>
                            • {safeText(s)}
                          </div>
                        ))}
                      </div>
                      <Space size={8} wrap style={{ marginTop: 10 }}>
                        {(p.experiments ?? []).map((m, i) => {
                          const label = safeText(m)
                          return (
                            <a
                              key={`${i}-${label}`}
                              onClick={() => createExperiment(label)}
                              style={{ fontSize: 12 }}
                            >
                              创建「{label}」
                            </a>
                          )
                        })}
                      </Space>
                    </Card>
                  ))}
                </Space>
              )}
            </Card>
          </Col>

          <Col xs={24} lg={8}>
            <Card size="small" style={{ marginBottom: 16 }}>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="综合需求强度"
                    value={result.demand_score_5}
                    suffix="/ 5.0"
                    valueStyle={{ color: '#2563EB', fontSize: 22 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic title="匹配信号" value={result.matched_signals ?? 0} valueStyle={{ fontSize: 22 }} />
                </Col>
              </Row>
            </Card>

            <Card title="样本覆盖" size="small" style={{ marginBottom: 16 }}>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {Object.entries(result.coverage ?? {}).map(([k, v]) => (
                  <div key={k}>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <Typography.Text style={{ fontSize: 12 }}>{k}</Typography.Text>
                      <Typography.Text className="tc-mono">{(v * 100).toFixed(1)}%</Typography.Text>
                    </Space>
                    <Progress percent={v * 100} showInfo={false} size="small" />
                  </div>
                ))}
                {Object.keys(result.coverage ?? {}).length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无覆盖数据" />
                ) : null}
              </Space>
            </Card>

            <Card title="建议验证实验" size="small">
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {result.suggested_experiments.map((e, i) => {
                  const label = safeText(e)
                  return (
                    <Button key={`${i}-${label}`} block onClick={() => createExperiment(label)}>
                      {label}
                    </Button>
                  )
                })}
              </Space>
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message="点击建议实验将自动跳转到验证实验页并预填假设"
              />
            </Card>
          </Col>
        </Row>
        </>
      ) : (
        <Card>
          <EmptyState
            title="尚未执行分析"
            description="配置关键词与数据源后点击「开始分析」，需求挖掘 Agent 将聚类痛点并给出需求强度"
          />
        </Card>
      )}
    </>
  )
}
