import { useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Drawer,
  Modal,
  Row,
  Segmented,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  CloudDownloadOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  LinkOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import PageHead from '../components/PageHead'
import { signalApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import type { CollectReport, Signal, SourceAdapter } from '../api/types'

/** 信号类型中文名（与后端 `api/v1/signals.py::DATA_SOURCES` 保持一致） */
const TYPE_LABEL: Record<string, string> = {
  policy: '政策',
  patent: '专利',
  paper: '论文',
  funding: '融资',
  recruit: '招聘',
  social: '社媒',
  review: '评论',
  forum: '论坛',
  bidding: '招标',
  news: '产业媒体',
  supply: '供应链',
}

const TYPE_COLOR: Record<string, string> = {
  policy: 'red',
  paper: 'blue',
  news: 'green',
  bidding: 'orange',
  patent: 'purple',
  funding: 'gold',
  recruit: 'cyan',
  social: 'magenta',
}

/** P12 信号库 —— 信号层可见性（《技术文档》§3.1 数据源接入 / §3.2 数据管道）
 *
 * 为什么需要这一页：《设计文档》§12 的十页里没有它，但「证据链可追溯」
 * （§5.3 验收标准）要求任何结论都能下钻到**原始信号**。此前 40 条种子信号
 * 在前端没有任何入口，采集来的真实信号同样无处可看 —— 本页补上这个断点。
 */
export default function Signals() {
  const [type, setType] = useState<string>('all')
  const [collecting, setCollecting] = useState(false)
  const [dryRun, setDryRun] = useState(false)
  const [report, setReport] = useState<CollectReport | null>(null)
  const [detail, setDetail] = useState<Signal | null>(null)

  const { data, loading, refresh } = useAsync(
    () => signalApi.list({ signal_type: type === 'all' ? undefined : type, limit: 300 }),
    [type],
  )
  const { data: adapterData, refresh: refreshAdapters } = useAsync(
    () => signalApi.adapters(),
    [],
  )

  const signals = data?.signals ?? []
  const stats = data?.stats ?? {}
  const adapters: SourceAdapter[] = adapterData?.adapters ?? []
  const availableAdapters = adapters.filter((a) => a.available)

  const handleCollect = async () => {
    setCollecting(true)
    try {
      const res = await signalApi.collect({ dry_run: dryRun, limit_per_source: 10 })
      setReport(res)
      if (!res.dry_run) {
        refresh()
        refreshAdapters()
      }
      message.success(
        res.dry_run
          ? `干跑完成：抓取 ${res.total_fetched} 条，未写库`
          : `采集完成：新增 ${res.total_inserted} 条，去重 ${res.total_duplicated} 条`,
      )
    } catch {
      message.error('采集失败，请查看后端日志')
    } finally {
      setCollecting(false)
    }
  }

  return (
    <>
      <PageHead
        title="信号库"
        desc="信号层的原始数据：政策、论文、招标、产业媒体。趋势雷达的所有结论都能在这里找到出处。"
        extra={
          <Space>
            <Checkbox
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              data-testid="dry-run"
            >
              <Tooltip title="只采集与清洗，不写入数据库 —— 接入新数据源前建议先干跑">
                <span>干跑</span>
              </Tooltip>
            </Checkbox>
            <Button
              type="primary"
              icon={<CloudDownloadOutlined />}
              loading={collecting}
              onClick={handleCollect}
            >
              立即采集
            </Button>
          </Space>
        }
      />

      {/* ---- 采集能力概览 ---- */}
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="信号总量" value={data?.total ?? 0} suffix="条" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="可用数据源"
              value={availableAdapters.length}
              suffix={`/ ${adapters.length}`}
              valueStyle={{ color: availableAdapters.length >= 5 ? '#16a34a' : '#d97706' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="信号类型" value={Object.keys(stats).length} suffix="/ 11" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="最近同步"
              value={
                signals[0]?.published_at
                  ? String(signals[0].published_at).slice(0, 16).replace('T', ' ')
                  : '—'
              }
              valueStyle={{ fontSize: 18 }}
            />
          </Card>
        </Col>
      </Row>

      {adapterData && !adapterData.coverage_ok ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="数据源覆盖未达标"
          description={`《设计文档》§7.2 要求覆盖至少 5 个数据源，当前可用 ${availableAdapters.length} 个。`}
        />
      ) : null}

      <Row gutter={12}>
        {/* ---- 信号列表 ---- */}
        <Col span={16}>
          <Card
            size="small"
            title={
              <Space>
                <DatabaseOutlined />
                信号列表
              </Space>
            }
            extra={
              <Space>
                <Segmented
                  size="small"
                  value={type}
                  onChange={(v) => setType(String(v))}
                  options={[
                    { label: '全部', value: 'all' },
                    ...Object.entries(TYPE_LABEL)
                      .filter(([k]) => (stats[k] ?? 0) > 0)
                      .map(([k, v]) => ({ label: `${v} ${stats[k]}`, value: k })),
                  ]}
                />
                <Button size="small" icon={<ReloadOutlined />} onClick={refresh} />
              </Space>
            }
          >
            <Table<Signal>
              size="small"
              rowKey="id"
              loading={loading}
              dataSource={signals}
              pagination={{ pageSize: 12, size: 'small', showSizeChanger: false }}
              onRow={(record) => ({
                onClick: () => setDetail(record),
                style: { cursor: 'pointer' },
              })}
              columns={[
                {
                  title: '类型',
                  dataIndex: 'signal_type',
                  width: 92,
                  render: (v: string) => (
                    <Tag color={TYPE_COLOR[v] ?? 'default'}>{TYPE_LABEL[v] ?? v}</Tag>
                  ),
                },
                {
                  title: '标题',
                  dataIndex: 'title',
                  ellipsis: true,
                  render: (v: string, r) => (
                    <Space direction="vertical" size={0}>
                      <Typography.Text style={{ fontSize: 13 }}>{v}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                        {r.source}
                        {r.industry ? ` · ${r.industry}` : ''}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '时间',
                  dataIndex: 'published_at',
                  width: 108,
                  render: (v?: string) => (
                    <span className="tc-mono" style={{ fontSize: 11 }}>
                      {v ? v.slice(0, 10) : '—'}
                    </span>
                  ),
                },
              ]}
            />
          </Card>
        </Col>

        {/* ---- 数据源适配器 ---- */}
        <Col span={8}>
          <Card
            size="small"
            title={
              <Space>
                <ExperimentOutlined />
                数据源覆盖
              </Space>
            }
            extra={<Badge status="processing" text={`${availableAdapters.length} 可用`} />}
          >
            <Table<SourceAdapter>
              size="small"
              rowKey="key"
              pagination={false}
              dataSource={adapters}
              columns={[
                {
                  title: '数据源',
                  dataIndex: 'name',
                  render: (v: string, r) => (
                    <Space direction="vertical" size={0}>
                      <Typography.Text style={{ fontSize: 12 }}>{v}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                        {TYPE_LABEL[r.signal_type] ?? r.signal_type} · {r.frequency}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '状态',
                  dataIndex: 'available',
                  width: 84,
                  render: (v: boolean, r) => (
                    <Tooltip title={r.unavailable_reason ?? undefined}>
                      <Badge
                        status={v ? 'success' : 'default'}
                        text={
                          <span style={{ fontSize: 11 }}>
                            {v ? '可采集' : r.requires_key ? '待授权' : '不可用'}
                          </span>
                        }
                      />
                    </Tooltip>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      {/* ---- 信号详情 ---- */}
      <Drawer
        open={!!detail}
        width={620}
        title="信号详情"
        onClose={() => setDetail(null)}
      >
        {detail ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Typography.Title level={5} style={{ margin: 0 }}>
              {detail.title}
            </Typography.Title>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="类型">
                <Tag color={TYPE_COLOR[detail.signal_type] ?? 'default'}>
                  {TYPE_LABEL[detail.signal_type] ?? detail.signal_type}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="来源">{detail.source}</Descriptions.Item>
              <Descriptions.Item label="行业">{detail.industry ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="发布时间">
                {detail.published_at ? String(detail.published_at).slice(0, 19).replace('T', ' ') : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="关键词" span={2}>
                {(detail.keywords?.length ?? 0) > 0 ? (
                  <Space size={[4, 4]} wrap>
                    {detail.keywords.map((k) => (
                      <Tag key={k} style={{ fontSize: 11 }}>
                        {k}
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
            </Descriptions>

            {detail.url ? (
              <Button
                type="link"
                icon={<LinkOutlined />}
                href={detail.url}
                target="_blank"
                rel="noreferrer"
                style={{ paddingLeft: 0 }}
              >
                查看原文
              </Button>
            ) : null}

            <Card size="small" title="正文">
              <Typography.Paragraph
                style={{ whiteSpace: 'pre-wrap', fontSize: 13, marginBottom: 0 }}
              >
                {detail.content || '（该源未提供正文摘要）'}
              </Typography.Paragraph>
            </Card>
          </Space>
        ) : null}
      </Drawer>

      {/* ---- 采集报告 ---- */}
      <Modal
        open={!!report}
        title={report?.dry_run ? '采集报告（干跑·未写库）' : '采集报告'}
        width={760}
        footer={<Button onClick={() => setReport(null)}>关闭</Button>}
        onCancel={() => setReport(null)}
      >
        {report ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type={report.failed_sources > 0 ? 'warning' : 'success'}
              showIcon
              message={`检索词：${report.keywords.join('、')}${
                report.industry ? `　｜　行业：${report.industry}` : ''
              }`}
              description={`抓取 ${report.total_fetched} 条，新增 ${report.total_inserted} 条，去重 ${report.total_duplicated} 条，丢弃 ${report.total_dropped} 条，耗时 ${report.elapsed_ms} ms`}
            />
            <Table
              size="small"
              rowKey="key"
              pagination={false}
              dataSource={report.sources}
              columns={[
                { title: '数据源', dataIndex: 'name', ellipsis: true },
                {
                  title: '状态',
                  dataIndex: 'status',
                  width: 80,
                  render: (v: string) => (
                    <Badge
                      status={v === 'ok' ? 'success' : v === 'skipped' ? 'default' : 'error'}
                      text={v === 'ok' ? '成功' : v === 'skipped' ? '跳过' : '失败'}
                    />
                  ),
                },
                { title: '抓取', dataIndex: 'fetched', width: 60 },
                { title: '新增', dataIndex: 'inserted', width: 60 },
                { title: '重复', dataIndex: 'duplicated', width: 60 },
                {
                  title: '耗时',
                  dataIndex: 'elapsed_ms',
                  width: 80,
                  render: (v: number) => `${v} ms`,
                },
                {
                  title: '说明',
                  width: 200,
                  render: (_: unknown, r) => (
                    <Typography.Text type="secondary" style={{ fontSize: 11 }} ellipsis>
                      {r.error ?? r.note ?? ''}
                    </Typography.Text>
                  ),
                },
              ]}
            />
          </Space>
        ) : null}
      </Modal>
    </>
  )
}
