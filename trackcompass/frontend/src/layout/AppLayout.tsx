import { type ReactNode, useMemo } from 'react'
import { Avatar, Badge, Dropdown, Layout, Menu, Space, Tag, Tooltip, Typography } from 'antd'
import {
  AppstoreOutlined,
  BellOutlined,
  CompassOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  FundProjectionScreenOutlined,
  HeatMapOutlined,
  LoadingOutlined,
  LogoutOutlined,
  ProfileOutlined,
  RadarChartOutlined,
  SettingOutlined,
  TeamOutlined,
  ExperimentOutlined,
} from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { monitorApi } from '../api/client'
import { useRunningTasks } from '../api/taskStore'
import { useAsync, useAuth } from '../hooks/useAuth'

const { Header, Sider, Content } = Layout

/** 信息架构严格对齐《原型设计》§2 与页面清单 §11.1 */
const NAV = [
  { key: '/dashboard', icon: <DashboardOutlined />, label: '工作台', page: 'P02' },
  { key: '/profile', icon: <ProfileOutlined />, label: '能力画像', page: 'P03' },
  // P12 为信号层补充页：《设计文档》§12 的十页清单里没有它，但 §5.3
  // 验收标准「每个赛道评分可下钻到至少 3 类证据来源」要求原始信号可见 ——
  // 此前 40 条信号在前端没有任何入口，本页补上这个断点。
  { key: '/signals', icon: <DatabaseOutlined />, label: '信号库', page: 'P12' },
  { key: '/trends', icon: <RadarChartOutlined />, label: '趋势雷达', page: 'P04' },
  { key: '/graph', icon: <HeatMapOutlined />, label: '机会图谱', page: 'P05' },
  { key: '/demand', icon: <FundProjectionScreenOutlined />, label: '需求探针', page: 'P06' },
  { key: '/scorecard', icon: <AppstoreOutlined />, label: '赛道评分', page: 'P07' },
  { key: '/experiments', icon: <ExperimentOutlined />, label: '验证实验', page: 'P08' },
  { key: '/reports', icon: <FileTextOutlined />, label: '决策报告', page: 'P09' },
  { key: '/monitor', icon: <BellOutlined />, label: '监控复盘', page: 'P10' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置', page: 'P11' },
]

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  strategy: '战略部',
  product: '产品部',
  investment: '投资部',
  expert: '领域专家',
}

export default function AppLayout({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()
  const { data: alerts } = useAsync(() => monitorApi.list({ status: 'pending' }), [])
  /** 正在后台跑的任务（趋势扫描 / 需求探针 / 七 Agent 分析…）——
   *  放在顶栏是为了让用户敢离开页面：知道活还在跑、跑完了会通知。 */
  const running = useRunningTasks()

  const selectedKey = useMemo(() => {
    const hit = NAV.find((n) => location.pathname.startsWith(n.key))
    return hit?.key ?? '/dashboard'
  }, [location.pathname])

  const pendingCount = alerts?.total ?? 0

  return (
    <Layout className="tc-layout">
      <Sider className="tc-sider" width={208} breakpoint="lg" collapsedWidth={64}>
        <div className="tc-logo">
          <span className="tc-logo-mark">
            <CompassOutlined />
          </span>
          <span>赛道罗盘</span>
        </div>
        <Menu
          mode="inline"
          theme="dark"
          selectedKeys={[selectedKey]}
          onClick={({ key }) => navigate(key)}
          items={NAV.map((n) => ({
            key: n.key,
            icon: n.icon,
            label: (
              <Space size={6}>
                <span>{n.label}</span>
                {n.key === '/monitor' && pendingCount > 0 ? (
                  <Badge count={pendingCount} size="small" />
                ) : null}
              </Space>
            ),
          }))}
        />
      </Sider>

      <Layout>
        <Header className="tc-header">
          <Typography.Text strong style={{ fontSize: 15 }}>
            {NAV.find((n) => n.key === selectedKey)?.label}
          </Typography.Text>
          <Tag color="blue" className="tc-tag-soft">
            七 Agent 决策流水线
          </Tag>
          {running.length ? (
            <Tooltip
              title={
                <Space direction="vertical" size={2}>
                  {running.map((t) => (
                    <span key={t.key}>
                      · {t.meta.page} · {t.meta.title}
                    </span>
                  ))}
                </Space>
              }
            >
              <Tag color="processing" className="tc-tag-soft" icon={<LoadingOutlined />}>
                {running.length} 个任务后台运行中
              </Tag>
            </Tooltip>
          ) : null}
          <div style={{ flex: 1 }} />
          <Space size={16}>
            <Badge count={pendingCount} size="small">
              <BellOutlined
                style={{ fontSize: 16, cursor: 'pointer' }}
                onClick={() => navigate('/monitor')}
              />
            </Badge>
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'logout',
                    icon: <LogoutOutlined />,
                    label: '退出登录',
                    onClick: () => {
                      logout()
                      navigate('/login')
                    },
                  },
                ],
              }}
            >
              <Space style={{ cursor: 'pointer' }}>
                <Avatar size={30} style={{ background: '#2563EB' }}>
                  <TeamOutlined />
                </Avatar>
                <span>
                  {user?.name || user?.email}
                  <Tag style={{ marginLeft: 8 }} className="tc-tag-soft">
                    {ROLE_LABEL[user?.role ?? ''] ?? user?.role}
                  </Tag>
                </span>
              </Space>
            </Dropdown>
          </Space>
        </Header>

        <Content>
          <div className="tc-content">{children}</div>
        </Content>
      </Layout>
    </Layout>
  )
}
