import type { ReactNode } from 'react'

interface Props {
  title: string
  desc?: ReactNode
  extra?: ReactNode
  page?: string
}

/** 页面标题区：标题 + 说明 + 右侧操作，统一各页头部节奏 */
export default function PageHead({ title, desc, extra }: Props) {
  return (
    <div className="tc-page-head">
      <div style={{ minWidth: 260 }}>
        <h2 className="tc-page-title">{title}</h2>
        {desc ? <p className="tc-page-desc">{desc}</p> : null}
      </div>
      {extra ? <div>{extra}</div> : null}
    </div>
  )
}
