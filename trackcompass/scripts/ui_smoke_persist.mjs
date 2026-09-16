#!/usr/bin/env node
/**
 * UI 会话态冒烟：验证「切到别的菜单再回来，结果不能空白」。
 *
 * 背景
 * ----
 * 本应用用 React Router 切页，**路由一变页面组件即被卸载**，组件内 `useState` 全部归零。
 * 需求探针 / 趋势扫描 / 七 Agent 分析都是「用户手动触发、耗时数秒到数十秒」的动作，
 * 结果因此丢失，用户只能重跑。修复手段是 `hooks/usePersistentState`（sessionStorage）。
 * 本脚本就是那条修复的**可复现证据**：
 *   ① 跑一次需求探针 → 结果区出现
 *   ② 切走再切回        → 结果区仍在（不是「尚未执行分析」）
 *   ③ sessionStorage 里确实留下了 `tc:ui:*` 键
 *
 * 零依赖
 * ------
 * 用 Node 22 内置的 `fetch` + `WebSocket` 直连 Chromium 的 CDP，
 * 复用本机已装的 Edge / Chrome，**不下载任何浏览器、不装任何包**。
 *
 * 用法
 * ----
 *   node scripts/ui_smoke_persist.mjs           # 需后端已在 8020 运行
 *   TC_BASE=http://127.0.0.1:8020 node scripts/ui_smoke_persist.mjs
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = process.env.TC_BASE ?? 'http://127.0.0.1:8020'
const EMAIL = process.env.TC_EMAIL ?? 'admin@demo.com'
const PASSWORD = process.env.TC_PASSWORD ?? 'TrackCompass@2026'
const PORT = Number(process.env.TC_CDP_PORT ?? 9333)
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

const BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
]

/** 信号类型中文名（与前端 `components/widgets.tsx::SIGNAL_TYPE_LABEL` 对齐，断言用） */
const TYPE_LABEL = {
  policy: '政策', patent: '专利', paper: '论文', funding: '融资', recruit: '招聘',
  social: '社媒', review: '评论', forum: '论坛', bidding: '招标', news: '产业媒体', supply: '供应链',
}

const results = []

function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? `  — ${detail}` : ''}`)
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/* ------------------------------------------------------------------ CDP */

class Cdp {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
  }

  static async connect(url) {
    const ws = new WebSocket(url)
    await new Promise((resolve, reject) => {
      ws.onopen = resolve
      ws.onerror = () => reject(new Error(`无法连接 CDP: ${url}`))
    })
    const cdp = new Cdp(ws)
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      const slot = msg.id ? cdp.pending.get(msg.id) : undefined
      if (!slot) return
      cdp.pending.delete(msg.id)
      if (msg.error) slot.reject(new Error(msg.error.message))
      else slot.resolve(msg.result)
    }
    return cdp
  }

  send(method, params = {}, timeoutMs = 60_000) {
    const id = ++this.seq
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`CDP 超时：${method}`))
      }, timeoutMs)
    })
  }

  /** 在页面里求值；返回 JS 值（对象已结构化克隆） */
  async eval(expression) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    })
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.exception?.description ?? '页面脚本抛错')
    }
    return r.result.value
  }

  async goto(url, waitForText) {
    await this.send('Page.navigate', { url })
    await this.waitFor(`document.readyState === 'complete'`, 20_000)
    if (waitForText) await this.waitForText(waitForText, 25_000)
  }

  /** 轮询页面表达式直到为真 */
  async waitFor(expression, timeoutMs) {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      try {
        if (await this.eval(`!!(${expression})`)) return true
      } catch {
        /* 导航中执行上下文被销毁，重试即可 */
      }
      if (Date.now() > deadline) return false
      await sleep(300)
    }
  }

  async waitForText(text, timeoutMs) {
    const ok = await this.waitFor(
      `document.body && document.body.innerText.includes(${JSON.stringify(text)})`,
      timeoutMs,
    )
    if (!ok) {
      const where = await this.eval(`location.href`).catch(() => '?')
      const seen = await this.eval(`(document.body.innerText || '').slice(0, 300)`).catch(() => '')
      throw new Error(`等待页面文本超时：${text}\n  当前 URL：${where}\n  页面文本：${seen.replace(/\n/g, ' ')}`)
    }
    return true
  }

  /** 按可见文本点击元素（antd 的按钮/菜单项都是 DOM 元素，无需猜选择器） */
  async clickByText(selector, text) {
    const hit = await this.eval(`(() => {
      const el = [...document.querySelectorAll(${JSON.stringify(selector)})]
        .find(e => (e.innerText || e.textContent || '').trim().startsWith(${JSON.stringify(text)}))
      if (!el) return false
      el.click()
      return true
    })()`)
    if (!hit) throw new Error(`找不到可点击元素：${selector} 含「${text}」`)
  }

  close() {
    try {
      this.ws.close()
    } catch {
      /* 已关闭 */
    }
  }
}

/* ------------------------------------------------------------------ 工具 */

async function waitHttp(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    try {
      const r = await fetch(url)
      if (r.ok) return await r.json()
    } catch {
      /* 还没起来 */
    }
    if (Date.now() > deadline) throw new Error(`等待 ${url} 超时`)
    await sleep(300)
  }
}

async function api(path, token, init = {}) {
  const r = await fetch(`${BASE}/api/v1${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  })
  return (await r.json()).data
}

