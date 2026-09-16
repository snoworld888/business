import { useEffect, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { DatabaseOutlined, CloudDownloadOutlined, PlusOutlined, ReloadOutlined, TeamOutlined } from '@ant-design/icons'
import PageHead from '../components/PageHead'
import { adminApi, metaApi, signalApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import { runTask, useTask, useTaskVersion } from '../api/taskStore'

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  strategy: '战略部',
  product: '产品部',
  investment: '投资部',
  expert: '领域专家',
}

/** P11 设置（《原型设计》§5.11） */
/** 从客户端推导响应类型，避免手写重复的下游契约 */
type CollectReport = Awaited<ReturnType<typeof signalApi.collect>>
type ReindexResult = Awaited<ReturnType<typeof signalApi.reindex>>

export default function Settings() {
  const [inviteOpen, setInviteOpen] = useState(false)
  const [form] = Form.useForm()
  /** 采集 / 重建索引都跑在应用级任务注册表里：切页不中断，回来仍能认领结果 */
  const collectTask = useTask<CollectReport>('signals:collect')
  const collecting = collectTask?.status === 'running'
  const reindexTask = useTask<ReindexResult>('settings:reindex')
  const reindexing = reindexTask?.status === 'running'
  const taskVersion = useTaskVersion()

  const { data: users, refresh: refreshUsers } = useAsync(() => adminApi.users(), [])
  const { data: roles } = useAsync(() => adminApi.roles(), [])
  const { data: sources, refresh: refreshSources } = useAsync(() => signalApi.sources(), [taskVersion])
  const { data: adapters, refresh: refreshAdapters } = useAsync(() => signalApi.adapters(), [taskVersion])
  const { data: model } = useAsync(() => metaApi.modelConfig(), [])
  const { data: logs, refresh: refreshLogs } = useAsync(() => adminApi.auditLogs(100), [])

  /**
   * 触发一轮真实采集（《技术文档》§3.1）。完整报告见「信号库」页。
   *
   * ⚠️ 与「信号库」页**共用同一个任务键** —— 两处点的是同一件事，
   * 共用后既不会并发跑两遍，也能看到对方发起的采集正在进行中。
   */
  const collect = async () => {
    await runTask<CollectReport>(
      'signals:collect',
      { page: '信号采集', title: '采集信号', params: { source: '设置页' } },
      () => signalApi.collect({ limit_per_source: 10 }),
      {
        summarize: (r) =>
          `新增 ${r.total_inserted} 条，去重 ${r.total_duplicated} 条（${r.elapsed_ms} ms）`,
      },
    )
  }

  /** 认领采集结果（本页不弹报告，只刷新数据源统计；报告在「信号库」页看） */
  useEffect(() => {
    if (!collectTask || collectTask.status !== 'done' || !collectTask.result) return
    refreshSources()
    refreshAdapters()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collectTask?.finishedAt, collectTask?.status])

  const invite = async () => {
    const values = await form.validateFields()
    const res = await adminApi.createUser(values)
    message.success(
      `用户已创建${res.initial_password ? `，初始密码 ${res.initial_password}` : ''}`,
    )
    setInviteOpen(false)
    form.resetFields()
    await refreshUsers()
  }

  /** 索引重建较慢（要重新灌向量库 + 重建图谱），同样走后台任务 */
  const reindex = async () => {
    await runTask<ReindexResult>(
      'settings:reindex',
      { page: '设置', title: '重建索引' },
      () => signalApi.reindex(),
      {
        summarize: (r) =>
          `${r.indexed_documents} 篇文档，图谱 ${r.graph.nodes} 节点 / ${r.graph.edges} 边`,
      },
    )
  }

  useEffect(() => {
    if (!reindexTask || reindexTask.status !== 'done' || !reindexTask.result) return
    void Promise.all([refreshSources(), refreshLogs()])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reindexTask?.finishedAt, reindexTask?.status])

  const tabItems = [
    {
      key: 'users',
      label: (
        <Space size={6}>
          <TeamOutlined />
          用户与权限
        </Space>
      ),
      children: (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card
              title="用户"
              size="small"
              extra={
                <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setInviteOpen(true)}>
                  邀请用户
                </Button>
              }
            >
              <Table
                size="small"
                pagination={false}
                rowKey="id"
                dataSource={users?.users ?? []}
                columns={[
                  { title: '用户', dataIndex: 'name', render: (v, r) => v || r.email },
                  { title: '邮箱', dataIndex: 'email', ellipsis: true },
                  {
                    title: '角色',
                    dataIndex: 'role',
                    width: 100,
                    render: (v: string) => (
                      <Tag color={v === 'admin' ? 'blue' : 'default'} className="tc-tag-soft">
                        {ROLE_LABEL[v] ?? v}
                      </Tag>
                    ),
                  },
                  {
                    title: '状态',
                    dataIndex: 'is_active',
                    width: 80,
                    render: (v: boolean) => (
                      <Badge status={v ? 'success' : 'default'} text={v ? '活跃' : '禁用'} />
                    ),
                  },
                ]}
              />
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title="角色权限矩阵" size="small">
              <Table
                size="small"
                pagination={false}
                rowKey="role"
                scroll={{ x: 520 }}
                dataSource={(roles?.matrix ?? []).map((r) => ({
                  key: r.role,
                  role: r.role,
                  ...Object.fromEntries(
                    (roles?.all_permissions ?? []).map((p) => [p, r.permissions.includes(p)]),
                  ),
                }))}
                columns={[
                  {
                    title: '角色',
                    dataIndex: 'role',
                    fixed: 'left',
                    width: 100,
                    render: (v: string) => ROLE_LABEL[v] ?? v,
                  },
                  ...(roles?.all_permissions ?? []).map((p) => ({
                    title: p.replace(/[:.]/g, '·'),
                    dataIndex: p,
                    width: 120,
                    render: (v: boolean) => (v ? '✓' : '✗'),
                  })),
                ]}
              />
            </Card>
          </Col>
        </Row>
      ),
    },
    {
      key: 'sources',
      label: (
        <Space size={6}>
          <DatabaseOutlined />
          数据源
        </Space>
      ),
      children: (
        <Card
          size="small"
          title={`数据源连接状态（${sources?.connected ?? 0} / ${sources?.mapped_sources ?? 0} 已连接）`}
          extra={
            <Space>
              <Button
                size="small"
                type="primary"
                ghost
                icon={<CloudDownloadOutlined />}
                loading={collecting}
                onClick={collect}
              >
                立即采集
              </Button>
              <Button size="small" icon={<ReloadOutlined />} loading={reindexing} onClick={reindex}>
                重建索引与图谱
              </Button>
            </Space>
          }
        >
          {adapters && !adapters.coverage_ok ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="可用数据源不足"
              description={`《设计文档》§7.2 要求覆盖至少 5 个数据源，当前可采集 ${adapters.available} 个（共 ${adapters.total} 个适配器）。`}
            />
          ) : (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 12 }}
              message="数据源覆盖达标"
              description={`可采集 ${adapters?.available ?? 0} / ${adapters?.total ?? 0} 个数据源，其中默认启用 ${adapters?.default_enabled ?? 0} 个；下方为「已有数据」的分布。`}
            />
          )}
          <Table
            size="small"
            pagination={false}
            rowKey="key"
            dataSource={sources?.sources ?? []}
            columns={[
              { title: '数据源', dataIndex: 'name' },
              { title: '类型', dataIndex: 'key', width: 100 },
              {
                title: '状态',
                dataIndex: 'connected',
                width: 110,
                render: (v: boolean) => (
                  <Badge status={v ? 'success' : 'default'} text={v ? '已连接' : '未连接'} />
                ),
              },
              { title: '信号数', dataIndex: 'signal_count', width: 90 },
              {
                title: '最后同步',
                dataIndex: 'last_sync',
                width: 180,
                render: (v?: string) =>
                  v ? <span className="tc-mono">{v.slice(0, 16).replace('T', ' ')}</span> : '—',
              },
            ]}
          />
        </Card>
      ),
    },
    {
      key: 'model',
      label: '模型配置',
      children: (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card title="LLM 多模型路由" size="small">
              <Table
                size="small"
                pagination={false}
                rowKey="task"
                dataSource={model?.routes ?? []}
                columns={[
                  { title: '任务类型', dataIndex: 'task' },
                  { title: '模型', dataIndex: 'model' },
                ]}
              />
              <Descriptions column={1} size="small" style={{ marginTop: 12 }}>
                <Descriptions.Item label="Provider">
                  <Tag color="blue" className="tc-tag-soft">
                    {model?.llm?.provider}
                  </Tag>
                  {model?.llm?.remote_ready ? (
                    <Tag color="success" className="tc-tag-soft">
                      远端就绪
                    </Tag>
                  ) : (
                    <Tag color="warning" className="tc-tag-soft">
                      离线规则引擎
                    </Tag>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="缓存有效期">
                  {model?.cache_ttl_hours} 小时
                </Descriptions.Item>
                <Descriptions.Item label="Embedding">
                  {model?.embedding?.backend} / {model?.embedding?.model}（{model?.embedding?.dim} 维）
                </Descriptions.Item>
                <Descriptions.Item label="向量库">{model?.vector_backend}</Descriptions.Item>
                <Descriptions.Item label="图谱">{model?.graph_backend}</Descriptions.Item>
                <Descriptions.Item label="数据库">{model?.database}</Descriptions.Item>
              </Descriptions>
              <Alert
                style={{ marginTop: 12 }}
                type="info"
                showIcon
                message="切换真实模型"
                description="在 backend/.env 中设置 LLM_PROVIDER=openai（或 ollama）与 LLM_API_KEY，重启后端即可启用真实 LLM；Agent 的规则实现会自动降级为校验基线。"
              />
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title="适配器开关" size="small">
              <Alert
                type="info"
                showIcon
                message="重型组件以「开关 + 适配器」形式接入"
                description={
                  <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12 }}>
                    <li>VECTOR_BACKEND=milvus → 启用 Milvus 2.4（IVF_FLAT + COSINE）</li>
                    <li>GRAPH_BACKEND=neo4j → 启用 Neo4j 5（Cypher 查询）</li>
                    <li>DATABASE_URL=postgresql+psycopg://… → 切换 PostgreSQL 16</li>
                    <li>未配置时自动降级为零依赖本地实现，业务代码无需改动</li>
                  </ul>
                }
              />
            </Card>
          </Col>
        </Row>
      ),
    },
    {
      key: 'audit',
      label: '审计日志',
      children: (
        <Card
          size="small"
          title={`审计日志（保留 ${logs?.retention_days ?? 180} 天，共 ${logs?.total ?? 0} 条）`}
          extra={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => refreshLogs()}>
              刷新
            </Button>
          }
        >
          <Table
            size="small"
            rowKey="id"
            dataSource={logs?.logs ?? []}
            pagination={{ pageSize: 15 }}
            columns={[
              {
                title: '时间',
                dataIndex: 'created_at',
                width: 175,
                render: (v: string) => <span className="tc-mono">{v?.slice(0, 19).replace('T', ' ')}</span>,
              },
              { title: '动作', dataIndex: 'action', width: 210 },
              { title: '对象', dataIndex: 'target_type', width: 140 },
              {
                title: '详情',
                dataIndex: 'detail',
                ellipsis: true,
                render: (v: Record<string, unknown>) => (
                  <span className="tc-mono" style={{ fontSize: 11 }}>
                    {JSON.stringify(v)}
                  </span>
                ),
              },
            ]}
          />
        </Card>
      ),
    },
  ]

  return (
    <>
      <PageHead
        title="设置"
        desc="用户、角色权限、数据源、模型路由与审计日志。RBAC 与操作日志保留 180 天，满足私有化合规要求。"
      />
      <Tabs items={tabItems} />

      <Modal
        title="邀请用户"
        open={inviteOpen}
        onCancel={() => setInviteOpen(false)}
        onOk={invite}
        okText="创建"
      >
        <Form form={form} layout="vertical" initialValues={{ role: 'product' }}>
          <Form.Item label="邮箱" name="email" rules={[{ required: true, type: 'email' }]}>
            <Input placeholder="user@company.com" />
          </Form.Item>
          <Form.Item label="姓名" name="name">
            <Input placeholder="张三" />
          </Form.Item>
          <Form.Item label="角色" name="role" rules={[{ required: true }]}>
            <Select
              options={Object.entries(ROLE_LABEL).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
            未指定初始密码时，系统使用默认密码 TrackCompass@2026，请提醒用户首次登录后修改。
          </Typography.Text>
        </Form>
      </Modal>
    </>
  )
}
