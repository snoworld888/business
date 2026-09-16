import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  List,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { CheckOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons'
import PageHead from '../components/PageHead'
import { EmptyState } from '../components/widgets'
import { monitorApi, opportunityApi, scorecardApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import { runTask, useTask, useTaskVersion } from '../api/taskStore'
import type { Alert as AlertModel, Replay } from '../api/types'

const LEVEL_META: Record<string, { color: string; label: string }> = {
  critical: { color: 'error', label: '严重' },
  warning: { color: 'warning', label: '警告' },
  info: { color: 'blue', label: '提示' },
}

/** P10 监控复盘（《原型设计》§5.10） */
/** 预警扫描的响应类型 —— 从客户端推导，避免手写重复的下游契约 */
type ScanResult = Awaited<ReturnType<typeof monitorApi.scan>>

export default function Monitor() {
  const [tab, setTab] = useState<'alerts' | 'replay' | 'calibration'>('alerts')
  const [statusFilter, setStatusFilter] = useState<'pending' | 'handled' | 'ignored'>('pending')
  const [replayTarget, setReplayTarget] = useState<string>()
  const [replayForm] = Form.useForm()
  /** 预警扫描任务（与按钮联动；任务在应用级注册表里跑，切页不中断） */
  const scanTask = useTask<ScanResult>('monitor:scan')
  const scanning = scanTask?.status === 'running'
  const taskVersion = useTaskVersion()

  const { data: alerts, loading, refresh } = useAsync(() => monitorApi.list(), [taskVersion])
  const { data: replays, refresh: refreshReplays } = useAsync(
    async () => (await monitorApi.replays()).replays,
    [],
  )
  const { data: calibration, refresh: refreshCalibration } = useAsync(
    () => scorecardApi.calibrationPreview(),
    [],
  )
  const { data: card, refresh: refreshCard } = useAsync(() => scorecardApi.default(false), [])
  const { data: versions, refresh: refreshVersions } = useAsync(
    async () => (card ? scorecardApi.versions(card.id) : Promise.resolve(null)),
    [card?.id],
  )
  const { data: opportunities } = useAsync(
    async () => (await opportunityApi.list({ limit: 50 })).opportunities,
    [],
  )

  const grouped = alerts?.grouped?.[statusFilter] ?? []

  const act = async (a: AlertModel, action: 'handle' | 'ignore' | 'reopen') => {
    await monitorApi.action(a.id, action, action === 'ignore' ? '人工判定无需处理' : '')
    message.success(`预警已${action === 'handle' ? '标记处理' : action === 'ignore' ? '忽略' : '重开'}`)
    await refresh()
  }

  /** 预警扫描跑在应用级任务注册表里：切页不中断，回来仍能认领结果 */
  const scan = async () => {
    await runTask<ScanResult>(
      'monitor:scan',
      { page: '监控复盘', title: '扫描预警' },
      () => monitorApi.scan(),
      { summarize: (r) => `扫描 ${r.scanned_signals} 条信号，新增 ${r.new_alerts} 条预警` },
    )
  }

  /** 认领扫描结果（本页完成 / 离开期间完成，同一条路径） */
  useEffect(() => {
    if (!scanTask || scanTask.status !== 'done' || !scanTask.result) return
    void Promise.all([refresh(), refreshCalibration()])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanTask?.finishedAt, scanTask?.status])

  const submitReplay = async () => {
    const values = await replayForm.validateFields()
    let outcome: Record<string, unknown> = {}
    try {
      outcome = values.outcome ? JSON.parse(values.outcome) : {}
    } catch {
      message.error('实际结果需为合法 JSON')
      return
    }
    const res = await monitorApi.replay({
      decision_id: values.decision_id,
      outcome,
      review: values.review,
    })
    message.success(`复盘 ${res.replay.id} 已记录，产生 ${res.replay.calibration_suggestions.length} 条校准建议`)
    setReplayTarget(undefined)
    replayForm.resetFields()
    await Promise.all([refreshReplays(), refreshCalibration()])
  }

  const applyReplay = async (r: Replay) => {
    const res = await monitorApi.applyReplay(r.id)
    message.success(`已应用校准，评分卡升级至 ${res.version}，重算 ${res.recalculated} 个机会`)
    await Promise.all([refreshCard(), refreshVersions(), refreshCalibration()])
  }

  const applyCalibration = async () => {
    const res = await scorecardApi.applyCalibration('根据复盘结果校准')
    message.success(`校准已应用，评分卡升级至 ${res.version}，重算 ${res.recalculated} 个机会`)
    await Promise.all([refreshCard(), refreshVersions(), refreshCalibration()])
  }

  return (
    <>
      <PageHead
        title="监控复盘"
        desc="关键信号变化触发预警，记录决策依据并事后复盘，校准建议可一键应用到评分模型。"
        extra={
          <Button icon={<SyncOutlined />} loading={scanning} onClick={scan}>
            重新扫描信号
          </Button>
        }
      />

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} lg={6}>
          <Card size="small">
            <Statistic
              title="严重预警"
              value={alerts?.severity?.critical ?? 0}
              valueStyle={{ color: '#EF4444', fontSize: 22 }}
            />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small">
            <Statistic
              title="警告"
              value={alerts?.severity?.warning ?? 0}
              valueStyle={{ color: '#F59E0B', fontSize: 22 }}
            />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small">
            <Statistic
              title="提示"
              value={alerts?.severity?.info ?? 0}
              valueStyle={{ color: '#2563EB', fontSize: 22 }}
            />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card size="small">
            <Statistic
              title="复盘记录"
              value={replays?.length ?? 0}
              valueStyle={{ fontSize: 22 }}
            />
          </Card>
        </Col>
      </Row>

      <Segmented
        value={tab}
        onChange={(v) => setTab(v as typeof tab)}
        options={[
          { label: '预警列表', value: 'alerts' },
          { label: '复盘看板', value: 'replay' },
          { label: '模型校准', value: 'calibration' },
        ]}
        style={{ marginBottom: 16 }}
      />

      {tab === 'alerts' ? (
        <Card
          size="small"
          title={
            <Segmented
              size="small"
              value={statusFilter}
              onChange={(v) => setStatusFilter(v as typeof statusFilter)}
              options={[
                { label: `未处理 ${alerts?.grouped?.pending?.length ?? 0}`, value: 'pending' },
                { label: `已处理 ${alerts?.grouped?.handled?.length ?? 0}`, value: 'handled' },
                { label: `已忽略 ${alerts?.grouped?.ignored?.length ?? 0}`, value: 'ignored' },
              ]}
            />
          }
        >
          <Spin spinning={loading}>
            {grouped.length === 0 ? (
              <EmptyState
                title="暂无预警"
                description="系统正在监控政策、融资、技术突破、需求激增等关键信号"
                actionText="立即扫描"
                onAction={scan}
              />
            ) : (
              <List
                dataSource={grouped}
                renderItem={(a) => {
                  const meta = LEVEL_META[a.level] ?? LEVEL_META.info
                  return (
                    <List.Item
                      actions={[
                        a.status === 'pending' ? (
                          <a key="h" onClick={() => act(a, 'handle')}>
                            标记已处理
                          </a>
                        ) : (
                          <a key="h" onClick={() => act(a, 'reopen')}>
                            重开
                          </a>
                        ),
                        a.status === 'pending' ? (
                          <a key="i" onClick={() => act(a, 'ignore')}>
                            忽略
                          </a>
                        ) : null,
                        <a
                          key="r"
                          onClick={() => {
                            setReplayTarget(a.opportunity_id ?? a.id)
                            setTab('replay')
                            replayForm.setFieldsValue({
                              decision_id: a.opportunity_id ?? undefined,
                              review: '',
                            })
                          }}
                        >
                          复盘
                        </a>,
                      ].filter(Boolean)}
                    >
                      <List.Item.Meta
                        avatar={
                          <Tag color={meta.color} className="tc-tag-soft">
                            {meta.label}
                          </Tag>
                        }
                        title={<span style={{ fontSize: 13 }}>{a.title}</span>}
                        description={
                          <Space direction="vertical" size={2}>
                            <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                              {a.summary}
                            </Typography.Text>
                            <Space size={8} wrap>
                              <Tag className="tc-tag-soft">规则：{a.rule_name}</Tag>
                              {a.track ? <Tag className="tc-tag-soft">影响赛道：{a.track}</Tag> : null}
                              <Typography.Text className="tc-mono tc-muted">
                                {a.created_at?.slice(0, 16).replace('T', ' ')}
                              </Typography.Text>
                            </Space>
                            {a.advice ? (
                              <Alert
                                type="info"
                                showIcon
                                message={<span style={{ fontSize: 12 }}>{a.advice}</span>}
                              />
                            ) : null}
                          </Space>
                        }
                      />
                    </List.Item>
                  )
                }}
              />
            )}
          </Spin>
        </Card>
      ) : null}

      {tab === 'replay' ? (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={14}>
            <Card title="复盘看板" size="small">
              {(replays ?? []).length === 0 ? (
                <EmptyState
                  title="暂无复盘记录"
                  description="从预警列表点击「复盘」，或直接填写下方表单记录一次决策复盘"
                />
              ) : (
                <List
                  dataSource={replays ?? []}
                  renderItem={(r) => (
                    <Card size="small" style={{ marginBottom: 12 }} key={r.id}>
                      <Descriptions column={2} size="small">
                        <Descriptions.Item label="编号">{r.id}</Descriptions.Item>
                        <Descriptions.Item label="决策">{r.title}</Descriptions.Item>
                        <Descriptions.Item label="决策依据" span={2}>
                          <span className="tc-mono" style={{ fontSize: 12 }}>
                            {JSON.stringify(r.basis)}
                          </span>
                        </Descriptions.Item>
                        <Descriptions.Item label="实际结果" span={2}>
                          <span className="tc-mono" style={{ fontSize: 12 }}>
                            {JSON.stringify(r.outcome)}
                          </span>
                        </Descriptions.Item>
                        <Descriptions.Item label="复盘结论" span={2}>
                          {r.review || '—'}
                        </Descriptions.Item>
                      </Descriptions>
                      {r.calibration_suggestions.length ? (
                        <>
                          <Divider style={{ margin: '8px 0' }} />
                          <Typography.Text strong style={{ fontSize: 13 }}>
                            校准建议
                          </Typography.Text>
                          <List
                            size="small"
                            dataSource={r.calibration_suggestions}
                            renderItem={(s) => (
                              <List.Item>
                                <Space>
                                  <Tag color={s.delta > 0 ? 'success' : 'warning'} className="tc-tag-soft">
                                    {s.dimension} {s.from} → {s.to}
                                  </Tag>
                                  <Typography.Text style={{ fontSize: 12 }}>{s.reason}</Typography.Text>
                                </Space>
                              </List.Item>
                            )}
                          />
                          {!r.applied ? (
                            <Button
                              size="small"
                              type="primary"
                              icon={<CheckOutlined />}
                              onClick={() => applyReplay(r)}
                              disabled={!r.calibration_suggestions.length}
                            >
                              应用校准
                            </Button>
                          ) : (
                            <Tag color="success" className="tc-tag-soft">
                              已应用
                            </Tag>
                          )}
                        </>
                      ) : null}
                    </Card>
                  )}
                />
              )}
            </Card>
          </Col>

          <Col xs={24} lg={10}>
            <Card title="记录一次复盘" size="small">
              <Form form={replayForm} layout="vertical">
                <Form.Item
                  label="关联决策（机会卡片）"
                  name="decision_id"
                  rules={[{ required: true, message: '请选择关联的机会卡片' }]}
                >
                  <Select
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择机会卡片"
                    options={(opportunities ?? []).map((o) => ({ value: o.id, label: o.title }))}
                  />
                </Form.Item>
                <Form.Item label="实际结果（JSON）" name="outcome">
                  <Input.TextArea rows={3} placeholder='{"signup_rate": 0.06}' />
                </Form.Item>
                <Form.Item label="复盘结论" name="review">
                  <Input.TextArea rows={3} placeholder="例如：需求验证充分，但竞争壁垒被高估" />
                </Form.Item>
                <Button type="primary" block onClick={submitReplay}>
                  记录复盘并生成校准建议
                </Button>
              </Form>
              {replayTarget ? (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginTop: 12 }}
                  message="已从预警带入关联决策"
                />
              ) : null}
            </Card>
          </Col>
        </Row>
      ) : null}

      {tab === 'calibration' ? (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={14}>
            <Card
              title="校准建议"
              size="small"
              extra={
                <Button
                  size="small"
                  type="primary"
                  icon={<CheckOutlined />}
                  disabled={!calibration?.suggestions?.length}
                  onClick={applyCalibration}
                >
                  一键应用校准
                </Button>
              }
            >
              {!calibration?.suggestions?.length ? (
                <Alert type="success" showIcon message="当前无校准建议" description="监控 Agent 未检测到需要调整权重的信号组合" />
              ) : (
                <Table
                  size="small"
                  pagination={false}
                  rowKey="key"
                  dataSource={calibration.suggestions.map((s) => ({ ...s, key: s.dimension }))}
                  columns={[
                    { title: '维度', dataIndex: 'dimension', width: 130 },
                    {
                      title: '权重变化',
                      width: 160,
                      render: (_, r) => (
                        <Space size={4}>
                          <Tag className="tc-tag-soft">{(r.from * 100).toFixed(0)}%</Tag>
                          <span className="tc-muted">→</span>
                          <Tag color={r.delta > 0 ? 'success' : 'warning'} className="tc-tag-soft">
                            {(r.to * 100).toFixed(0)}%
                          </Tag>
                        </Space>
                      ),
                    },
                    { title: '理由', dataIndex: 'reason' },
                  ]}
                />
              )}
            </Card>

            <Card title="版本对比" size="small" style={{ marginTop: 16 }}>
              {!versions?.comparison?.length ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无版本差异" />
              ) : (
                <List
                  size="small"
                  dataSource={versions.comparison}
                  renderItem={(c) => (
                    <List.Item>
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Space>
                          <Tag color="blue" className="tc-tag-soft">
                            {c.from_version} → {c.to_version}
                          </Tag>
                          <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                            {c.reason}
                          </Typography.Text>
                        </Space>
                        <Space wrap size={6}>
                          {c.changes.map((ch: Record<string, unknown>) => (
                            <Tag key={String(ch.dimension)} className="tc-tag-soft">
                              {String(ch.dimension)}: {String(ch.from)} → {String(ch.to)}
                            </Tag>
                          ))}
                        </Space>
                      </Space>
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </Col>

          <Col xs={24} lg={10}>
            <Card
              title="当前评分卡"
              size="small"
              extra={
                <Space>
                  <Tag color="blue" className="tc-tag-soft">
                    {card?.version}
                  </Tag>
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => refreshVersions()}>
                    刷新
                  </Button>
                </Space>
              }
            >
              <Table
                size="small"
                pagination={false}
                rowKey="key"
                dataSource={(card?.dimensions ?? []).map((d) => ({
                  key: d.key,
                  name: d.name,
                  current: (card?.weights?.[d.key] ?? 0) * 100,
                  suggested: (calibration?.current_weights?.[d.key] ?? 0) * 100,
                  next: (calibration?.weights?.[d.key] ?? 0) * 100,
                }))}
                columns={[
                  { title: '维度', dataIndex: 'name' },
                  {
                    title: '当前',
                    dataIndex: 'current',
                    width: 70,
                    render: (v: number) => `${v.toFixed(0)}%`,
                  },
                  {
                    title: '校准后',
                    dataIndex: 'next',
                    width: 80,
                    render: (v: number, r) => (
                      <Typography.Text
                        strong={Math.abs(v - r.current) > 0.5}
                        type={v > r.current ? 'success' : v < r.current ? 'warning' : undefined}
                      >
                        {v.toFixed(0)}%
                      </Typography.Text>
                    ),
                  },
                ]}
              />
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message="模型校准越用越准"
                description="每次复盘产生的校准建议都会沉淀为新版本，历史版本可对比追溯。"
              />
            </Card>
          </Col>
        </Row>
      ) : null}
    </>
  )
}
