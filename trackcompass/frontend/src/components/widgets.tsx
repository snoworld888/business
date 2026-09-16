import { Card, Empty, Progress, Space, Tag, Typography } from 'antd'

interface Props {
  title: string
  description?: string
  actionText?: string
  onAction?: () => void
  children?: React.ReactNode
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
        <Typography.Text style={{ fontSize: 13 }}>{label}</Typography.Text>
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
                {item.rank}. {item.title}
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
            {label}
          </Typography.Text>
          <span style={{ color, fontSize: 16 }}>{icon}</span>
        </Space>
        <div className="tc-stat-value" style={{ color }}>
          {value}
          {suffix ? (
            <span style={{ fontSize: 13, color: '#64748B', marginLeft: 4 }}>{suffix}</span>
          ) : null}
        </div>
      </Space>
    </Card>
  )
}
