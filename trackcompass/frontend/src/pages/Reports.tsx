import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Dropdown,
  Empty,
  List,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  DownloadOutlined,
  DownOutlined,
  FileTextOutlined,
  LinkOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import ReactMarkdownLite from '../components/MarkdownLite'
import PageHead from '../components/PageHead'
import { EmptyState } from '../components/widgets'
import { opportunityApi, reportApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import { runTask, useTask, useTaskVersion } from '../api/taskStore'
import type { Report } from '../api/types'

/**
 * 把「导出产物」的路径/URI 换成**同端口可访问的静态地址**。
 *
 * 导出接口返回的 `url` 是服务端本地的 `file:///D:/...`（浏览器打不开），
 * 真正的入口是后端挂载的 `/static/reports/<文件名>`（`main.py` 里 mount）。
 */
function staticUrlOf(fileUrlOrPath?: string | null): string | null {
  if (!fileUrlOrPath) return null
  const base = fileUrlOrPath.split(/[\\/]/).pop()
  if (!base) return null
  try {
    return `/static/reports/${encodeURIComponent(decodeURIComponent(base))}`
  } catch {
    return `/static/reports/${encodeURIComponent(base)}`
  }
}

/** P09 决策报告（《原型设计》§5.9） */
/** 报告生成的响应类型 —— 从客户端推导，避免手写重复的下游契约 */
type GeneratedReport = Awaited<ReturnType<typeof reportApi.create>>

export default function Reports() {
  const [opportunityId, setOpportunityId] = useState<string>()
  const [template, setTemplate] = useState('investment_memo')
  const [format, setFormat] = useState('markdown')
  const [preview, setPreview] = useState<Report | null>(null)
  /** 取正文时的加载态（列表接口不带 content，点「查看」要再拉一次详情） */
  const [opening, setOpening] = useState(false)
  /** 报告生成跑在应用级任务注册表里：文档生成要几秒，用户不必钉在页面上等 */
  const generateTask = useTask<GeneratedReport>('reports:generate')
  const generating = generateTask?.status === 'running'
  const taskVersion = useTaskVersion()

  const { data: templates } = useAsync(() => reportApi.templates(), [])
  const { data: opportunities } = useAsync(
    async () => (await opportunityApi.list({ limit: 50 })).opportunities,
    [],
  )
  const { data: reports, loading, refresh } = useAsync(
    async () => (await reportApi.list()).reports,
    [taskVersion],
  )

  const analyzeReady = (opportunities ?? []).filter(
    (o) => (o.total_score ?? 0) > 0,
  )

  const generate = async () => {
    if (!opportunityId) {
      message.warning('请先选择已完成分析的机会卡片')
      return
    }
    await runTask<GeneratedReport>(
      'reports:generate',
      {
        page: '决策报告',
        title: '生成决策报告',
        params: { opportunity: opportunityId, template, format },
      },
      () => reportApi.create({ opportunity_id: opportunityId, template, format }),
      {
        summarize: (r) => `${r.version} · ${r.evidence_count} 条证据引用 · ${format.toUpperCase()}`,
      },
    )
  }

  /** 认领生成结果（本页完成 / 离开期间完成，同一条路径） */
  useEffect(() => {
    if (!generateTask || generateTask.status !== 'done' || !generateTask.result) return
    setPreview(generateTask.result)
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generateTask?.finishedAt, generateTask?.status])

  /**
   * 点「查看」看正文。
   *
   * ⚠️ 列表接口**不返回 `content`**（只有元信息），因此不能直接把列表项塞进预览区 ——
   * 之前就是这么写的，结果每份历史报告都显示「该报告无正文内容」，
   * 看起来像「不能在线看」。正文要靠详情接口 `GET /reports/{id}` 单独取。
   * 刚生成的那份已经带正文（POST 响应里有 `sections_detail`），直接用，省一次请求。
   */
  const openReport = async (r: Report) => {
    if (r.content?.sections?.length) {
      setPreview(r)
      return
    }
    setOpening(true)
    try {
      setPreview(await reportApi.detail(r.id))
    } catch {
      message.error('报告正文加载失败，请重试')
    } finally {
      setOpening(false)
    }
  }

  /** 在线打开（浏览器新标签页看排版版）：有导出产物就直接开，没有就先导 HTML。 */
  const openOnline = async (report: Report) => {
    const existing = staticUrlOf(report.download_url)
    if (existing) {
      window.open(existing, '_blank')
      return
    }
    const res = await reportApi.exportAs(report.id, 'html')
    if (res.error) {
      message.warning(res.error)
      return
    }
    const url = staticUrlOf(res.path) ?? staticUrlOf(res.url)
    if (url) window.open(url, '_blank')
    else message.warning('导出成功，但未拿到可访问地址')
    await refresh()
  }

  const exportAs = async (report: Report, fmt: string) => {
    const res = await reportApi.exportAs(report.id, fmt)
    if (res.error) {
      message.warning(res.error)
      return
    }
    message.success(`已导出 ${res.format.toUpperCase()}`)
    // 网页类格式顺手打开；PPT / Word 属于「下载后编辑」，不弹窗口
    if (fmt === 'html') {
      const url = staticUrlOf(res.path) ?? staticUrlOf(res.url)
      if (url) window.open(url, '_blank')
    } else if (res.path) {
      message.info(`文件已生成：${res.path.split(/[\\/]/).pop()}`)
    }
    await refresh()
  }

  return (
    <>
      <PageHead
        title="决策报告"
        desc="一键生成立项报告 / 投资备忘录，含证据链、假设清单、里程碑与风险，支持 Markdown / HTML / JSON / PPT / Word 导出。"
        extra={
          <Space>
            <Select
              style={{ width: 220 }}
              placeholder="选择机会卡片"
              value={opportunityId}
              onChange={setOpportunityId}
              options={analyzeReady.map((o) => ({
                value: o.id,
                label: `${o.title}（${(o.total_score ?? 0).toFixed(2)}）`,
              }))}
            />
            <Select
              style={{ width: 150 }}
              value={template}
              onChange={setTemplate}
              options={(templates?.templates ?? []).map((t) => ({ value: t.key, label: t.name }))}
            />
            <Select
              style={{ width: 130 }}
              value={format}
              onChange={setFormat}
              options={[
                { value: 'markdown', label: 'Markdown' },
                { value: 'html', label: 'HTML' },
                { value: 'json', label: 'JSON' },
                { value: 'pptx', label: 'PPTX' },
                { value: 'docx', label: 'DOCX' },
              ]}
            />
            <Button
              type="primary"
              icon={<PlusOutlined />}
              loading={generating}
              onClick={generate}
            >
              生成报告
            </Button>
          </Space>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card title="报告列表" size="small" loading={loading}>
            {(reports ?? []).length === 0 ? (
              <EmptyState
                title="暂无报告"
                description="选择一个已完成分析的机会卡片，点击「生成报告」"
              />
            ) : (
              <List
                dataSource={reports ?? []}
                renderItem={(r) => (
                  <List.Item
                    actions={[
                      <a key="v" onClick={() => openReport(r)}>
                        查看全文
                      </a>,
                      <a key="o" onClick={() => openOnline(r)}>
                        在线打开
                      </a>,
                      <Dropdown
                        key="e"
                        menu={{
                          items: [
                            { key: 'html', label: '导出 HTML（可在线打开）' },
                            { key: 'markdown', label: '导出 Markdown' },
                            { key: 'pptx', label: '导出 PPT' },
                            { key: 'docx', label: '导出 Word' },
                            { key: 'json', label: '导出 JSON' },
                          ],
                          onClick: ({ key }) => exportAs(r, key),
                        }}
                      >
                        <a>
                          导出 <DownOutlined style={{ fontSize: 10 }} />
                        </a>
                      </Dropdown>,
                    ]}
                  >
                    <List.Item.Meta
                      avatar={<FileTextOutlined style={{ fontSize: 18, color: '#2563EB' }} />}
                      title={
                        <Space>
                          <span style={{ fontSize: 13 }}>{r.title}</span>
                          <Tag color="blue" className="tc-tag-soft">
                            {r.version}
                          </Tag>
                          <Tag className="tc-tag-soft">{r.format}</Tag>
                        </Space>
                      }
                      description={
                        <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                          {r.created_at?.slice(0, 10)} · 约 {r.page_count} 页 · 含 {r.evidence_count} 条证据
                        </Typography.Text>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>

          <Card title="模板说明" size="small" style={{ marginTop: 16 }}>
            <List
              size="small"
              dataSource={templates?.templates ?? []}
              renderItem={(t) => (
                <List.Item>
                  <List.Item.Meta
                    title={<span style={{ fontSize: 13 }}>{t.name}</span>}
                    description={
                      <span className="tc-muted" style={{ fontSize: 12 }}>
                        {t.tone} · {t.sections.length} 个章节
                      </span>
                    }
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Spin spinning={generating || opening}>
            {preview ? (
              <Card
                title={preview.title}
                size="small"
                extra={
                  <Space>
                    <Tag color="blue" className="tc-tag-soft">
                      {preview.version}
                    </Tag>
                    <Tag className="tc-tag-soft">{preview.evidence_count} 条证据</Tag>
                    <Button
                      size="small"
                      type="primary"
                      ghost
                      icon={<LinkOutlined />}
                      onClick={() => openOnline(preview)}
                    >
                      在线打开
                    </Button>
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      onClick={() => exportAs(preview, 'pptx')}
                    >
                      导出 PPT
                    </Button>
                    <Button
                      size="small"
                      icon={<DownloadOutlined />}
                      onClick={() => exportAs(preview, 'docx')}
                    >
                      导出 Word
                    </Button>
                  </Space>
                }
              >
                <Descriptions column={2} size="small" style={{ marginBottom: 12 }}>
                  <Descriptions.Item label="模板">
                    {(templates?.templates ?? []).find((t) => t.key === preview.template)?.name ??
                      preview.template}
                  </Descriptions.Item>
                  <Descriptions.Item label="页数">约 {preview.page_count} 页</Descriptions.Item>
                </Descriptions>

                {preview.content?.sections?.length ? (
                  preview.content.sections.map((s, i) => (
                    <div key={s.key}>
                      <Divider orientation="left" style={{ margin: '16px 0 8px' }}>
                        <Typography.Text strong style={{ fontSize: 14 }}>
                          {i + 1}. {s.title}
                        </Typography.Text>
                      </Divider>
                      <ReactMarkdownLite text={s.markdown} />
                      {s.citations?.length ? (
                        <div style={{ marginTop: 8 }}>
                          {s.citations.map((c) => (
                            <Tag key={c} color="blue" className="tc-tag-soft">
                              {c}
                            </Tag>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ))
                ) : (
                  <Empty description="该报告无正文内容" />
                )}

                <Alert
                  type="info"
                  showIcon
                  style={{ marginTop: 16 }}
                  message="报告全部结论均可下钻至原始信号，请结合专家复核后使用"
                />
              </Card>
            ) : (
              <Card>
                <EmptyState
                  title="报告预览区"
                  description="生成或点击左侧报告查看内容。报告含 9 个章节：执行摘要、赛道定义与市场空间、需求验证、技术评估、竞争格局、商业模式、风险与应对、里程碑与资源需求、证据链附录"
                />
              </Card>
            )}
          </Spin>
        </Col>
      </Row>
    </>
  )
}