/* ------------------------------------------------------------------ 主流程 */

let proc
let cdp
let profileDir
let browserCdp

async function main() {
  const exe = BROWSERS.find((p) => existsSync(p))
  if (!exe) throw new Error('本机未找到 Chromium 内核浏览器（Chrome / Edge）')
  console.log(`浏览器：${exe}`)
  console.log(`目标：${BASE}\n`)

  profileDir = mkdtempSync(join(tmpdir(), 'tc-ui-smoke-'))
  proc = spawn(
    exe,
    [
      '--headless=new',
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profileDir}`,
      '--window-size=1600,1000',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-extensions',
      '--disable-gpu',
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  const version = await waitHttp(`http://127.0.0.1:${PORT}/json/version`, 25_000)
  check('浏览器启动（CDP 就绪）', Boolean(version && version.webSocketDebuggerUrl), version?.Browser ?? '')

  const pages = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
  let target = pages.find((t) => t.type === 'page' && t.webSocketDebuggerUrl)
  if (!target) {
    const r = await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })
    target = await r.json()
  }
  cdp = await Cdp.connect(target.webSocketDebuggerUrl)
  browserCdp = await Cdp.connect(version.webSocketDebuggerUrl)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1600,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  })

  /* ---- 1. 登录态注入 ---- */
  const auth = await api('/auth/login', null, {
    method: 'POST',
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  })
  check('接口登录成功', Boolean(auth?.access_token), `${auth?.user?.email ?? ''} / ${auth?.user?.role ?? ''}`)

  await cdp.goto(`${BASE}/`, '赛道罗盘')
  await cdp.eval(`(() => {
    localStorage.setItem('trackcompass_token', ${JSON.stringify(auth.access_token)})
    localStorage.setItem('trackcompass_user', ${JSON.stringify(JSON.stringify(auth.user))})
    return 'ok'
  })()`)
  const stored = await cdp.eval(
    `[localStorage.getItem('trackcompass_token') ? 'token' : 'no-token',
      localStorage.getItem('trackcompass_user') ? 'user' : 'no-user'].join('+')`,
  )
  check('登录态已写入 localStorage', stored === 'token+user', stored)

  /* ---- 2. 只预置关键词 ----
   * 数据源**故意不预置** —— 要验证的正是「① 默认值由页面自己补全为库内已有类型」。 */
  const pool = await api('/signals?limit=1', auth.access_token)
  const types = Object.keys(pool?.stats ?? {})
  console.log(`信号库存量：${types.map((t) => `${t}=${pool.stats[t]}`).join(' / ') || '空'}`)
  const keywords = ['大模型']
  await cdp.eval(`(() => {
    sessionStorage.removeItem('tc:ui:demand:sources')
    sessionStorage.setItem('tc:ui:demand:keywords', ${JSON.stringify(JSON.stringify(keywords))})
    return 'ok'
  })()`)

  /* ---- 3. 进入需求探针 ----
   * 顺序有讲究：先整页 reload 让 App 带着 localStorage 里的登录态重启，
   * 再改 hash 跳页。只改 hash 的话 App 的 `isAuthenticated`
   * 不会重新求值（它是在首屏渲染时读的 localStorage），会停在登录页。 */
  await cdp.send('Page.reload')
  await cdp.waitFor(`document.readyState === 'complete'`, 20_000)
  await sleep(1000)
  await cdp.eval(`location.hash = '#/demand'`)
  const diag = await cdp.eval(`(async () => {
    const t = localStorage.getItem('trackcompass_token')
    let status = 'n/a'
    try {
      const r = await fetch('/api/v1/auth/me', { headers: { Authorization: 'Bearer ' + t } })
      status = String(r.status)
    } catch (e) { status = 'err:' + e.message }
    return [location.href, t ? 'token-ok' : 'token-gone', 'me=' + status].join(' | ')
  })()`)
  await cdp.waitForText('开始分析', 30_000)
  const onDemand = await cdp.eval(`document.body.innerText.includes('需求探针')`)
  check('已进入需求探针页（登录态生效）', onDemand, diag)
  // 数据源默认值要等 /signals 返回后才补齐，先等选中项数量到位（含 1 个关键词标签）
  await cdp.waitFor(
    `document.querySelectorAll('.ant-select-selection-item').length >= ${types.length + 1}`,
    15_000,
  )
  const sourceTags = await cdp.eval(
    `[...document.querySelectorAll('.ant-select-selection-item')].map(e => e.innerText).join(' | ')`,
  )
  check('数据源下拉带存量计数', /（\d+）/.test(sourceTags), sourceTags.slice(0, 160))
  const labelOf = (t) => TYPE_LABEL[t] ?? t
  const missing = types.filter((t) => !sourceTags.includes(`${labelOf(t)}（`))
  check(
    '① 默认数据源已并入库内已有类型',
    types.length > 0 && missing.length === 0,
    `库内 ${types.map(labelOf).join('/')}；已选 ${sourceTags}`,
  )
  const demandSide = ['社媒', '论坛', '评论', '招标', '招聘']
  check('① 需求侧五类仍在默认选中里（不丢设计口径）',
    demandSide.every((l) => sourceTags.includes(l)))

  /* ---- 4. 跑一次探针 ---- */
  const t0 = Date.now()
  await cdp.clickByText('button', '开始分析')
  await cdp.waitForText('痛点聚类', 240_000)
  const ranMs = Date.now() - t0
  const afterRun = await cdp.eval(`document.body.innerText`)
  check('分析产出结果区', afterRun.includes('痛点聚类'), `耗时 ${(ranMs / 1000).toFixed(1)}s`)
  check('结果带「上次分析」条件与时间', afterRun.includes('上次分析'))
  const keys1 = await cdp.eval(
    `Object.keys(sessionStorage).filter(k => k.startsWith('tc:ui:')).sort().join(',')`,
  )
  check('会话态已落 sessionStorage', keys1.includes('tc:ui:demand:result'), keys1)

  /* ---- 5. 切走再切回（本次修复的核心断言） ---- */
  await cdp.clickByText('.ant-menu-item', '趋势雷达')
  await cdp.waitForText('重新扫描', 25_000)
  const leftPage = await cdp.eval(`document.body.innerText.includes('痛点聚类')`)
  check('离开需求探针后结果不再占位（组件确已卸载）', !leftPage)

  await cdp.clickByText('.ant-menu-item', '需求探针')
  await cdp.waitForText('开始分析', 25_000)
  const back = await cdp.eval(`document.body.innerText`)
  check('★ 切回来结果仍在（未空白）', back.includes('痛点聚类') && back.includes('上次分析'))
  check('★ 未回退到「尚未执行分析」空态', !back.includes('尚未执行分析'))
  check('切回后未误报「检索条件已改动」', !back.includes('检索条件已改动'))

  /* ---- 6. 截图留档 ---- */
  mkdirSync(join(ROOT, 'docs'), { recursive: true })
  const shot = join(ROOT, 'docs', '需求探针-会话态保持-验证.png')
  const cap = await cdp.send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(shot, Buffer.from(cap.data, 'base64'))
  console.log(`\n截图：${shot}`)

  /* ---- 7. 清空按钮可用 ---- */
  const cleared = await cdp.eval(`(() => {
    const a = [...document.querySelectorAll('a')].find(e => e.innerText.trim() === '清除结果')
    if (!a) return 'no-button'
    a.click()
    return 'clicked'
  })()`)
  await sleep(600)
  const afterClear = await cdp.eval(`document.body.innerText.includes('尚未执行分析')`)
  check('「清除结果」可回到空态', cleared === 'clicked' && afterClear, cleared)

  /* ---- 8. 能力画像：选好的公司 / 未保存内容 / 当前 Step 都要跨页保留 ---- */
  const profiles = (await api('/company/profiles', auth.access_token)).profiles ?? []
  // 关掉「最近保存」那家才验证得出「选择是否被保留」
  const targetProfile = profiles[profiles.length - 1]
  const canSwitch = profiles.length >= 2
  const expectedName = (canSwitch ? targetProfile?.name : profiles[0]?.name) ?? ''
  console.log(`公司画像：${profiles.map((p) => p.name).join(' / ') || '无'}`)

  // ⚠️ antd 的 Select / Steps 靠 **mousedown** 触发，纯 `click()` 打不开 ——
  //    必须把 pointerdown/mousedown/mouseup/click 都派发一遍（实测踩坑）
  const fire = (selExpr) => cdp.eval(`(() => {
    const el = ${selExpr}
    if (!el) return false
    for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }))
    }
    return true
  })()`)

  await cdp.eval(`location.hash = '#/profile'`)
  await cdp.waitForText('当前公司', 25_000)
  check('已进入能力画像页', await cdp.eval(`document.body.innerText.includes('公司能力画像')`))

  if (canSwitch) {
    // 8.1 切到「非最近保存」的那家公司
    await fire(`document.querySelectorAll('.ant-select-selector')[0]`)
    const opened = await cdp.waitFor(
      `document.querySelectorAll('.ant-select-item-option').length > 0`,
      6000,
    )
    const optTexts = await cdp.eval(
      `[...document.querySelectorAll('.ant-select-item-option')].map(o => o.innerText).join(' | ')`,
    )
    check('公司下拉可打开', opened, optTexts.slice(0, 120))

    const picked = await cdp.eval(`(() => {
      const opts = [...document.querySelectorAll('.ant-select-item-option')]
      const hit = opts.find(o => o.innerText.includes(${JSON.stringify(targetProfile.name)}))
      if (!hit) return false
      for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
        hit.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }))
      }
      return true
    })()`)
    check('已切换到目标公司', picked, targetProfile.name)
    const switched = await cdp.waitFor(
      `(document.querySelector('input#name') || {}).value === ${JSON.stringify(targetProfile.name)}`,
      15_000,
    )
    check('目标公司画像已载入表单', switched,
      `name = ${await cdp.eval(`(document.querySelector('input#name') || {}).value`)}`)
    const editingState = await cdp.eval(`sessionStorage.getItem('tc:ui:profile:editing')`)
    check('切换公司已写入会话态', String(editingState).includes(targetProfile.id.slice(0, 8)),
      String(editingState).slice(0, 80))
  } else {
    console.log(`  ⚠️ 只有 ${profiles.length} 家公司，跳过「切换选择是否被保留」这一段`)
  }

  // 8.2 填一个未保存的字段 + 切到第 2 步
  const MARK = '2031-07'
  await cdp.eval(`(() => {
    const el = document.querySelector('input#founded_at')
    if (!el) return false
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(el, ${JSON.stringify(MARK)})
    el.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  })()`)
  await sleep(400)
  const draftWritten = await cdp.eval(
    `Object.keys(JSON.parse(sessionStorage.getItem('tc:ui:profile:drafts') || '{}')).join(',')`,
  )
  check('改动已记为未保存草稿', draftWritten.length > 0, `草稿键：${draftWritten}`)

  // ⚠️ antd Steps 的点击处理挂在**内层 `.ant-steps-item-container`** 上，
  //    派发到外层 `.ant-steps-item` 不会冒泡「向下」传到 handler（实测踩坑）
  await fire(
    `(document.querySelectorAll('.ant-steps-item')[1] || {}).querySelector?.('.ant-steps-item-container')`,
  )
  await sleep(300)
  const stepAfterClick = await cdp.eval(`sessionStorage.getItem('tc:ui:profile:step')`)
  check('点 Step 已写入会话态', String(stepAfterClick) === '1', `profile:step = ${stepAfterClick}`)

  // 8.3 切走再回来
  await cdp.clickByText('.ant-menu-item', '趋势雷达')
  await cdp.waitForText('重新扫描', 25_000)
  await cdp.clickByText('.ant-menu-item', '能力画像')
  await cdp.waitForText('当前公司', 25_000)
  await sleep(1200) // 等画像拉取 + 草稿回填

  const backProfile = await cdp.eval(`(() => ({
    name: (document.querySelector('input#name') || {}).value,
    founded: (document.querySelector('input#founded_at') || {}).value,
    alert: document.body.innerText.includes('已恢复未保存的草稿'),
    step: [...document.querySelectorAll('.ant-steps-item')]
      .findIndex(e => e.className.includes('ant-steps-item-active')),
  }))()`)
  check('★ 切回来仍是刚才选的那家公司', backProfile.name === expectedName,
    `当前公司 = ${backProfile.name}（期望 ${expectedName}）`)
  check('★ 未保存的字段值被恢复', backProfile.founded === MARK, `成立时间 = ${backProfile.founded}`)
  check('★ 提示「已恢复未保存的草稿」', backProfile.alert)
  check('★ 当前 Step 被保留', backProfile.step === 1, `active step = ${backProfile.step}`)

  const shot2 = join(ROOT, 'docs', '能力画像-会话态保持-验证.png')
  const cap2 = await cdp.send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(shot2, Buffer.from(cap2.data, 'base64'))
  console.log(`截图：${shot2}`)

  // 8.4 丢弃草稿 → 回到已保存的画像
  await cdp.clickByText('button', '丢弃草稿')
  await sleep(900)
  const afterDiscard = await cdp.eval(`(() => ({
    alert: document.body.innerText.includes('已恢复未保存的草稿'),
    founded: (document.querySelector('input#founded_at') || {}).value,
  }))()`)
  check('「丢弃草稿」清提示并还原已保存值',
    !afterDiscard.alert && afterDiscard.founded !== MARK,
    `成立时间 = ${afterDiscard.founded}`)
}

async function cleanup() {
  try {
    if (browserCdp) await browserCdp.send('Browser.close', {}, 5000)
  } catch {
    /* 忽略 */
  }
  try {
    if (proc && !proc.killed) proc.kill()
  } catch {
    /* 忽略 */
  }
  cdp?.close()
  browserCdp?.close()
  await sleep(500)
  try {
    if (profileDir) rmSync(profileDir, { recursive: true, force: true })
  } catch {
    /* 忽略：临时目录残留无害 */
  }
}

try {
  await main()
} catch (err) {
  check('脚本执行未抛异常', false, err.message)
} finally {
  await cleanup()
}

const failed = results.filter((r) => !r.ok)
console.log(`\n${'─'.repeat(60)}`)
console.log(`UI 会话态冒烟：${results.length - failed.length}/${results.length} 通过`)
if (failed.length) {
  console.log('失败项：')
  failed.forEach((f) => console.log(`  · ${f.name}${f.detail ? ` — ${f.detail}` : ''}`))
  process.exit(1)
}
console.log('全部通过 ✅')
