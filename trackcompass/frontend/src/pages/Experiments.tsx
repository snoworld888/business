import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlusOutlined, UploadOutlined } from '@ant-design/icons'
import { useSearchParams } from 'react-router-dom'
import PageHead from '../components/PageHead'
import { EmptyState, MetricBar } from '../components/widgets'
import { experimentApi, opportunityApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import type { Experiment } from '../api/types'

const STATUS_META: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: '草稿' },
  running: { color: 'processing', label: '进行中' },
  completed: { color: 'success', label: '已完成' },
  archived: { color: 'default', label: '已归档' },
  cancelled: { color: 'error', label: '已取消' },
}

const METHOD_OPTIONS = [
  { value: 'landing_page', label: '落地页测试' },
  { value: 'interview', label: '客户访谈' },
  { value: 'survey', label: '问卷调研' },
  { value: 'poc', label: 'POC 概念验证' },
  { value: 'presale', label: '预售测试' },
  { value: 'channel_test', label: '渠道测试' },
]

/** P08 验证实验（《原型设计》§5.8） */
export default function Experiments() {
  const [params] = useSearchParams()
  const [filter, setFilter] = useState<'all' | 'running' | 'completed' | 'draft'>('all')
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<Experiment | null>(null)
  const [form] = Form.useForm()

  const { data, loading, refresh } = useAsync(() => experimentApi.list(), [])
  const { data: opportunities } = useAsync(
    async () => (await opportunityApi.list({ limit: 50 })).opportunities,
    [],
  )
  const { data: detail, refresh: refreshDetail } = useAsync(
    () => (selected ? experimentApi.detail(selected.id) : Promise.resolve(null)),
    [selected?.id],
  )

  useEffect(() => {
    const method = params.get('method')
    const hypothesis = params.get('hypothesis')
    if (method) {
      form.setFieldsValue({ method, hypothesis: hypothesis ?? '' })
      setOpen(true)
    }
  }, [params, form])

  const experiments = data?.experiments ?? []
  const filtered = filter === 'all' ? experiments : experiments.filter((e) => e.status === filter)

  const create = async () => {
    const values = await form.validateFields()
    const res = await experimentApi.create(values)
    message.success(`实验已创建：${res.method_name}`)
    setOpen(false)
    form.resetFields()
    await refresh()
    const created = (await experimentApi.list()).experiments.find((e) => e.id === res.id)
    if (created) setSelected(created)
  }

  const transition = async (exp: Experiment, status: string) => {
    try {
      await experimentApi.update(exp.id, { status })
      message.success(`状态已更新为 ${STATUS_META[status]?.label ?? status}`)
      await Promise.all([refresh(), refreshDetail()])
    } catch {
      /* 错误提示由拦截器统一处理 */
    }
  }

  const writeback = async (exp: Experiment) => {
    const res = await experimentApi.writeback(exp.id)
    message.success(
      `已回写：达成率 ${(res.achievement_rate * 100).toFixed(0)}%，机会状态 ${res.opportunity_status}，新评分 ${res.new_total_score}`,
    )
    await Promise.all([refresh(), refreshDetail()])
  }

  const exportCsv = async (exp: Experiment) => {
    const res = await experimentApi.exportCsv(exp.id)
    const blob = new Blob([`\ufeff${res.content}`], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = res.filename
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <>
      <PageHead
        title="验证实验"
        desc="管理「假设 → 实验 → 指标 → 结论」闭环，支持落地页、访谈、问卷、POC、预售、渠道测试 6 种方法，结论可回写赛道评分。"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新建实验
          </Button>
        }
      />

      <Segmented
        value={filter}
        onChange={(v) => setFilter(v as typeof filter)}
        options={[
          { label: `全部 ${experiments.length}`, value: 'all' },
          { label: `进行中 ${experiments.filter((e) => e.status === 'running').length}`, value: 'running' },
          { label: `已完成 ${experiments.filter((e) => e.status === 'completed').length}`, value: 'completed' },
          { label: `草稿 ${experiments.filter((e) => e.status === 'draft').length}`, value: 'draft' },
        ]}
        style={{ marginBottom: 16 }}
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={13}>
          <Spin spinning={loading}>
            {filtered.length === 0 ? (
              <Card>
                <EmptyState
                  title="暂无实验"
                  description="从需求探针的建议实验创建，或点击右上角新建"
                  actionText="新建实验"
                  onAction={() => setOpen(true)}
                />
              </Card>
            ) : (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                {filtered.map((exp) => {
                  const meta = STATUS_META[exp.status] ?? STATUS_META.draft
                  return (
                    <Card
                      key={exp.id}
                      size="small"
                      hoverable
                      onClick={() => setSelected(exp)}
                      title={
                        <Space>
                          <span>{exp.title || exp.method_name}</span>
                          <Tag color={meta.color} className="tc-tag-soft">
                            {meta.label}
                          </Tag>
                          {exp.applied_to_score ? (
                            <Tag color="blue" className="tc-tag-soft">
                              已回写评分
                            </Tag>
                          ) : null}
                        </Space>
                      }
                    >
                      <Space direction="vertical" size={6} style={{ width: '100%' }}>
                        <Typography.Text style={{ fontSize: 13 }}>
                          假设：{exp.hypothesis}
                        </Typography.Text>
                        {exp.conclusion ? (
                          <Typography.Text type="success" style={{ fontSize: 12 }}>
                            结论：{exp.conclusion}
                          </Typography.Text>
                        ) : null}
                        <Space wrap size={6}>
                          {Object.entries(exp.metrics ?? {}).slice(0, 3).map(([k, v]) => (
                            <Tag key={k} className="tc-tag-soft">
                              {k}: {String(v.current ?? '—')} / {String(v.target ?? '—')}
                              {v.unit ?? ''}
                            </Tag>
                          ))}
                        </Space>
                        <Space size={8} onClick={(e) => e.stopPropagation()}>
                          <a onClick={() => setSelected(exp)}>查看详情</a>
                          {exp.status === 'running' ? (
                            <a onClick={() => transition(exp, 'completed')}>标记完成</a>
                          ) : null}
                          {exp.status === 'completed' && !exp.applied_to_score ? (
                            <a onClick={() => writeback(exp)}>回写评分</a>
                          ) : null}
                          <a onClick={() => exportCsv(exp)}>导出 CSV</a>
                        </Space>
                      </Space>
                    </Card>
                  )
                })}
              </Space>
            )}
          </Spin>
        </Col>

        <Col xs={24} lg={11}>
          {detail ? (
            <Card
              title={`实验详情：${detail.title || detail.method_name}`}
              size="small"
              extra={<Tag color={STATUS_META[detail.status]?.color} className="tc-tag-soft">
                {STATUS_META[detail.status]?.label}
              </Tag>}
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="方法">{detail.method_name}</Descriptions.Item>
                <Descriptions.Item label="假设">{detail.hypothesis}</Descriptions.Item>
                <Descriptions.Item label="结论">{detail.conclusion || '进行中'}</Descriptions.Item>
                <Descriptions.Item label="进度">
                  {detail.progress.toFixed(1)}%
                </Descriptions.Item>
              </Descriptions>
              <Typography.Text strong style={{ fontSize: 13 }}>
                指标看板
              </Typography.Text>
              <div style={{ marginTop: 10 }}>
                {(detail.progress_bars ?? []).length ? (
                  detail.progress_bars!.map((b) => (
                    <MetricBar
                      key={b.metric}
                      label={b.metric}
                      current={b.current}
                      target={b.target}
                      unit={b.unit}
                      achievement={b.achievement}
                    />
                  ))
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无指标" />
                )}
              </div>

              <Space wrap style={{ marginTop: 12 }}>
                {(detail.allowed_transitions ?? []).map((s) => (
                  <Button key={s} size="small" onClick={() => transition(detail, s)}>
                    转为 {STATUS_META[s]?.label ?? s}
                  </Button>
                ))}
                {detail.status === 'completed' && !detail.applied_to_score ? (
                  <Button size="small" type="primary" onClick={() => writeback(detail)}>
                    回写赛道评分
                  </Button>
                ) : null}
                <Button size="small" icon={<UploadOutlined />} onClick={() => exportCsv(detail)}>
                  导出数据
                </Button>
              </Space>

              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message="状态机：draft → running → completed → archived（running 可分支 cancelled）"
              />
            </Card>
          ) : (
            <Card>
              <EmptyState title="选择左侧实验" description="查看指标看板、状态流转与结论回写" />
            </Card>
          )}

          <Card title="实验方法模板" size="small" style={{ marginTop: 16 }}>
            <Table
              size="small"
              pagination={false}
              rowKey="value"
              dataSource={METHOD_OPTIONS}
              columns={[
                { title: '方法', dataIndex: 'label' },
                {
                  title: '操作',
                  width: 80,
                  render: (_, r) => (
                    <a
                      onClick={() => {
                        form.setFieldsValue({ method: r.value })
                        setOpen(true)
                      }}
                    >
                      使用
                    </a>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <Modal
        title="新建验证实验"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={create}
        okText="创建实验"
      >
        <Form form={form} layout="vertical" initialValues={{ method: 'landing_page' }}>
          <Form.Item label="实验方法" name="method" rules={[{ required: true }]}>
            <Select options={METHOD_OPTIONS} />
          </Form.Item>
          <Form.Item label="关联机会卡片" name="opportunity_id">
            <Select
              allowClear
              options={(opportunities ?? []).map((o) => ({ value: o.id, label: o.title }))}
              placeholder="选择后可将结论回写该机会的赛道评分"
            />
          </Form.Item>
          <Form.Item
            label="核心假设"
            name="hypothesis"
            rules={[{ required: true, message: '请填写可证伪的假设' }]}
          >
            <Input.TextArea rows={3} placeholder="例如：制造业客户愿意为 AI 质检付费" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
