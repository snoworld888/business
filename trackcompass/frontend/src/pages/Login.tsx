import { useState } from 'react'
import { Alert, Button, Divider, Form, Input, Space, Typography, message } from 'antd'
import { CompassOutlined, LockOutlined, UserOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

/** P01 登录 / 注册（《原型设计》§5.1） */
export default function Login() {
  const navigate = useNavigate()
  const { login } = useAuth()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onFinish = async (values: { email: string; password: string }) => {
    setLoading(true)
    setError(null)
    try {
      const user = await login(values.email, values.password)
      message.success(`欢迎回来，${user.name || user.email}`)
      navigate('/dashboard', { replace: true })
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { message?: string } } })?.response?.data?.message ??
        '登录失败'
      setError(detail)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="tc-login-wrap">
      <div className="tc-login-card">
        <div className="tc-login-brand">
          <span
            className="tc-logo-mark"
            style={{ width: 46, height: 46, fontSize: 22, margin: '0 auto' }}
          >
            <CompassOutlined />
          </span>
          <h1>赛道罗盘</h1>
          <Typography.Text className="tc-muted">TrackCompass</Typography.Text>
          <div style={{ marginTop: 8 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              把「凭感觉选方向」变成可追溯的决策流水线
            </Typography.Text>
          </div>
        </div>

        {error ? (
          <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />
        ) : null}

        <Form layout="vertical" onFinish={onFinish} requiredMark={false}
              initialValues={{ email: 'admin@demo.com', password: 'TrackCompass@2026' }}>
          <Form.Item
            label="邮箱"
            name="email"
            rules={[{ required: true, message: '请输入邮箱' }, { type: 'email', message: '邮箱格式不正确' }]}
          >
            <Input size="large" prefix={<UserOutlined />} placeholder="you@company.com" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }, { min: 8, message: '密码至少 8 位' }]}
          >
            <Input.Password size="large" prefix={<LockOutlined />} placeholder="••••••••" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={loading}>
            登 录
          </Button>
        </Form>

        <Divider plain style={{ margin: '20px 0 12px' }}>
          <Typography.Text className="tc-muted" style={{ fontSize: 12 }}>
            演示账号已预填
          </Typography.Text>
        </Divider>
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          {[
            ['admin@demo.com', '管理员'],
            ['strategy@demo.com', '战略部'],
            ['product@demo.com', '产品部'],
            ['invest@demo.com', '投资部'],
            ['expert@demo.com', '领域专家'],
          ].map(([email, role]) => (
            <Typography.Text key={email} className="tc-mono tc-muted">
              {email} · {role} · TrackCompass@2026
            </Typography.Text>
          ))}
        </Space>
      </div>
    </div>
  )
}
