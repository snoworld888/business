import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  BellOutlined,
  FireOutlined,
  PlusOutlined,
  SyncOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import PageHead from '../components/PageHead'
import { EmptyState, formatClock } from '../components/widgets'
import { trendApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import { usePersistentState } from '../hooks/usePersistentState'
import { runTask, useTask, useTaskVersion } from '../api/taskStore'
import type { Trend, TrendScanResult } from '../api/types'

const INDUSTRIES = ['AI+制造', 'AI+医疗', '新能源科技', '具身智能']

/** P04 趋势雷达（《原型设计》§5.4）
 *
 * 筛选条件与「上一次扫描结果」走会话级持久化（`usePersistentState`）：
 * 「能力画像 × 外部信号」面板是扫描的产物，切走再回来不该消失。
 */
export default function Trends() {
  const navigate = useNavigate()
  const [industry, setIndustry] = usePersistentState<string | undefined>('trends:industry', undefined)
  const [direction, setDirection] = usePersistentState<string>('trends:direction', 'all')
  const [detail, setDetail] = useState<Trend | null>(null)
  const [scanResult, setScanResult] = usePersistentState<TrendScanResult | null>(
    'trends:scanResult',
    null,
  )
  const [scannedAt, setScannedAt] = usePersistentState<string | null>('trends:scannedAt', null)
  /** 扫描跑在应用级任务注册表里：切页不中断，回来仍能认领结果 */
  const scanTask = useTask<TrendScanResult>('trends:scan')
  const scanning = scanTask?.status === 'running'
  /** 任何后台任务完成都 +1 ⇒ 列表随之自动刷新（不必手点「重新扫描」旁的刷新） */
  const taskVersion = useTaskVersion()

  const { data, loading, refresh } = useAsync(
    () =>
      trendApi.list({
        industry,
        direction: direction === 'all' ? undefined : direction,
        limit: 100,
      }),
    [industry, direction, taskVersion],
  )

  const trends = data?.trends ?? []

  const handleScan = async () => {
    await runTask<TrendScanResult>(
      'trends:scan',
      { page: '趋势雷达', title: '趋势扫描', params: { industry } },
      () => trendApi.scan({ industry }),
      {
        summarize: (r) => {
          const gapN = r.capability_gaps?.length ?? 0
          return (
            `扫描 ${r.scanned_signals} 条信号，生成 ${r.generated} 条趋势` +
            (gapN ? `；另有 ${gapN} 个能力词无外部信号支撑` : '')
          )
        },
      },
    )
  }

  /** 认领扫描结果（本页完成 / 离开期间完成，同一条路径） */
  useEffect(() => {
    if (!scanTask || scanTask.status !== 'done' || !scanTask.result) return
    setScanResult(scanTask.result)
    setScannedAt(scanTask.finishedAt ?? scanTask.startedAt)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanTask?.finishedAt, scanTask?.status])

  const addToPool = (t: Trend) => {
    Modal.confirm({
      title: `将「${t.name}」加入机会池`,
      content: (
        <Form layout="vertical">
          <Form.Item label="机会标题">
            <Input id="pool-title" defaultValue={t.name} />
          </Form.Item>
          <Form.Item label="一句话说明" style={{ marginBottom: 0 }}>
            <Input.TextArea id="pool-summary" rows={3} defaultValue={t.description} />
          </Form.Item>
        </Form>
      ),
      okText: '确认加入',
      onOk: async () => {
        const title = (document.getElementById('pool-title') as HTMLInputElement)?.value
        const summary = (document.getElementById('pool-summary') as HTMLTextAreaElement)?.value
        const res = await trendApi.toPool(t.id, { title, summary })
        message.success(`已生成机会卡片：${res.title}`)
        await refresh()
        navigate(`/scorecard?opportunity=${res.opportunity_id}`)
      },
    })
  }

  const toggleSubscribe = async (t: Trend) => {
    await trendApi.subscribe(t.id, !t.subscribed)
    message.success(t.subscribed ? '已取消订阅' : '已订阅该趋势信号预警')
    await refresh()
  }

  return (
    <>
      <PageHead
        title="趋势雷达"
        desc={
          <span>
            扫描 PESTEL 宏观环境、技术成熟度曲线、资本热度与政策文件，输出候选技术方向。
            评分算法 <code className="tc-mono">{data?.algorithm ?? '0.3*signal + 0.3*growth + 0.2*policy + 0.2*capital'}</code>
          </span>
        }
        extra={
          <Space>
            <Button icon={<SyncOutlined />} loading={scanning} onClick={handleScan}>
              重新扫描
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/profile')}>
              调整搜索边界
            </Button>
          </Space>
        }
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            allowClear
            placeholder="行业"
            style={{ width: 160 }}
            value={industry}
            onChange={setIndustry}
            options={INDUSTRIES.map((v) => ({ value: v, label: v }))}
          />
          <Segmented
            value={direction}
            onChange={(v) => setDirection(String(v))}
            options={[
              { label: '全部', value: 'all' },
              { label: '顺风 ▲', value: 'tailwind' },
              { label: '逆风 ▼', value: 'headwind' },
            ]}
          />
          <Tag className="tc-tag-soft">共 {trends.length} 条趋势</Tag>
        </Space>
      </Card>

      {scanResult ? <CapabilityPanel result={scanResult} scannedAt={scannedAt} /> : null}

      <Spin spinning={loading}>
        {trends.length === 0 ? (
          <Card>
            <EmptyState
              title="暂无趋势数据"
              description="点击右上角「重新扫描」，趋势侦察 Agent 将扫描全部信号并聚合技术方向"
              actionText="立即扫描"
              onAction={handleScan}
            />
          </Card>
        ) : (
          <Row gutter={[16, 12]}>
            {trends.map((t) => (
              <Col xs={24} lg={12} xxl={8} key={t.id}>
                <Card
                  size="small"
                  hoverable
                  onClick={() => setDetail(t)}
                  title={
                    <Space>
                      {t.direction === 'tailwind' ? (
                        <FireOutlined style={{ color: '#EF4444' }} />
                      ) : (
                        <WarningOutlined style={{ color: '#F59E0B' }} />
                      )}
                      <span>{t.name}</span>
                    </Space>
                  }
                  extra={
                    <Tag
                      color={t.direction === 'tailwind' ? 'success' : 'warning'}
                      className="tc-tag-soft"
                    >
                      {t.direction === 'tailwind' ? '顺风 ▲' : '逆风 ▼'}
                    </Tag>
                  }
                >
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                      {t.description?.slice(0, 78) || '—'}
                    </Typography.Text>
                    <Space size={8} wrap>
                      <Tag className="tc-tag-soft">置信度 {t.confidence.toFixed(2)}</Tag>
                      <Tag className="tc-tag-soft">评分 {t.score.toFixed(3)}</Tag>
                      <Tag className="tc-tag-soft">证据 {t.evidence?.length ?? 0} 条</Tag>
                      {t.capability_match ? (
                        <Tag color="gold" className="tc-tag-soft">
                          ⚑ 能力匹配{t.matched_capabilities?.length
                            ? `：${t.matched_capabilities.slice(0, 3).join('/')}`
                            : ''}
                        </Tag>
                      ) : null}
                      {t.subscribed ? (
                        <Tag color="blue" className="tc-tag-soft">
                          已订阅
                        </Tag>
                      ) : null}
                    </Space>
                    <Space size={8} onClick={(e) => e.stopPropagation()}>
                      <a onClick={() => setDetail(t)}>查看详情</a>
                      <a onClick={() => addToPool(t)}>加入机会池</a>
                      <a onClick={() => toggleSubscribe(t)}>
                        {t.subscribed ? '取消订阅' : '订阅预警'}
                      </a>
                    </Space>
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Spin>

      <Drawer
        title={detail?.name}
        width={560}
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
      >
        {detail ? (
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <Space wrap>
              <Tag color={detail.direction === 'tailwind' ? 'success' : 'warning'} className="tc-tag-soft">
                {detail.direction === 'tailwind' ? '顺风 ▲' : '逆风 ▼'}
              </Tag>
              <Tag className="tc-tag-soft">置信度 {detail.confidence.toFixed(2)}</Tag>
              <Tag className="tc-tag-soft">综合评分 {detail.score.toFixed(4)}</Tag>
            </Space>

            <Typography.Paragraph style={{ fontSize: 13 }}>{detail.description}</Typography.Paragraph>

            <Card size="small" title="评分因子（可解释）">
              <List
                size="small"
                dataSource={Object.entries(detail.factors ?? {})}
                renderItem={([k, v]) => (
                  <List.Item>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <Typography.Text className="tc-mono">{k}</Typography.Text>
                      <Typography.Text strong>{(v as number).toFixed(4)}</Typography.Text>
                    </Space>
                  </List.Item>
                )}
              />
            </Card>

            <Card size="small" title={`证据链（${detail.evidence?.length ?? 0} 条）`}>
              {(detail.evidence ?? []).length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无证据" />
              ) : (
                detail.evidence.map((e, i) => (
                  <div className="tc-evidence" key={i}>
                    <div style={{ fontWeight: 500, fontSize: 13 }}>
                      {String((e as Record<string, unknown>).title ?? '—')}
                    </div>
                    <Space size={10}>
                      <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                        {String((e as Record<string, unknown>).source ?? '')}
                      </Typography.Text>
                      {(e as Record<string, unknown>).url ? (
                        <a
                          href={String((e as Record<string, unknown>).url)}
                          target="_blank"
                          rel="noreferrer"
                          style={{ fontSize: 12 }}
                        >
                          查看原文
                        </a>
                      ) : null}
                    </Space>
                  </div>
                ))
              )}
            </Card>

            <Space>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => addToPool(detail)}>
                加入机会池
              </Button>
              <Button icon={<BellOutlined />} onClick={() => toggleSubscribe(detail)}>
                {detail.subscribed ? '取消订阅' : '订阅预警'}
              </Button>
            </Space>
          </Space>
        ) : null}
      </Drawer>
    </>
  )
}

/**
 * 能力画像 × 外部信号 的交叉核对面板。
 *
 * 存在的意义：能力画像（技术栈 / 专利）此前**完全不参与**趋势雷达，
 * 用户填了「智能体」等能力词却在雷达里看不到任何反馈，容易误以为系统漏读。
 * 这里明确区分两类东西：
 *   · 已匹配 —— 该能力有对应趋势（有外部信号支撑）
 *   · 情报空白 —— 画像写了、但外部零信号；**不是趋势**，是需要补数据源的地方
 */
function CapabilityPanel({ result, scannedAt }: { result: TrendScanResult; scannedAt?: string | null }) {
  const gaps = result.capability_gaps ?? []
  const matched = result.capability_matched ?? []
  const terms = result.capability_terms ?? []
  if (!terms.length) return null

  return (
    <Card
      size="small"
      title="能力画像 × 外部信号"
      style={{ marginBottom: 16 }}
      extra={
        <Space>
          {scannedAt ? <Tag className="tc-tag-soft">扫描于 {formatClock(scannedAt)}</Tag> : null}
          <Tag className="tc-tag-soft">画像能力词 {terms.length} 个</Tag>
        </Space>
      }
    >
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <Space size={8} wrap>
          <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
            已匹配（有外部信号支撑）：
          </Typography.Text>
          {matched.length ? (
            matched.map((m) => (
              <Tag color="gold" className="tc-tag-soft" key={m}>
                ⚑ {m}
              </Tag>
            ))
          ) : (
            <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
              —
            </Typography.Text>
          )}
        </Space>

        <Space size={8} wrap>
          <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
            情报空白（画像写了、外部零信号）：
          </Typography.Text>
          {gaps.length ? (
            gaps.map((g) => (
              <Tag className="tc-tag-soft" key={g.name}>
                {g.name}
              </Tag>
            ))
          ) : (
            <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
              无 —— 画像里的能力词都有信号支撑
            </Typography.Text>
          )}
        </Space>

        {gaps.length ? (
          <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
            ⚠️ 上排能力词来自你的能力画像（技术栈 / 专利），但当前信号库里**没有任何证据**。
            它们不是趋势，而是**情报空白** —— 建议补数据源或做定向调研，而不是据此决策。
          </Typography.Text>
        ) : null}
      </Space>
    </Card>
  )
}
