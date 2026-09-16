import { useCallback, useEffect, useRef, useState } from 'react'
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
import { formatClock } from '../components/widgets'
import { runTask } from '../api/taskStore'
import { profileApi, trendApi } from '../api/client'
import { useAsync } from '../hooks/useAuth'
import { usePersistentState } from '../hooks/usePersistentState'
import type { CompanyProfile, CompanyProfileSummary, TrendScanResult } from '../api/types'

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

/** 草稿键：未选中任何公司时代表「正在新建」 */
const NEW_KEY = '__new__'

/**
 * 未保存草稿：**按编辑对象分别存**。
 *
 * 为什么按对象分而不是只存一份：用户可能在 A 公司改了技术栈、又切到 B 公司填专利，
 * 只存一份会互相覆盖。切回 A 时应该还能看到 A 那次的未保存修改。
 */
type DraftMap = Record<string, { at: string; values: Partial<FormValues> }>

/**
 * 上次离开本页时正在编辑谁 —— 决定重新进入时拉哪一份画像。
 *
 * 不记这个的话，回到页面永远拉「最近保存的那家公司」，
 * 用户刚在右上角选好的公司会**跳回去**（这就是「选好了再回来又变默认值」的根因之一）。
 */
type Editing = { kind: 'current' } | { kind: 'company'; id: string } | { kind: 'new' }

/** P03 公司能力画像（《原型设计》§5.3）
 *
 * 状态保持（2026-09-16）：用户报「公司/字段选好了，切别的页面再回来又变默认值」。
 * 根因有两层，都已修：
 *   ① 回到页面固定拉「最近保存的那家公司」⇒ 刚选好的公司会跳回去 → 记 `editing`
 *   ② 填了但没保存的内容只活在组件内存里，卸载即失 → 按公司存 `drafts`（未保存草稿）
 * 另外 Step 当前步也保留，不再每次弹回第一步。
 */
