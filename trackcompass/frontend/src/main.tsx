import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'

import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './styles/tokens.css'

dayjs.locale('zh-cn')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#2563EB',
          colorSuccess: '#10B981',
          colorWarning: '#F59E0B',
          colorError: '#EF4444',
          colorBgLayout: '#F8FAFC',
          colorText: '#0F172A',
          colorTextSecondary: '#64748B',
          colorBorder: '#E2E8F0',
          borderRadius: 8,
          fontSize: 14,
          fontFamily:
            "-apple-system, 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', sans-serif",
        },
        components: {
          Card: { borderRadiusLG: 12, boxShadowTertiary: '0 1px 3px rgba(0,0,0,0.08)' },
          Table: { headerBg: '#F1F5F9' },
        },
      }}
    >
      <AntApp>
        <HashRouter>
          {/* 顶层兜底：连 AppLayout 都没渲染出来时（例如布局自身抛错）也能给出可读提示，
              而不是一片空白 */}
          <ErrorBoundary fullPage>
            <App />
          </ErrorBoundary>
        </HashRouter>
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
)
