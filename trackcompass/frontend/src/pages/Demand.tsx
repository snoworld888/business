import { useState } from 'react'
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
  message,
} from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import PageHead from '../components/PageHead'
import { EmptyState } from '../components/widgets'
import { demandApi } from '../api/client'
import type { DemandProbe } from '../api/types'

const SOURCES = [
  { value: 'social', label: '社媒' },
  { value: 'forum', label: '论坛' },
  { value: 'review', label: '应用评论' },
  { value: 'bidding', label: '招标' },
  { value: 'recruit', label: '招聘' },
]

const INDUSTRIES = ['AI+制造', 'AI+医疗', '新能源科技', '具身智能']

/** P06 需求探针（《原型设计》§5.6） */
export default function Demand() {
  const navigate = useNavigate()
  const [keywords, setKeywords] = useState<string[]>(['质检'])
  const [sources, setSources] = useState<string[]>(['social', 'forum', 'review', 'bidding', 'recruit'])
  const [industry, setIndustry] = useState<string | undefined>('AI+制造')
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<DemandProbe | null>(null)

  const run = async () => {
    setLoading(true)
    setProgress(10)
    const timer = setInterval(() => setProgress((p) => Math.min(92, p + 12)), 160)
    try {
      const res = await demandApi.probe({ keywords, sources, industry })
      setResult(res)
      setProgress(100)
      message.success(`分析完成，匹配 ${res.matched_signals ?? 0} 条信号`)
    } finally {
      clearInterval(timer)
      setLoading(false)
    }
  }

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
            接入社媒、评论、论坛、招标、招聘数据，聚类痛点并输出需求强度。公式{' '}
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
              onChange={setSources}
              options={SOURCES}
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
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={16}>
            <Card title={`痛点聚类（${painPoints.length} 类）`} size="small">
              {painPoints.length === 0 ? (
                <EmptyState title="未聚类到痛点" description="尝试更换关键词或扩大数据源" />
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
                            • {s}
                          </div>
                        ))}
                      </div>
                      <Space size={8} wrap style={{ marginTop: 10 }}>
                        {(p.experiments ?? []).map((m) => (
                          <a key={m} onClick={() => createExperiment(m)} style={{ fontSize: 12 }}>
                            创建「{m}」
                          </a>
                        ))}
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
                {result.suggested_experiments.map((e) => (
                  <Button key={e} block onClick={() => createExperiment(e)}>
                    {e}
                  </Button>
                ))}
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
