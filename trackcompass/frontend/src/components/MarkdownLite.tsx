import { type ReactNode } from 'react'
import { Space, Typography } from 'antd'

/**
 * 轻量 Markdown 渲染：只处理报告模板实际用到的语法
 * （标题、粗体、列表、表格、引用、链接），零第三方依赖。
 * 如需完整语法可替换为 react-markdown。
 */
export default function ReactMarkdownLite({ text }: { text: string }) {
  const blocks = parseBlocks(text)
  return <div>{blocks}</div>
}

function parseBlocks(text: string): ReactNode[] {
  const lines = (text || '').split('\n')
  const out: ReactNode[] = []
  let table: string[] = []
  let list: string[] = []
  let key = 0

  const flushTable = () => {
    if (table.length === 0) return
    const rows = table
      .filter((l) => !/^\s*\|?\s*-{2,}/.test(l))
      .map((l) => l.split('|').map((c) => c.trim()).filter((c, i, arr) => !(i === 0 && c === '') && !(i === arr.length - 1 && c === '')))
    if (rows.length === 0) {
      table = []
      return
    }
    const [head, ...body] = rows
    out.push(
      <div style={{ overflowX: 'auto', margin: '8px 0' }} key={`t${key++}`}>
        <table
          style={{
            borderCollapse: 'collapse',
            width: '100%',
            fontSize: 13,
          }}
        >
          <thead>
            <tr>
              {head.map((c, i) => (
                <th
                  key={i}
                  style={{
                    border: '1px solid #E2E8F0',
                    background: '#F1F5F9',
                    padding: '6px 10px',
                    textAlign: 'left',
                    fontWeight: 600,
                  }}
                >
                  {inline(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, ri) => (
              <tr key={ri}>
                {row.map((c, ci) => (
                  <td
                    key={ci}
                    style={{ border: '1px solid #E2E8F0', padding: '6px 10px', verticalAlign: 'top' }}
                  >
                    {inline(c)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    )
    table = []
  }

  const flushList = () => {
    if (list.length === 0) return
    out.push(
      <ul key={`l${key++}`} style={{ margin: '6px 0', paddingLeft: 20 }}>
        {list.map((item, i) => (
          <li key={i} style={{ marginBottom: 4, fontSize: 13, lineHeight: '22px' }}>
            {inline(item)}
          </li>
        ))}
      </ul>,
    )
    list = []
  }

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '')

    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushList()
      table.push(line)
      continue
    }
    flushTable()

    if (/^\s*[-*]\s+/.test(line)) {
      const content = line.replace(/^\s*[-*]\s+/, '')
      if (/^\[[ x]\]/.test(content)) {
        // 任务列表按普通列表处理
      }
      list.push(content)
      continue
    }
    flushList()

    if (line.trim() === '') continue

    if (/^###\s+/.test(line)) {
      out.push(
        <h4 key={key++} style={{ fontSize: 14, margin: '12px 0 6px' }}>
          {inline(line.replace(/^###\s+/, ''))}
        </h4>,
      )
    } else if (/^##\s+/.test(line)) {
      out.push(
        <h3 key={key++} style={{ fontSize: 16, margin: '14px 0 6px' }}>
          {inline(line.replace(/^##\s+/, ''))}
        </h3>,
      )
    } else if (/^#\s+/.test(line)) {
      out.push(
        <h2 key={key++} style={{ fontSize: 18, margin: '16px 0 8px' }}>
          {inline(line.replace(/^#\s+/, ''))}
        </h2>,
      )
    } else if (/^>\s?/.test(line)) {
      out.push(
        <div
          key={key++}
          style={{
            borderLeft: '3px solid #2563EB',
            background: '#F8FAFC',
            padding: '8px 12px',
            margin: '8px 0',
            fontSize: 13,
          }}
        >
          {inline(line.replace(/^>\s?/, ''))}
        </div>,
      )
    } else if (/^[\s]*\*[^*].*\*[\s]*$/.test(line)) {
      out.push(
        <Typography.Paragraph key={key++} className="tc-muted" style={{ fontSize: 12, margin: '6px 0' }}>
          {inline(line.replace(/^\*|\*$/g, ''))}
        </Typography.Paragraph>,
      )
    } else {
      out.push(
        <p key={key++} style={{ fontSize: 13, lineHeight: '22px', margin: '6px 0' }}>
          {inline(line)}
        </p>,
      )
    }
  }

  flushTable()
  flushList()
  return out
}

/** 行内语法：**粗体**、`代码`、[文本](链接) */
function inline(text: string): ReactNode {
  const nodes: ReactNode[] = []
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g
  const parts = text.split(pattern).filter((p) => p !== undefined && p !== '')
  parts.forEach((part, i) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) {
      nodes.push(<strong key={i}>{part.slice(2, -2)}</strong>)
    } else if (/^`[^`]+`$/.test(part)) {
      nodes.push(
        <code
          key={i}
          className="tc-mono"
          style={{ background: '#F1F5F9', padding: '1px 4px', borderRadius: 4 }}
        >
          {part.slice(1, -1)}
        </code>,
      )
    } else if (/^\[[^\]]+\]\([^)]+\)$/.test(part)) {
      const m = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part)!
      nodes.push(
        <a key={i} href={m[2]} target="_blank" rel="noreferrer">
          {m[1]}
        </a>,
      )
    } else {
      nodes.push(<span key={i}>{part}</span>)
    }
  })
  return <Space size={0} wrap>{nodes}</Space>
}
