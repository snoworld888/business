import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button, Result, Space, Typography } from 'antd'
import { useLocation } from 'react-router-dom'

/**
 * 渲染错误边界。
 *
 * 为什么必须有
 * ------------
 * React 遇到渲染错误会**卸载整棵子树**。此前没有边界时，一个字段类型不对
 * （实测：LLM 对「建议验证实验」返回对象，前端 `{e}` 直接渲染）就会让
 * **整个页面变成空白** —— 既没有提示、也不知道原因，排查时只能靠浏览器
 * 开发者工具，用户则完全无从下手。
 *
 * 有了边界之后：
 * - 错误被**限制在出错的那一块**（页面内容区），侧栏导航仍然可用；
 * - **把 `error.message` 显示出来** —— 空白页最大的问题不是崩溃，而是「什么都不知道」；
 * - 提供「重试」与「回工作台」两条出路。
 */
interface Props {
  children: ReactNode
  /** 全页模式：顶层兜底用（`AppLayout` 都没渲染出来时） */
  fullPage?: boolean
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 额外打一条带前缀的日志：自动化诊断脚本（CDP 抓 console）据此可直接定位边界
    console.error('[ErrorBoundary] 页面渲染失败：', error.message, info.componentStack)
  }

  private reset = () => this.setState({ error: null })

  private goHome = () => {
    window.location.hash = '#/dashboard'
    this.reset()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const detail = String(error.message || error)

    return (
      <Result
        status="error"
        title="这块内容渲染出错了"
        subTitle="其余功能仍可正常使用。若反复出现，请把下面的信息反馈给开发。"
        style={{
          padding: this.props.fullPage ? '80px 24px' : '24px',
          background: '#fff',
          borderRadius: 12,
        }}
        extra={
          <Space wrap>
            <Button type="primary" onClick={this.reset}>
              重试
            </Button>
            <Button onClick={this.goHome}>回到工作台</Button>
            <Button
              onClick={() => window.location.reload()}
              type="text"
              className="tc-muted"
            >
              刷新页面
            </Button>
          </Space>
        }
      >
        {/* 把错误原文亮出来：这正是「白屏」最缺的东西 —— 可判断的线索 */}
        <div
          style={{
            maxWidth: 720,
            margin: '0 auto',
            textAlign: 'left',
            background: '#FEF2F2',
            border: '1px solid #FECACA',
            borderRadius: 8,
            padding: '12px 16px',
          }}
        >
          <Typography.Text strong style={{ color: '#B91C1C' }}>
            错误信息
          </Typography.Text>
          <pre
            className="tc-mono"
            style={{
              margin: '8px 0 0',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 12,
              color: '#7F1D1D',
            }}
          >
            {detail}
          </pre>
        </div>
      </Result>
    )
  }
}

/**
 * 路由级错误边界：**随路由切换自动重置**。
 *
 * 用 `key={pathname}` 强制重建边界 —— 否则某页崩过一次之后，
 * 切到别的页面仍会看到错误页（边界状态不会自己清）。
 */
export function RouteBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  return <ErrorBoundary key={pathname}>{children}</ErrorBoundary>
}
