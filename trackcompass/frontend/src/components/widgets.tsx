import { Card, Empty, Progress, Space, Tag, Typography } from 'antd'

interface Props {
  title: string
  description?: string
  actionText?: string
  onAction?: () => void
  children?: React.ReactNode
}

/**
 * 信号类型中文名（与后端 `api/v1/signals.py::DATA_SOURCES` 保持一致）。
 *
 * 放在这里而不是各页各写一份：数据源名称出现在信号库、需求探针两处，
 * 各写一份必然漂移（同一概念多个入口 —— 见工作区约定 19）。
 */
export const SIGNAL_TYPE_LABEL: Record<string, string> = {
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

/**
 * 时间戳 → `MM-DD HH:mm`。
 *
 * 用于给「上一次分析/扫描/探针」的结果打时间标签：结果会跨路由保留
 * （见 `hooks/usePersistentState`），不标时间就会被误读成刚跑出来的新结果。
 */
export function formatClock(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/**
 * 把任意值安全地转成可渲染文本。
 *
 * 为什么必须存在
 * --------------
 * 模型/接口的「自由结构」字段随时可能从字符串漂移成对象
 * （实测：LLM 对「建议验证实验」返回 `{hypothesis, target_pain_point, metric}`，
 * 前端 `{e}` 直接渲染 ⇒ React 抛 `Objects are not valid as a React child`
 * 并**卸载整棵树 ⇒ 整页白屏**）。
 *
 * 规则：字符串原样；对象按常见文本键取值，取不到就把字符串值拼起来；
 * 其余 `String()`。**永不返回对象** —— 这是渲染层的最低保证。
 */
export function safeText(e: unknown): string {
  if (typeof e === 'string') return e
  if (e === null || e === undefined) return ''
  if (typeof e === 'number' || typeof e === 'boolean') return String(e)
  if (typeof e === 'object') {
    const o = e as Record<string, unknown>
    for (const k of ['hypothesis', 'title', 'name', 'text', 'label', 'message',
                     'description', 'method', 'action', 'value']) {
      const v = o[k]
      if (typeof v === 'string' && v.trim()) return v.trim()
      if (typeof v === 'number') return String(v)
    }
    const parts = Object.values(o).filter(
      (v): v is string => typeof v === 'string' && v.trim().length > 0,
    )
    if (parts.length) return parts.join('；')
    return ''   // 结构完全不认识：宁可留空，也不把对象交给 React
  }
  return String(e)
}

/**
 * 风险预警条目 → 可渲染的 `{level, title, detail}`。
 *
 * 为什么需要：**旧数据里该字段是纯字符串数组**（真实大模型早期产出的是叙事性风险长句），
 * 而界面按 `{title, detail}` 渲染 ⇒ 两个字段都取到 `undefined`，
 * 页面只剩「有图标、无文字」的黄色空卡（`docs/manual_shots/p07-scorecard.png` 修复前即如此）。
 * 后端已归一（`agents/base.py::normalize_risk_alerts`），此处兜底历史缓存与旧库数据 ——
 * 与 `safeText` 同一原则：**渲染层不该因为数据形态变化而显示空白**。
 */
export function riskItem(raw: unknown): { level: string; title: string; detail: string } {
  if (typeof raw === 'string') {
    return { level: 'warning', title: '风险提示', detail: raw }
  }
  if (raw && typeof raw === 'object') {
    const o = raw as Record<string, unknown>
    const pick = (...keys: string[]) => {
      for (const k of keys) {
        const v = o[k]
        if (typeof v === 'string' && v.trim()) return v.trim()
      }
      return ''
    }
    const title = pick('title', 'name')
    const detail = pick('detail', 'description', 'message', 'text')
    const level = pick('level') === 'critical' ? 'critical' : 'warning'
    return { level, title: title || detail.slice(0, 24) || '风险提示', detail: detail || title }
  }
  return { level: 'warning', title: '风险提示', detail: safeText(raw) }
}

/** 空状态：插图 + 引导文案 + 主行动按钮（《原型设计》§4.2） */
export function EmptyState({ title, description, actionText, onAction, children }: Props) {
  return (
    <div className="tc-empty">
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <Space direction="vertical" size={4}>
            <Typography.Text strong>{title}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {description}
            </Typography.Text>
          </Space>
        }
      >
        {actionText ? (
          <a onClick={onAction} style={{ fontSize: 13 }}>
            {actionText}
          </a>
        ) : null}
        {children}
      </Empty>
    </div>
  )
}

/** 指标进度条：指标值 vs 目标值（《原型设计》§5.8） */
export function MetricBar({
  label,
  current,
  target,
  unit,
  achievement,
}: {
  label: string
  current?: number
  target?: number
  unit?: string
  achievement?: number | null
}) {
  const ratio =
    achievement != null && Number.isFinite(achievement)
      ? Math.min(150, achievement * 100)
      : 0
  const status = ratio >= 100 ? 'success' : ratio >= 70 ? 'active' : 'exception'

  const fmt = (v?: number) => {
    if (v == null) return '—'
    if (unit === '%') return `${(v * 100).toFixed(1)}%`
    return `${v}${unit ?? ''}`
  }

  return (
    <div style={{ marginBottom: 12 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <Typography.Text style={{ fontSize: 13 }}>{safeText(label)}</Typography.Text>
        <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
          {fmt(current)} / 目标 {fmt(target)}
          {achievement != null ? (
            <Tag
              style={{ marginLeft: 8 }}
              color={status === 'success' ? 'success' : status === 'exception' ? 'error' : 'processing'}
              className="tc-tag-soft"
            >
              {(achievement * 100).toFixed(1)}%
            </Tag>
          ) : null}
        </Typography.Text>
      </Space>
      <Progress percent={Math.min(100, ratio)} status={status} showInfo={false} size="small" />
    </div>
  )
}

/** 排序对比条形（《原型设计》§5.7 排序对比） */
export function RankingBars({
  items,
  onSelect,
  colorFor,
}: {
  items: Array<{ id: string; title: string; total_score: number; rank: number }>
  onSelect?: (id: string) => void
  colorFor?: (score: number) => string
}) {
  const max = Math.max(5, ...items.map((i) => i.total_score))
  if (items.length === 0) {
    return <Empty description="暂无已评分的机会" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }
  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      {items.map((item) => {
        const width = (item.total_score / max) * 100
        const color = colorFor
          ? colorFor(item.total_score)
          : item.total_score >= 4.2
            ? '#10B981'
            : item.total_score >= 3.5
              ? '#2563EB'
              : item.total_score >= 2.8
                ? '#F59E0B'
                : '#94A3B8'
        return (
          <div
            key={item.id}
            onClick={() => onSelect?.(item.id)}
            style={{ cursor: onSelect ? 'pointer' : 'default' }}
          >
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Typography.Text style={{ fontSize: 13 }}>
                {item.rank}. {safeText(item.title)}
              </Typography.Text>
              <Typography.Text strong style={{ fontSize: 13, color }}>
                {item.total_score.toFixed(2)}
              </Typography.Text>
            </Space>
            <div
              style={{
                height: 8,
                borderRadius: 4,
                background: '#F1F5F9',
                marginTop: 4,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${width}%`,
                  height: '100%',
                  borderRadius: 4,
                  background: color,
                  transition: 'width .4s ease',
                }}
              />
            </div>
          </div>
        )
      })}
    </Space>
  )
}

/** 统计卡（《原型设计》§5.2 工作台统计卡片） */
export function StatCard({
  label,
  value,
  suffix,
  icon,
  color = '#2563EB',
  onClick,
}: {
  label: string
  value: number | string
  suffix?: string
  icon?: React.ReactNode
  color?: string
  onClick?: () => void
}) {
  return (
    <Card
      size="small"
      hoverable={Boolean(onClick)}
      onClick={onClick}
      styles={{ body: { padding: '16px 18px' } }}
    >
      <Space direction="vertical" size={2} style={{ width: '100%' }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Typography.Text className="tc-muted" style={{ fontSize: 13 }}>
            {safeText(label)}
          </Typography.Text>
          <span style={{ color, fontSize: 16 }}>{icon}</span>
        </Space>
        <div className="tc-stat-value" style={{ color }}>
          {safeText(value)}
          {suffix ? (
            <span style={{ fontSize: 13, color: '#64748B', marginLeft: 4 }}>{safeText(suffix)}</span>
          ) : null}
        </div>
      </Space>
    </Card>
  )
}
