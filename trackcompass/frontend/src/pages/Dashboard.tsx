import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  List,
  Modal,
  Row,
  Segmented,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import {
  BellOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  PlusOutlined,
  RiseOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import PageHead from '../components/PageHead'
import { EmptyState, RankingBars, StatCard } from '../components/widgets'
import { experimentApi, metaApi, monitorApi, opportunityApi, reportApi, trendApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import { clearTask, runTask, useTask, useTaskVersion } from '../api/taskStore'
import type { Opportunity, TrendScanResult } from '../api/types'

const LEVEL_META: Record<string, { color: string; label: string }> = {
  critical: { color: 'error', label: '严重' },
  warning: { color: 'warning', label: '警告' },
  info: { color: 'blue', label: '提示' },
}

/** P02 工作台（《原型设计》§5.2） */
export default function Dashboard() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<'opportunity' | 'engine'>('opportunity')
  /** 趋势扫描跑在应用级任务注册表里：用户可以立刻去别的页面，跑完弹通知、回来自动弹结果 */
  const scanTask = useTask<TrendScanResult>('trends:scan')
  const scanning = scanTask?.status === 'running'
  const taskVersion = useTaskVersion()

  const { data: opps, loading: loadingOpps } = useAsync(
    () => opportunityApi.list({ sort: 'score', limit: 20 }),
    [],
  )
  const { data: alerts } = useAsync(() => monitorApi.list({ status: 'pending' }), [])
  const { data: experiments } = useAsync(async () => (await experimentApi.list()).experiments, [])
  const { data: trends } = useAsync(() => trendApi.list({ limit: 8 }), [taskVersion])
  const { data: reports } = useAsync(async () => (await reportApi.list()).reports, [])

  const opportunities = opps?.opportunities ?? []
  const pendingAlerts = alerts?.alerts ?? []

  const handleScan = async () => {
    // 与「趋势雷达」页共用同一个任务键：两处点的是同一件事，避免并发跑两遍
    await runTask<TrendScanResult>(
      'trends:scan',
      { page: '趋势雷达', title: '趋势扫描' },
      () => trendApi.scan(),
      {
        summarize: (r) => `扫描 ${r.scanned_signals} 条信号，生成 ${r.generated} 条趋势`,
      },
    )
  }

  /** 认领扫描结果（本页完成 / 离开期间完成，同一条路径） */
  useEffect(() => {
    if (!scanTask || scanTask.status !== 'done' || !scanTask.result) return
    const result = scanTask.result
    Modal.success({
      title: '趋势扫描完成',
      content: (
        <Space direction="vertical" size={4}>
          <span>扫描信号：{result.scanned_signals} 条</span>
          <span>生成趋势：{result.generated} 条</span>
          {result.top.slice(0, 3).map((t) => (
            <span key={t.name}>
              · {t.name}（{t.score.toFixed(2)}，{t.direction === 'tailwind' ? '顺风' : '逆风'}）
            </span>
          ))}
        </Space>
      ),
      okText: '去趋势雷达',
      cancelText: '知道了',
      // 弹过即「已读」：清任务，避免下次进来又弹一遍
      onOk: () => {
        clearTask('trends:scan')
        navigate('/trends')
      },
      onCancel: () => clearTask('trends:scan'),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanTask?.finishedAt, scanTask?.status])

  return (
    <>
      <PageHead
        title="工作台"
        desc="机会卡片、待办、预警与实验进度一览。所有数字均可下钻到原始信号。"
        extra={
          <Space>
            <Button icon={<ThunderboltOutlined />} loading={scanning} onClick={handleScan}>
              开始趋势扫描
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/profile')}>
              建立能力画像
            </Button>
          </Space>
        }
      />

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} lg={6}>
          <StatCard
            label="机会卡片"
            value={opportunities.length}
            icon={<RiseOutlined />}
            onClick={() => navigate('/scorecard')}
          />
        </Col>
        <Col xs={12} lg={6}>
          <StatCard
            label="待处理预警"
            value={pendingAlerts.length}
            color="#F59E0B"
            icon={<BellOutlined />}
            onClick={() => navigate('/monitor')}
          />
        </Col>
        <Col xs={12} lg={6}>
          <StatCard
            label="进行中实验"
            value={experiments?.filter((e) => e.status === 'running').length ?? 0}
            color="#10B981"
            icon={<ExperimentOutlined />}
            onClick={() => navigate('/experiments')}
          />
        </Col>
        <Col xs={12} lg={6}>
          <StatCard
            label="已生成报告"
            value={reports?.length ?? 0}
            color="#64748B"
            icon={<FileTextOutlined />}
            onClick={() => navigate('/reports')}
          />
        </Col>
      </Row>

      <Segmented
        value={tab}
        onChange={(v) => setTab(v as 'opportunity' | 'engine')}
        options={[
          { label: '机会与预警', value: 'opportunity' },
          { label: '分析引擎状态', value: 'engine' },
        ]}
        style={{ marginBottom: 16 }}
      />

      {tab === 'opportunity' ? (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={14}>
            <Card
              title="机会卡片"
              size="small"
              extra={<a onClick={() => navigate('/scorecard')}>查看全部</a>}
              loading={loadingOpps}
            >
              {opportunities.length === 0 ? (
                <EmptyState
                  title="还没有机会卡片"
                  description="从建立能力画像开始，或用趋势雷达扫描候选方向"
                  actionText="去趋势雷达"
                  onAction={() => navigate('/trends')}
                />
              ) : (
                <>
                  <RankingBars
                    items={opportunities.map((o) => ({
                      id: o.id,
                      title: o.title,
                      total_score: o.total_score ?? 0,
                      rank: o.rank ?? 0,
                    }))}
                    onSelect={(id) => navigate(`/scorecard?opportunity=${id}`)}
                  />
                  <div style={{ marginTop: 16 }}>
                    <List
                      size="small"
                      dataSource={opportunities.slice(0, 5)}
                      renderItem={(o: Opportunity) => (
                        <List.Item
                          actions={[
                            <a
                              key="d"
                              onClick={() => navigate(`/scorecard?opportunity=${o.id}`)}
                            >
                              详情
                            </a>,
                          ]}
                        >
                          <List.Item.Meta
                            title={
                              <Space>
                                <span>{o.title}</span>
                                {o.verdict ? (
                                  <Tag
                                    color={o.verdict.code === 'go' ? 'success' : 'processing'}
                                    className="tc-tag-soft"
                                  >
                                    {o.verdict.label}
                                  </Tag>
                                ) : (
                                  <Tag className="tc-tag-soft">未分析</Tag>
                                )}
                              </Space>
                            }
                            description={
                              <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                                证据 {o.evidence_count ?? 0} 条 · 风险 {o.risk_count ?? 0} 项
                              </Typography.Text>
                            }
                          />
                          <Typography.Text strong style={{ color: '#2563EB' }}>
                            {(o.total_score ?? 0).toFixed(2)}
                          </Typography.Text>
                        </List.Item>
                      )}
                    />
                  </div>
                </>
              )}
            </Card>
          </Col>

          <Col xs={24} lg={10}>
            <Card
              title="预警通知"
              size="small"
              extra={<a onClick={() => navigate('/monitor')}>查看全部</a>}
              style={{ marginBottom: 16 }}
            >
              {pendingAlerts.length === 0 ? (
                <EmptyState title="暂无预警" description="系统正在监控中" />
              ) : (
                <List
                  size="small"
                  dataSource={pendingAlerts.slice(0, 4)}
                  renderItem={(a) => {
                    const meta = LEVEL_META[a.level] ?? LEVEL_META.info
                    return (
                      <List.Item>
                        <List.Item.Meta
                          avatar={<Tag color={meta.color} className="tc-tag-soft">{meta.label}</Tag>}
                          title={<span style={{ fontSize: 13 }}>{a.title}</span>}
                          description={
                            <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                              {a.summary?.slice(0, 60)}
                            </Typography.Text>
                          }
                        />
                      </List.Item>
                    )
                  }}
                />
              )}
            </Card>

            <Card title="最新趋势" size="small">
              {!trends?.trends?.length ? (
                <EmptyState title="尚未扫描" description="点击右上角「开始趋势扫描」" />
              ) : (
                <List
                  size="small"
                  dataSource={trends.trends.slice(0, 5)}
                  renderItem={(t) => (
                    <List.Item
                      actions={[
                        <Tag
                          key="dir"
                          color={t.direction === 'tailwind' ? 'success' : 'warning'}
                          className="tc-tag-soft"
                        >
                          {t.direction === 'tailwind' ? '顺风 ▲' : '逆风 ▼'}
                        </Tag>,
                      ]}
                    >
                      <List.Item.Meta
                        title={<span style={{ fontSize: 13 }}>{t.name}</span>}
                        description={
                          <Typography.Text className="tc-mono tc-muted">
                            置信度 {t.confidence.toFixed(2)} · 证据 {t.evidence?.length ?? 0} 条
                          </Typography.Text>
                        }
                      />
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </Col>
        </Row>
      ) : (
        <EngineStatusPanel />
      )}
    </>
  )
}

function EngineStatusPanel() {
  const { data: workflow, loading } = useAsync(() => metaApi.workflow(), [])
  const { data: health } = useAsync(() => metaApi.health(), [])

  if (loading) return <Spin />

  const llm = health?.llm
  const usingRealLLM = Boolean(llm?.remote_ready) && llm?.provider !== 'rule'

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24}>
        <Card title="当前分析引擎" size="small" style={{ marginBottom: 16 }}>
          <Row gutter={[16, 12]}>
            <Col xs={24} md={10}>
              <Alert
                type={usingRealLLM ? 'success' : 'warning'}
                showIcon
                message={
                  usingRealLLM
                    ? `已接入真实大模型：${llm?.provider}`
                    : '未接入大模型 —— 智能体运行在确定性规则引擎上'
                }
                description={
                  usingRealLLM
                    ? '七个 Agent 的推理由大模型完成，rule() 实现降级为校验基线。'
                    : '智能体编排真实运行，但「大脑」是算法而非大模型。配置 LLM_PROVIDER 后即切换，无需改代码。'
                }
              />
            </Col>
            <Col xs={24} md={14}>
              <Descriptions column={2} size="small" bordered>
                <Descriptions.Item label="LLM Provider">
                  <Tag color={usingRealLLM ? 'success' : 'default'} className="tc-tag-soft">
                    {llm?.provider ?? '—'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="远端就绪">
                  {llm?.remote_ready ? '是' : '否'}
                </Descriptions.Item>
                <Descriptions.Item label="简单任务">
                  {llm?.routes?.simple ?? '—'}
                </Descriptions.Item>
                <Descriptions.Item label="复杂分析">
                  {llm?.routes?.complex ?? '—'}
                </Descriptions.Item>
                <Descriptions.Item label="向量库">
                  {health?.vector_store?.backend ?? '—'}（{health?.rag_documents ?? 0} 篇）
                </Descriptions.Item>
                <Descriptions.Item label="数据库">
                  {health?.database?.backend ?? '—'}
                </Descriptions.Item>
              </Descriptions>
            </Col>
          </Row>
        </Card>
      </Col>

      <Col xs={24} lg={14}>
        <Card title="七 Agent 编排图" size="small">
          <Timeline
            items={(workflow?.layers ?? []).map((layer) => ({
              color: layer.parallel ? 'blue' : 'gray',
              children: (
                <Space direction="vertical" size={2}>
                  <Typography.Text strong style={{ fontSize: 13 }}>
                    第 {layer.layer} 层 {layer.parallel ? '（并行执行）' : '（串行）'}
                  </Typography.Text>
                  <Space wrap size={6}>
                    {layer.agents.map((a) => (
                      <Tag key={a.key} color="blue" className="tc-tag-soft">
                        {a.name}
                      </Tag>
                    ))}
                  </Space>
                </Space>
              ),
            }))}
          />
          <Alert
            type="info"
            showIcon
            message="反馈循环"
            description={workflow?.feedback_loop}
            style={{ marginTop: 8 }}
          />
        </Card>
      </Col>
      <Col xs={24} lg={10}>
        <Card title="运行时状态" size="small">
          <Collapse
            ghost
            items={[
              {
                key: 'health',
                label: '健康检查',
                children: (
                  <Typography.Paragraph className="tc-mono" style={{ fontSize: 12 }}>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
                      {JSON.stringify(health, null, 2)}
                    </pre>
                  </Typography.Paragraph>
                ),
              },
            ]}
          />
        </Card>
      </Col>
    </Row>
  )
}
