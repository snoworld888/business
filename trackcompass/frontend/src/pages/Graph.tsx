import { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  List,
  Radio,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined, ShareAltOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import PageHead from '../components/PageHead'
import { EmptyState } from '../components/widgets'
import { opportunityApi, trendApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'

const TYPE_COLOR: Record<string, string> = {
  Track: '#2563EB',
  Technology: '#10B981',
  Company: '#F59E0B',
  Opportunity: '#8B5CF6',
  Signal: '#CBD5E1',
  Patent: '#EF4444',
  Policy: '#0EA5E9',
  Funding: '#EAB308',
  Talent: '#EC4899',
  Trend: '#0F766E',
}
const TYPE_SIZE: Record<string, number> = {
  Track: 34,
  Company: 22,
  Opportunity: 26,
  Technology: 18,
  Signal: 8,
  Patent: 14,
  Policy: 16,
  Funding: 16,
  Talent: 14,
  Trend: 20,
}

/** 图谱关系名中文化 —— 后端存的是 Cypher 风格的大写关系名，
 *  直接显示会让用户看到 IMPACTS / BELONGS_TO 这类非中文串。 */
const REL_LABEL: Record<string, string> = {
  IMPACTS: '影响',
  BELONGS_TO: '属于',
  CONTAINS: '包含',
}
const relName = (rel: unknown) => REL_LABEL[String(rel)] ?? String(rel ?? '')

/** P05 机会图谱（《原型设计》§5.5） */
export default function Graph() {
  const navigate = useNavigate()
  const [view, setView] = useState<'map' | 'blank' | 'centrality'>('map')
  const [rebuilding, setRebuilding] = useState(false)

  const { data: graph, loading, refresh } = useAsync(() => opportunityApi.graph(true), [])
  const { data: insights, refresh: refreshInsights } = useAsync(
    () => opportunityApi.graphInsights(),
    [],
  )
  const { data: tracks, refresh: refreshTracks } = useAsync(() => trendApi.list({ limit: 5 }), [])

  const option = useMemo(() => {
    if (!graph) return {}
    const hideSignals = graph.nodes.length > 120
    const nodes = graph.nodes
      .filter((n) => !(hideSignals && n.data.type === 'Signal'))
      .map((n) => ({
        id: n.data.id,
        name: n.data.label,
        category: n.data.type,
        symbolSize: TYPE_SIZE[n.data.type] ?? 12,
        itemStyle: { color: TYPE_COLOR[n.data.type] ?? '#94A3B8' },
        label: {
          show: (TYPE_SIZE[n.data.type] ?? 12) >= 18,
          fontSize: 11,
          color: '#0F172A',
        },
      }))
    const visible = new Set(nodes.map((n) => n.id))
    const links = graph.edges
      .filter((e) => visible.has(e.data.source) && visible.has(e.data.target))
      .map((e) => ({
        source: e.data.source,
        target: e.data.target,
        value: relName(e.data.label),
        lineStyle: { color: '#CBD5E1', width: 1, curveness: 0.08 },
        label: { show: false, formatter: relName(e.data.label) },
      }))

    return {
      tooltip: {
        formatter: (p: any) =>
          p.dataType === 'edge'
            ? `${p.data.value}`
            : `<b>${p.data.name}</b><br/>类型：${p.data.category}`,
      },
      legend: {
        data: Object.keys(TYPE_COLOR),
        bottom: 0,
        textStyle: { color: '#64748B', fontSize: 11 },
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          categories: Object.keys(TYPE_COLOR).map((t) => ({ name: t })),
          force: { repulsion: 260, edgeLength: [70, 160], gravity: 0.12 },
          emphasis: { focus: 'adjacency', label: { show: true } },
          data: nodes,
          links,
          lineStyle: { opacity: 0.6 },
        },
      ],
    }
  }, [graph])

  const rebuild = async () => {
    setRebuilding(true)
    try {
      const res = await opportunityApi.graph(true)
      message.success(`图谱已重建：${res.node_count} 节点 / ${res.edge_count} 关系`)
      await Promise.all([refresh(), refreshInsights()])
    } finally {
      setRebuilding(false)
    }
  }

  const toOpportunity = async (trackName: string) => {
    const trendList = tracks?.trends ?? []
    const match = trendList.find((t) => t.name === trackName)
    if (!match) {
      message.warning('该空白点暂无对应趋势，请先在趋势雷达扫描')
      return
    }
    const res = await trendApi.toPool(match.id, {
      title: `${trackName}（空白点）`,
      summary: match.description,
    })
    message.success('已从空白点创建机会卡片')
    await Promise.all([refreshTracks(), refresh()])
    navigate(`/scorecard?opportunity=${res.opportunity_id}`)
  }

  return (
    <>
      <PageHead
        title="机会图谱"
        desc="可视化赛道节点、上下游、玩家、专利、融资关系，并用图算法（连接度、中心性、社区发现）识别空白点。"
        extra={
          <Space>
            <Radio.Group value={view} onChange={(e) => setView(e.target.value)} optionType="button">
              <Radio.Button value="map">赛道地图</Radio.Button>
              <Radio.Button value="blank">空白点</Radio.Button>
              <Radio.Button value="centrality">中心性</Radio.Button>
            </Radio.Group>
            <Button icon={<ReloadOutlined />} loading={rebuilding} onClick={rebuild}>
              重建图谱
            </Button>
          </Space>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card
            size="small"
            title={
              <Space>
                <ShareAltOutlined />
                <span>知识图谱</span>
                <Tag className="tc-tag-soft">{graph?.node_count ?? 0} 节点</Tag>
                <Tag className="tc-tag-soft">{graph?.edge_count ?? 0} 关系</Tag>
              </Space>
            }
          >
            <Spin spinning={loading}>
              {(graph?.nodes?.length ?? 0) === 0 ? (
                <EmptyState
                  title="图谱为空"
                  description="点击「重建图谱」，系统将从信号中抽取 Company / Technology / Track / Opportunity 等实体与关系"
                  actionText="立即重建"
                  onAction={rebuild}
                />
              ) : (
                <>
                  <ReactECharts
                    option={option}
                    style={{ height: 520, width: '100%' }}
                    opts={{ renderer: 'canvas' }}
                    notMerge
                  />
                  <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                    支持缩放、拖拽、节点高亮；图例见底部。节点规模较大时自动隐藏 Signal 细粒度节点以保证流畅度。
                  </Typography.Text>
                </>
              )}
            </Spin>
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          {view === 'map' ? (
            <Card title="赛道库" size="small">
              {(tracks?.trends ?? []).length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无赛道数据" />
              ) : (
                <List
                  size="small"
                  dataSource={tracks?.trends ?? []}
                  renderItem={(t) => (
                    <List.Item
                      actions={[
                        <a key="p" onClick={() => toOpportunity(t.name)}>
                          转为机会
                        </a>,
                      ]}
                    >
                      <List.Item.Meta
                        title={<span style={{ fontSize: 13 }}>{t.name}</span>}
                        description={
                          <span className="tc-muted" style={{ fontSize: 12 }}>
                            {t.description?.slice(0, 46) || '—'}
                          </span>
                        }
                      />
                    </List.Item>
                  )}
                />
              )}
            </Card>
          ) : null}

          {view === 'blank' ? (
            <Card title="空白点识别" size="small">
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="图算法：连接度低的赛道节点"
                description="对应技术文档 §4.3 的 Cypher 查询，degree < 5 视为稀疏区域，可能蕴含未覆盖机会。"
              />
              {(insights?.blank_spots ?? []).length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无空白点" />
              ) : (
                <Table
                  size="small"
                  pagination={false}
                  rowKey="name"
                  dataSource={insights?.blank_spots ?? []}
                  columns={[
                    { title: '赛道', dataIndex: 'name' },
                    { title: '连接度', dataIndex: 'degree', width: 80 },
                    {
                      title: '操作',
                      width: 90,
                      render: (_, r) => <a onClick={() => toOpportunity(r.name)}>转为机会</a>,
                    },
                  ]}
                />
              )}
              {(insights?.suggestions ?? []).length ? (
                <List
                  size="small"
                  style={{ marginTop: 12 }}
                  dataSource={insights?.suggestions ?? []}
                  renderItem={(s) => (
                    <List.Item>
                      <List.Item.Meta
                        title={<span style={{ fontSize: 13 }}>{s.track}</span>}
                        description={
                          <span className="tc-muted" style={{ fontSize: 12 }}>
                            {s.reason} · {s.action}
                          </span>
                        }
                      />
                    </List.Item>
                  )}
                />
              ) : null}
            </Card>
          ) : null}

          {view === 'centrality' ? (
            <Card title="中心性排名" size="small">
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="度中心性越高，代表该实体在网络中连接越广，往往是关键卡位点"
              />
              <Table
                size="small"
                pagination={false}
                rowKey="name"
                dataSource={insights?.centrality ?? []}
                columns={[
                  { title: '实体', dataIndex: 'name', ellipsis: true },
                  { title: '类型', dataIndex: 'label', width: 100 },
                  { title: '中心性', dataIndex: 'centrality', width: 90 },
                ]}
              />
              {(insights?.communities ?? []).length ? (
                <>
                  <Typography.Text strong style={{ fontSize: 13, display: 'block', marginTop: 16 }}>
                    社区发现（{insights?.communities?.length} 个簇）
                  </Typography.Text>
                  <Space wrap size={6} style={{ marginTop: 8 }}>
                    {(insights?.communities ?? []).slice(0, 6).map((c, i) => (
                      <Tag key={i} className="tc-tag-soft">
                        簇 {i + 1}：{c.slice(0, 3).join('、')}
                        {c.length > 3 ? ` 等 ${c.length} 项` : ''}
                      </Tag>
                    ))}
                  </Space>
                </>
              ) : null}
            </Card>
          ) : null}
        </Col>
      </Row>
    </>
  )
}
