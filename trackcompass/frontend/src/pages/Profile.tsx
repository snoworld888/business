import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Row,
  Select,
  Space,
  Steps,
  Tag,
  Upload,
  message,
} from 'antd'
import { InboxOutlined, PlusOutlined, SaveOutlined, SyncOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import PageHead from '../components/PageHead'
import Radar from '../components/Radar'
import { profileApi, trendApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import type { CompanyProfile, CompanyProfileSummary } from '../api/types'

interface FormValues {
  name: string
  industry?: string
  founded_at?: string
  scale?: string
  tech_stack?: string[]
  tech_description?: string
  patents?: string[]
  talents?: Record<string, number>
  funding_round?: string
  funding_amount?: number
  channels?: string[]
  customers?: string[]
}

const INDUSTRIES = ['AI+制造', 'AI+医疗', '新能源科技', '具身智能', 'AI+农业', 'AI+教育']
const SCALES = ['1-50人', '50-200人', '200-500人', '500-2000人', '2000人以上']

/** P03 公司能力画像（《原型设计》§5.3） */
export default function Profile() {
  const [form] = Form.useForm<FormValues>()
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [saving, setSaving] = useState(false)
  const [switching, setSwitching] = useState(false)
  const [radar, setRadar] = useState<Record<string, number> | null>(null)
  const [tracks, setTracks] = useState<string[]>([])
  const [boundary, setBoundary] = useState<Record<string, unknown>>({})
  const [vectorDim, setVectorDim] = useState<number | null>(null)
  /** 当前正在编辑的公司画像 id；为 null 表示「新建公司」 */
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [switchError, setSwitchError] = useState<string | null>(null)
  /** 供应链目前无表单字段，保存时需原样带回当前公司的值，避免被清空 */
  const [supplyChain, setSupplyChain] = useState<string[]>([])

  const { data: profile, loading, error: loadError, refresh: reloadProfile } =
    useAsync(() => profileApi.get(), [])
  const { data: listData, refresh: refreshList } = useAsync(() => profileApi.list(), [])
  const { data: trackData } = useAsync(() => profileApi.tracks(), [])

  const companies: CompanyProfileSummary[] = listData?.profiles ?? []

  /** 把一份画像回填到左侧表单与右侧面板（切换公司与首次加载共用） */
  const applyProfile = useCallback((p: CompanyProfile) => {
    const funding = (p.funding ?? {}) as { round?: string; amount?: number }
    form.setFieldsValue({
      name: p.name,
      industry: p.industry ?? undefined,
      founded_at: p.founded_at ?? undefined,
      scale: p.scale ?? undefined,
      tech_stack: p.tech_stack ?? [],
      tech_description: p.tech_description ?? '',
      patents: p.patents ?? [],
      talents: (p.talents ?? {}) as FormValues['talents'],
      funding_round: funding.round,
      funding_amount: funding.amount,
      channels: p.channels ?? [],
      customers: p.customers ?? [],
    })
    setRadar(p.radar ?? null)
    setTracks(p.recommended_tracks ?? [])
    setBoundary(p.boundary ?? {})
    setVectorDim(p.vector_dim ?? null)
    setSupplyChain(p.supply_chain ?? [])
    setCurrentId(p.id)
    setSavedAt(p.updated_at ?? null)
  }, [form])

  // 首次加载：后端返回「当前公司」（= 最近保存的那一份）
  useEffect(() => {
    if (profile) applyProfile(profile)
  }, [profile, applyProfile])

  /** 切换公司：按 id 拉取完整画像后回填 */
  const switchCompany = async (id: string) => {
    if (id === currentId) return
    setSwitching(true)
    setSwitchError(null)
    try {
      const p = await profileApi.get(id)
      if (!p) {
        setSwitchError('该公司画像已不存在，请点右侧刷新重试')
        refreshList()
        return
      }
      applyProfile(p)
      message.success(`已载入「${p.name}」的能力画像`)
    } catch (e) {
      setSwitchError(e instanceof Error ? e.message : String(e))
    } finally {
      setSwitching(false)
    }
  }

  /** 新建公司：清空表单与面板；保存时不带 id ⇒ 后端新建一份画像 */
  const createNew = () => {
    form.resetFields()
    setRadar(null)
    setTracks([])
    setBoundary({})
    setVectorDim(null)
    setSupplyChain([])
    setCurrentId(null)
    setSavedAt(null)
    setSwitchError(null)
    setStep(0)
    message.info('已切换到「新建公司」，填写后保存即创建新档案')
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    setSwitchError(null)
    try {
      const payload: Partial<CompanyProfile> = {
        // 带 id ⇒ 更新该公司；不带 id ⇒ 后端新建（同名会被视为同一家，不会重复建档）
        ...(currentId ? { id: currentId } : {}),
        name: values.name,
        industry: values.industry,
        founded_at: values.founded_at,
        scale: values.scale,
        tech_stack: values.tech_stack ?? [],
        tech_description: values.tech_description ?? '',
        patents: values.patents ?? [],
        talents: values.talents ?? {},
        funding: { round: values.funding_round, amount: values.funding_amount },
        channels: values.channels ?? [],
        customers: values.customers ?? [],
        supply_chain: supplyChain,
      }
      const res = await profileApi.save(payload)
      setRadar(res.radar)
      setTracks(res.recommended_tracks)
      setBoundary(res.boundary)
      setVectorDim(res.vector_dim)
      setCurrentId(res.id)
      setSavedAt(res.updated_at ?? null)
      refreshList()
      message.success(
        `${res.created ? '已新建' : '已更新'}「${res.name}」的能力画像，能力向量 ${res.vector_dim} 维`,
      )
    } catch (e) {
      // 保存失败此前是静默的（无 catch）—— 必须让用户看到
      const msg = e instanceof Error ? e.message : String(e)
      setSwitchError(`保存失败：${msg}`)
      message.error(`保存失败：${msg}`)
    } finally {
      setSaving(false)
    }
  }

  const toOpportunityPool = async () => {
    const res = await trendApi.scan({ industry: form.getFieldValue('industry') })
    message.success(`扫描 ${res.scanned_signals} 条信号，生成 ${res.generated} 条趋势`)
    navigate('/trends')
  }

  const radarData = Object.entries(radar ?? {}).map(([name, value]) => ({ name, value }))

  return (
    <>
      <PageHead
        title="公司能力画像"
        desc="输入技术栈、专利、人才、资金、渠道与客户资源，系统生成能力向量与可进入赛道边界。赛道推荐必须与公司能力匹配，而非单纯追风口。"
        extra={
          <Space>
            <Button icon={<SyncOutlined />} onClick={toOpportunityPool}>
              按画像开始扫描
            </Button>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
              保存并重新生成
            </Button>
          </Space>
        }
      />

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap align="center" size={12}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>当前公司</span>
          <Select
            style={{ minWidth: 280 }}
            showSearch
            allowClear
            optionFilterProp="label"
            loading={switching}
            placeholder={
              currentId ? '切换公司'
                : companies.length ? '选择已有公司画像' : '暂无已保存公司'
            }
            value={companies.some((c) => c.id === currentId) ? currentId! : undefined}
            onChange={(v) => (v ? switchCompany(v) : createNew())}
            options={companies.map((c) => ({
              value: c.id,
              label: `${c.name}（${c.industry ?? '未填行业'}）`,
            }))}
          />
          <Button icon={<PlusOutlined />} onClick={createNew}>
            新建公司
          </Button>
          {currentId && savedAt ? (
            <Tag color="blue" className="tc-tag-soft">
              已保存 · {savedAt.replace('T', ' ').slice(0, 16)}
            </Tag>
          ) : (
            <Tag className="tc-tag-soft">未保存（新建中）</Tag>
          )}
          <span className="tc-muted" style={{ fontSize: 12 }}>
            共 {companies.length} 家公司；趋势分析 / 评分 / 报告均以「当前公司」为主体
          </span>
        </Space>
        {switchError || loadError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 8 }}
            message={switchError ?? `画像加载失败：${loadError}`}
            action={
              <Button
                size="small"
                onClick={() => {
                  setSwitchError(null)
                  reloadProfile()
                  refreshList()
                }}
              >
                重试
              </Button>
            }
          />
        ) : null}
      </Card>

      <Steps
        current={step}
        onChange={setStep}
        size="small"
        style={{ marginBottom: 20 }}
        items={[
          { title: '基本信息' },
          { title: '技术栈' },
          { title: '专利与人才' },
          { title: '资金与渠道' },
        ]}
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card size="small" loading={loading}>
            <Form form={form} layout="vertical" initialValues={{ talents: {}, tech_stack: [], patents: [] }}>
              <div style={{ display: step === 0 ? 'block' : 'none' }}>
                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item label="公司名称" name="name" rules={[{ required: true, message: '请输入公司名称' }]}>
                      <Input placeholder="某某科技" />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="行业" name="industry">
                      <Select allowClear options={INDUSTRIES.map((v) => ({ value: v, label: v }))} placeholder="选择主营行业" />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="成立时间" name="founded_at">
                      <Input placeholder="2020-03" />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="规模" name="scale">
                      <Select allowClear options={SCALES.map((v) => ({ value: v, label: v }))} />
                    </Form.Item>
                  </Col>
                </Row>
              </div>

              <div style={{ display: step === 1 ? 'block' : 'none' }}>
                <Form.Item label="技术栈（回车添加）" name="tech_stack">
                  <Select mode="tags" placeholder="计算机视觉 / 深度学习 / 边缘计算" tokenSeparators={[',', '，']} />
                </Form.Item>
                <Form.Item label="核心技术描述" name="tech_description">
                  <Input.TextArea rows={4} placeholder="描述自研算法、产品与交付能力" />
                </Form.Item>
                <Upload.Dragger
                  accept=".txt,.md,.json,.csv"
                  showUploadList={false}
                  beforeUpload={async (file) => {
                    try {
                      const res = await profileApi.parse(file as File)
                      if (!res.parsed) {
                        message.warning(res.reason ?? '无法解析该文件，请手工填写')
                      } else {
                        const sug = res.suggestion ?? {}
                        form.setFieldsValue({
                          tech_stack: sug.tech_stack ?? [],
                          tech_description: sug.tech_description ?? '',
                          patents: sug.patents ?? [],
                        })
                        message.success('已自动解析并填入，请人工确认')
                      }
                    } catch {
                      message.error('解析失败')
                    }
                    return Upload.LIST_IGNORE
                  }}
                  style={{ padding: 12 }}
                >
                  <p className="ant-upload-drag-icon" style={{ margin: 0 }}>
                    <InboxOutlined />
                  </p>
                  <p className="ant-upload-text" style={{ fontSize: 13 }}>
                    上传公司简介 / 技术文档，自动抽取能力标签（纯文本）
                  </p>
                  <p className="ant-upload-hint" style={{ fontSize: 12 }}>
                    PDF / Word 二进制文件暂不支持直接解析，请粘贴文本或手工填写
                  </p>
                </Upload.Dragger>
              </div>

              <div style={{ display: step === 2 ? 'block' : 'none' }}>
                <Form.Item label="专利清单（回车添加）" name="patents">
                  <Select mode="tags" placeholder="输入专利名称或公开号" tokenSeparators={[',', '，']} />
                </Form.Item>
                <Row gutter={12}>
                  {['算法', '工程', '产品', '销售'].map((k) => (
                    <Col span={6} key={k}>
                      <Form.Item label={`${k}人数`} name={['talents', k]}>
                        <InputNumber min={0} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                  ))}
                </Row>
              </div>

              <div style={{ display: step === 3 ? 'block' : 'none' }}>
                <Row gutter={16}>
                  <Col span={12}>
                    <Form.Item label="融资阶段" name="funding_round">
                      <Select
                        allowClear
                        options={['天使', 'A轮', 'B轮', 'C轮', 'pre-ipo', '已上市'].map((v) => ({ value: v, label: v }))}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="累计融资额（元）" name="funding_amount">
                      <InputNumber min={0} step={1000000} style={{ width: '100%' }} />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item label="渠道" name="channels">
                  <Select mode="tags" placeholder="直销 / 系统集成商 / 线上" tokenSeparators={[',', '，']} />
                </Form.Item>
                <Form.Item label="客户行业" name="customers">
                  <Select mode="tags" placeholder="汽车零部件 / 3C 制造 / 新能源电池" tokenSeparators={[',', '，']} />
                </Form.Item>
              </div>
            </Form>

            <Space style={{ marginTop: 8 }}>
              <Button disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
                上一步
              </Button>
              <Button disabled={step === 3} onClick={() => setStep((s) => s + 1)}>
                下一步
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={9}>
          <Card
            title="能力雷达图"
            size="small"
            style={{ marginBottom: 16 }}
            extra={
              vectorDim ? (
                <Tag color="blue" className="tc-tag-soft">
                  向量 {vectorDim} 维
                </Tag>
              ) : null
            }
          >
            {radarData.length ? (
              <Radar data={radarData} max={5} height={280} name="能力" />
            ) : (
              <Empty description="保存后生成能力雷达" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>

          <Card title="推荐赛道边界" size="small" style={{ marginBottom: 16 }}>
            {tracks.length === 0 ? (
              <Empty description="保存后生成推荐赛道" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Space wrap>
                  {tracks.map((t) => (
                    <Tag key={t} color="green" className="tc-tag-soft">
                      {t}
                    </Tag>
                  ))}
                </Space>
                {boundary?.note ? (
                  <Alert type="info" showIcon message={String(boundary.note)} />
                ) : null}
              </Space>
            )}
          </Card>

          <Card title="赛道库" size="small">
            <List
              size="small"
              dataSource={trackData?.tracks ?? []}
              renderItem={(t) => (
                <List.Item>
                  <List.Item.Meta
                    title={<span style={{ fontSize: 13 }}>{t.name}</span>}
                    description={
                      <Typography12>{t.description}</Typography12>
                    }
                  />
                  <Tag className="tc-tag-soft">{t.signal_count} 信号</Tag>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </>
  )
}

function Typography12({ children }: { children: React.ReactNode }) {
  return (
    <span className="tc-muted" style={{ fontSize: 12 }}>
      {children}
    </span>
  )
}
