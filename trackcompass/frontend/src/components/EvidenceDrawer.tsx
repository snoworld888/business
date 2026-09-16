import { Drawer, Empty, List, Space, Tag, Typography } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import type { Evidence } from '../api/types'

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
  news: '新闻',
  supply: '供应链',
}

const TYPE_COLOR: Record<string, string> = {
  policy: 'blue',
  patent: 'purple',
  paper: 'geekblue',
  funding: 'gold',
  recruit: 'cyan',
  social: 'orange',
  review: 'volcano',
  forum: 'magenta',
  bidding: 'green',
  news: 'default',
}

interface Props {
  open: boolean
  onClose: () => void
  evidence: Evidence[]
  title?: string
}

/** 证据链下钻面板（《原型设计》§4.2「点击结论弹出证据面板，右侧抽屉」） */
export default function EvidenceDrawer({ open, onClose, evidence, title = '证据链' }: Props) {
  return (
    <Drawer
      title={
        <Space>
          <span>{title}</span>
          <Tag className="tc-tag-soft">{evidence.length} 条</Tag>
        </Space>
      }
      placement="right"
      width={560}
      open={open}
      onClose={onClose}
    >
      {evidence.length === 0 ? (
        <Empty description="暂无证据，请先执行分析" />
      ) : (
        <List
          dataSource={evidence}
          renderItem={(item) => (
            <div className="tc-evidence">
              <div style={{ marginBottom: 4 }}>
                <span className="tc-evidence-ref">{item.ref}</span>
                <Tag color={TYPE_COLOR[item.signal_type] ?? 'default'} className="tc-tag-soft">
                  {TYPE_LABEL[item.signal_type] ?? item.signal_type}
                </Tag>
                <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
                  {item.source}
                </Typography.Text>
              </div>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>{item.title}</div>
              <Typography.Paragraph
                className="tc-muted"
                style={{ fontSize: 12, marginBottom: 6 }}
                ellipsis={{ rows: 3, expandable: true, symbol: '展开' }}
              >
                {item.excerpt || '—'}
              </Typography.Paragraph>
              <Space size={12}>
                {item.url ? (
                  <a href={item.url} target="_blank" rel="noreferrer">
                    <LinkOutlined /> 查看原文
                  </a>
                ) : null}
                <Typography.Text className="tc-mono tc-muted">
                  signal: {item.signal_id?.slice(0, 8)}
                </Typography.Text>
                {item.score ? (
                  <Typography.Text className="tc-mono tc-muted">
                    相关度 {item.score.toFixed(4)}
                  </Typography.Text>
                ) : null}
              </Space>
            </div>
          )}
        />
      )}
    </Drawer>
  )
}