export default function Profile() {
  const [form] = Form.useForm<FormValues>()
  const navigate = useNavigate()
  const [step, setStep] = usePersistentState<number>('profile:step', 0)
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
  /** 上次离开时在编辑谁；未保存草稿；本次进入时**恢复出来的**草稿时间 */
  const [editing, setEditing] = usePersistentState<Editing>('profile:editing', { kind: 'current' })
  const [drafts, setDrafts] = usePersistentState<DraftMap>('profile:drafts', {})
  const [restoredAt, setRestoredAt] = useState<string | null>(null)
  const draftKey = currentId ?? NEW_KEY
  /** 正在切换中的公司 id —— 防止同一次切换被并发触发两次 */
  const switchingTo = useRef<string | null>(null)

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

  /* ------------------------------------------------ 未保存草稿 */
  /** 把当前表单值记为**当前编辑对象**的未保存草稿（表单任意改动 / 解析填入后调用） */
  const saveDraft = () => {
    setDrafts((prev) => ({
      ...prev,
      [draftKey]: { at: new Date().toISOString(), values: form.getFieldsValue() },
    }))
  }

  const dropDraft = (key: string) => {
    setDrafts((prev) => {
      if (!(key in prev)) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
    setRestoredAt(null)
  }

  /** 丢弃草稿：删掉草稿并把表单恢复成「已保存的画像」（新建态则清空） */
  const discardDraft = async () => {
    const key = draftKey
    dropDraft(key)
    if (key === NEW_KEY) {
      form.resetFields()
      message.info('已丢弃草稿')
      return
    }
    const p = await profileApi.get(key)
    if (p) applyProfile(p)
    message.info('已丢弃草稿，恢复为已保存的画像')
  }

  /**
   * 恢复「上次离开时正在编辑的对象」。
   *
   * 只在首次加载跑一次：否则会与用户的主动切换互相打架。
   * ⚠️ 必须等首屏画像拉取**落定**（`loading === false`）再判断：
   * `currentId` 在拉取完成前是 null，若名单先回来就急着切换，
   * 会在「后端给的当前公司恰好就是要恢复的那家」时**重复拉一次**（实测多出一条重复提示）。
   */
  const restoreDone = useRef(false)
  useEffect(() => {
    if (restoreDone.current || loading) return
    if (editing.kind === 'new') {
      restoreDone.current = true
      resetToNew({ clearDraft: false })
      return
    }
    if (editing.kind === 'company' && editing.id) {
      if (!listData) return // 等公司列表就绪，避免加载已被删除的公司
      restoreDone.current = true
      // 与后端给的当前公司一致就无需再切；名单里已经没有（被别处删掉）也保持现状
      if (editing.id !== currentId && companies.some((c) => c.id === editing.id)) {
        void switchCompany(editing.id, { silent: true })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, listData, companies, loading, currentId])

  /**
   * 载入某份画像后，用它的未保存草稿覆盖表单。
   *
   * 右侧面板（能力向量/推荐赛道/边界）**仍按已保存的画像**渲染 —— 草稿本来就还没落库，
   * 拿它去显示「能力向量」等于展示不存在的结果。
   */
  const draftAppliedFor = useRef<string | null>(null)
  useEffect(() => {
    if (draftAppliedFor.current === draftKey) return
    draftAppliedFor.current = draftKey
    const d = drafts[draftKey]
    if (!d) return
    form.setFieldsValue(d.values)
    setRestoredAt(d.at)
  }, [draftKey, drafts, form])

  /** 把表单与右侧面板切回「新建公司」的空态。`clearDraft=false` 用于首次恢复时保留草稿 */
  const resetToNew = ({ clearDraft }: { clearDraft: boolean }) => {
    form.resetFields()
    setRadar(null)
    setTracks([])
    setBoundary({})
    setVectorDim(null)
    setSupplyChain([])
    setCurrentId(null)
    setSavedAt(null)
    setSwitchError(null)
    setRestoredAt(null)
    if (clearDraft) dropDraft(NEW_KEY)
  }

  /**
   * 切换公司：按 id 拉取完整画像后回填。
   *
   * `silent` 用于「进入页面时恢复上次编辑对象」——那一步不需要弹提示，
   * 否则每次进页面都弹一条「已载入 X」，与用户主动切换的反馈混在一起。
   */
  const switchCompany = async (id: string, opts: { silent?: boolean } = {}) => {
    if (id === currentId || switchingTo.current === id) return
    switchingTo.current = id
    setSwitching(true)
    setSwitchError(null)
    setEditing({ kind: 'company', id })
    try {
      const p = await profileApi.get(id)
      if (!p) {
        setSwitchError('该公司画像已不存在，请点右侧刷新重试')
        refreshList()
        return
      }
      applyProfile(p)
      if (!opts.silent) message.success(`已载入「${p.name}」的能力画像`)
    } catch (e) {
      setSwitchError(e instanceof Error ? e.message : String(e))
    } finally {
      switchingTo.current = null
      setSwitching(false)
    }
  }

  /** 新建公司：清空表单与面板；保存时不带 id ⇒ 后端新建一份画像 */
  const createNew = () => {
    setStep(0)
    // 用户主动点了「新建公司」= 要从空白开始，因此把可能存在的草稿一并清掉；
    // 而「首次进入时恢复新建态」走 restore 分支，那条路径保留草稿。
    resetToNew({ clearDraft: true })
    setEditing({ kind: 'new' })
    message.info('已切换到「新建公司」，填写后保存即创建新档案')
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    /** 保存前的草稿键（新建态是 `__new__`，保存后会变成真实 id） */
    const keyBefore = currentId ?? NEW_KEY
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
      // 已落库 ⇒ 草稿使命结束（留着会让下次进入「恢复」出一份与后端相同的假草稿）
      dropDraft(keyBefore)
      setEditing({ kind: 'company', id: res.id })
      draftAppliedFor.current = res.id
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

  /**
   * 按画像开始扫描 —— 与「趋势雷达」页**共用同一个任务键**（点的是同一件事，不并发跑两遍）。
   * **不等待**：立刻跳到趋势雷达，扫描在后台继续；跑完弹全局通知、列表自动刷新。
   */
  const toOpportunityPool = () => {
    const industry = form.getFieldValue('industry')
    void runTask<TrendScanResult>(
      'trends:scan',
      { page: '趋势雷达', title: '趋势扫描', params: { industry, from: '能力画像' } },
      () => trendApi.scan({ industry }),
      { summarize: (r) => `扫描 ${r.scanned_signals} 条信号，生成 ${r.generated} 条趋势` },
    )
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
        {restoredAt ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 8 }}
            message={`已恢复未保存的草稿（${formatClock(restoredAt)} 的修改）`}
            description="下面表单里的内容是离开本页前填的、尚未保存的修改；右侧能力向量与推荐赛道仍按已保存的画像展示。"
            action={
              <Button size="small" onClick={discardDraft}>
                丢弃草稿
              </Button>
            }
          />
        ) : null}
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
            <Form
              form={form}
              layout="vertical"
              initialValues={{ talents: {}, tech_stack: [], patents: [] }}
              onValuesChange={saveDraft}
            >
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
                        // 自动填入的也是「未保存修改」：不记草稿的话，
                        // 用户传完文件切个页面回来就白填了
                        saveDraft()
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
