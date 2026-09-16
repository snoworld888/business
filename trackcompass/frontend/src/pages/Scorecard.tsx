import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Empty,
  Form,
  Modal,
  Row,
  Select,
  Slider,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined, SettingOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import PageHead from '../components/PageHead'
import Radar from '../components/Radar'
import EvidenceDrawer from '../components/EvidenceDrawer'
import { EmptyState, RankingBars } from '../components/widgets'
import { opportunityApi, scorecardApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import type { AgentTraceItem, OpportunityScore, ScoreDimension } from '../api/types'

const VERDICT_COLOR: Record<string, string> = {
  go: 'success',
  verify: 'processing',
  watch: 'warning',
  hold: 'default',
}

/** P07 赛道评分（《原型设计》§5.7） */
export default function Scorecard() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const selectedId = params.get('opportunity') ?? undefined

  const [analyzing, setAnalyzing] = useState(false)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [localWeights, setLocalWeights] = useState<Record<string, number> | null>(null)
  const [preview, setPreview] = useState<{ total_score: number; explained?: OpportunityScore['explained'] } | null>(null)
  const [trace, setTrace] = useState<AgentTraceItem[]>([])

  const { data: card, refresh: refreshCard } = useAsync(() => scorecardApi.default(), [])
  const { data: list, refresh: refreshList } = useAsync(
    () => opportunityApi.list({ sort: 'score', limit: 50 }),
    [],
  )
  const { data: detail, refresh: refreshDetail } = useAsync(
    () => (selectedId ? opportunityApi.detail(selectedId) : Promise.resolve(null)),
    [selectedId],
  )
  const { data: score } = useAsync(
    () => (selectedId ? opportunityApi.score(selectedId) : Promise.resolve(null)),
    [selectedId, detail?.updated_at],
  )

  const opportunities = list?.opportunities ?? []
  const current = useMemo(
    () => opportunities.find((o) => o.id === selectedId),
    [opportunities, selectedId],
  )

  useEffect(() => {
    setLocalWeights(null)
    setPreview(null)
  }, [selectedId])

  const activeScore = preview?.total_score ?? score?.total_score ?? 0
  const activeDimensions: ScoreDimension[] =
    (preview?.explained?.dimensions as ScoreDimension[] | undefined) ??
    score?.dimensions ??
    []

  const radarData = activeDimensions.map((d) => ({ name: d.name, value: d.score }))

  const handleAnalyze = async () => {
    if (!selectedId) {
      message.warning('请先选择一个机会卡片')
      return
    }
    setAnalyzing(true)
    try {
      const res = await opportunityApi.analyze(selectedId, {
        industry: current?.track ?? undefined,
        days: 3650,
        max_loops: 1,
        persist: true,
      })
      setTrace(res.trace as AgentTraceItem[])
      const engines = new Set(res.trace.map((t) => (t as AgentTraceItem).engine ?? 'rule-engine'))
      const engineLabel = engines.has('llm') ? '真实大模型' : '规则引擎'
      message.success(
        `七 Agent 分析完成（${res.trace.length} 个 Agent · ${engineLabel} · ${res.signal_count} 条信号），综合评分 ${res.total_score}`,
      )
      await Promise.all([refreshList(), refreshDetail()])
      navigate(`/scorecard?opportunity=${selectedId}`, { replace: true })
    } finally {
      setAnalyzing(false)
    }
  }

  const handlePreview = async (weights: Record<string, number>) => {
    if (!selectedId) return
    try {
      const res = await opportunityApi.preview(selectedId, weights)
      setPreview({ total_score: res.total_score, explained: res.explained })
    } catch {
      message.error('预览失败：该机会尚未分析')
    }
  }

  const saveWeights = async () => {
    if (!card || !localWeights) return
    const total = Object.values(localWeights).reduce((a, b) => a + b, 0)
    if (Math.abs(total - 1) > 1e-6) {
      message.error(`权重之和应为 1.0，当前 ${total.toFixed(4)}`)
      return
    }
    const res = await scorecardApi.updateWeights(card.id, localWeights, '页面人工调整权重')
    message.success(`权重已保存为 ${res.version}，重算 ${res.recalculated_opportunities} 个机会`)
    setLocalWeights(null)
    setPreview(null)
    await Promise.all([refreshCard(), refreshList(), refreshDetail()])
  }

  const weightModal = () => {
    if (!card) return
    const base = localWeights ?? card.weights
    setLocalWeights({ ...base })
  }

  return (
    <>
      <PageHead
        title="赛道评分"
        desc="可配置权重的多维评分模型。每个维度分数可下钻到证据链，权重调整实时更新排序。"
        extra={
          <Space>
            <Select
              style={{ width: 240 }}
              placeholder="选择机会卡片"
              value={selectedId}
              onChange={(v) => setParams({ opportunity: v })}
              options={opportunities.map((o) => ({
                value: o.id,
                label: `${o.title}（${(o.total_score ?? 0).toFixed(2)}）`,
              }))}
            />
            <Button icon={<SettingOutlined />} onClick={weightModal} disabled={!card}>
              配置权重
            </Button>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={analyzing}
              onClick={handleAnalyze}
              disabled={!selectedId}
            >
              重跑七 Agent 分析
            </Button>
          </Space>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card
            size="small"
            title={
              <Space>
                <span>{detail?.title ?? '未选择机会'}</span>
                {score?.verdict ? (
                  <Tag color={VERDICT_COLOR[score.verdict.code]} className="tc-tag-soft">
                    {score.verdict.label}
                  </Tag>
                ) : null}
              </Space>
            }
            extra={
              <Space>
                <Statistic
                  value={activeScore}
                  precision={2}
                  suffix="/ 5.0"
                  valueStyle={{
                    fontSize: 20,
                    color: activeScore >= 4.2 ? '#10B981' : activeScore >= 3.5 ? '#2563EB' : '#F59E0B',
                  }}
                />
              </Space>
            }
            style={{ marginBottom: 16 }}
          >
            {!selectedId ? (
              <EmptyState
                title="尚未选择机会"
                description="从右上角选择一个机会卡片，或到趋势雷达把候选方向加入机会池"
                actionText="去趋势雷达"
                onAction={() => navigate('/trends')}
              />
            ) : !score?.dimensions?.length ? (
              <EmptyState
                title="该机会尚未分析"
                description="点击右上角「重跑七 Agent 分析」，系统将执行趋势/需求/市场/竞争/技术/决策/监控七步流水线"
                actionText="立即分析"
                onAction={handleAnalyze}
              />
            ) : (
              <>
                <Table
                  size="small"
                  pagination={false}
                  rowKey="key"
                  dataSource={activeDimensions}
                  columns={[
                    {
                      title: '维度',
                      dataIndex: 'name',
                      width: 110,
                      render: (v: string, r: ScoreDimension) => (
                        <Tooltip title={r.rationale}>
                          <span style={{ borderBottom: '1px dashed #CBD5E1' }}>{v}</span>
                        </Tooltip>
                      ),
                    },
                    {
                      title: '权重',
                      dataIndex: 'weight',
                      width: 80,
                      render: (v: number) => `${(v * 100).toFixed(0)}%`,
                    },
                    {
                      title: '得分',
                      dataIndex: 'score',
                      width: 90,
                      render: (v: number) => (
                        <Typography.Text
                          strong
                          style={{ color: v >= 4 ? '#10B981' : v >= 3 ? '#2563EB' : '#F59E0B' }}
                        >
                          {v.toFixed(2)}
                        </Typography.Text>
                      ),
                    },
                    {
                      title: '证据',
                      dataIndex: 'evidence',
                      width: 80,
                      render: (v: string[]) =>
                        v?.length ? (
                          <a onClick={() => setEvidenceOpen(true)}>查看 {v.length}</a>
                        ) : (
                          <span className="tc-muted">—</span>
                        ),
                    },
                    { title: '评分理由', dataIndex: 'rationale', ellipsis: true },
                  ]}
                />

                {score?.conflicts?.length ? (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginTop: 12 }}
                    message="结论冲突点（需人工裁决）"
                    description={
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {score.conflicts.map((c) => (
                          <li key={c}>{c}</li>
                        ))}
                      </ul>
                    }
                  />
                ) : null}
              </>
            )}
          </Card>

          <Card
            title="排序对比"
            size="small"
            extra={
              <a onClick={() => navigate('/reports')}>生成决策报告</a>
            }
          >
            <RankingBars
              items={opportunities.map((o) => ({
                id: o.id,
                title: o.title,
                total_score: o.total_score ?? 0,
                rank: o.rank ?? 0,
              }))}
              onSelect={(id) => setParams({ opportunity: id })}
            />
          </Card>
        </Col>

        <Col xs={24} xl={9}>
          <Card title="雷达图" size="small" style={{ marginBottom: 16 }}>
            {radarData.length ? (
              <Radar data={radarData} max={5} height={280} name="赛道评分" />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无评分" />
            )}
          </Card>

          <Card title="风险预警" size="small" style={{ marginBottom: 16 }}>
            {score?.risk_alerts?.length ? (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {score.risk_alerts.map((r, i) => (
                  <Alert
                    key={i}
                    type={r.level === 'critical' ? 'error' : 'warning'}
                    showIcon
                    message={r.title}
                    description={<span style={{ fontSize: 12 }}>{r.detail}</span>}
                  />
                ))}
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无触发阈值的风险项" />
            )}
          </Card>

          <Card
            title="当前评分卡"
            size="small"
            extra={<Tag color="blue" className="tc-tag-soft">{card?.version}</Tag>}
          >
            <Descriptions column={1} size="small">
              {(card?.dimensions ?? []).map((d) => (
                <Descriptions.Item key={d.key} label={d.name}>
                  {((localWeights ?? card?.weights ?? {})[d.key] * 100).toFixed(0)}%
                </Descriptions.Item>
              ))}
            </Descriptions>
            {card?.validation && !card.validation.ok ? (
              <Alert type="error" showIcon message={card.validation.message} />
            ) : null}
          </Card>

          {trace.length ? (
            <Card
              title="本次分析执行明细"
              size="small"
              style={{ marginTop: 16 }}
              extra={
                <Tag
                  color={trace.some((t) => t.engine === 'llm') ? 'success' : 'default'}
                  className="tc-tag-soft"
                >
                  {trace.some((t) => t.engine === 'llm') ? '真实大模型' : '规则引擎'}
                </Tag>
              }
            >
              <Table
                size="small"
                pagination={false}
                rowKey={(r: AgentTraceItem, i?: number) => String(r.seq ?? `${r.agent}-${i}`)}
                dataSource={trace}
                columns={[
                  { title: 'Agent', dataIndex: 'agent_name', ellipsis: true },
                  {
                    title: '引擎',
                    dataIndex: 'engine',
                    width: 90,
                    render: (v?: string) => (
                      <Tag
                        color={v === 'llm' ? 'success' : 'default'}
                        className="tc-tag-soft"
                        style={{ fontSize: 11 }}
                      >
                        {v === 'llm' ? '大模型' : '规则'}
                      </Tag>
                    ),
                  },
                  { title: '模型', dataIndex: 'model', width: 120, ellipsis: true },
                  {
                    title: '耗时',
                    dataIndex: 'latency_ms',
                    width: 80,
                    render: (v: number) => `${v}ms`,
                  },
                ]}
              />
              <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                耗时为亚毫秒级说明未发生网络调用（规则引擎）；数十到数百毫秒代表真实大模型推理。
              </Typography.Text>
            </Card>
          ) : null}
        </Col>
      </Row>

      <EvidenceDrawer
        open={evidenceOpen}
        onClose={() => setEvidenceOpen(false)}
        evidence={detail?.evidence ?? []}
        title={`${detail?.title ?? ''} 证据链`}
      />

      <Modal
        title="配置评分卡权重"
        open={localWeights !== null}
        onCancel={() => setLocalWeights(null)}
        onOk={saveWeights}
        okText="保存并重算排序"
        width={560}
      >
        {localWeights ? (
          <>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="权重之和必须等于 100%，调整后实时更新综合评分与排序"
            />
            {(card?.dimensions ?? []).map((d) => (
              <Form.Item key={d.key} label={`${d.name}（${((localWeights[d.key] ?? 0) * 100).toFixed(0)}%）`}>
                <Slider
                  min={0}
                  max={50}
                  value={Math.round((localWeights[d.key] ?? 0) * 100)}
                  onChange={(v) =>
                    setLocalWeights({ ...localWeights, [d.key]: Number((v / 100).toFixed(4)) })
                  }
                />
              </Form.Item>
            ))}
            <Divider style={{ margin: '8px 0' }} />
            <Space>
              <Typography.Text>
                合计：
                <Typography.Text
                  strong
                  type={
                    Math.abs(Object.values(localWeights).reduce((a, b) => a + b, 0) - 1) < 1e-6
                      ? 'success'
                      : 'danger'
                  }
                >
                  {(Object.values(localWeights).reduce((a, b) => a + b, 0) * 100).toFixed(1)}%
                </Typography.Text>
              </Typography.Text>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                disabled={!selectedId}
                onClick={() => handlePreview(localWeights)}
              >
                预览排序影响
              </Button>
              {preview ? (
                <Tag color="blue" className="tc-tag-soft">
                  预览评分 {preview.total_score.toFixed(2)}
                </Tag>
              ) : null}
            </Space>
          </>
        ) : null}
      </Modal>
    </>
  )
}
