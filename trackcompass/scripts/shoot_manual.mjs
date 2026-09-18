#!/usr/bin/env node
/**
 * 为《产品使用手册》批量截取**真实界面**截图。
 *
 * 为什么要脚本而不是手工截图：手册里的图必须能**重跑复现** —— 界面改了，
 * 重跑一次即可刷新全部配图，不必人工一张张对齐。零依赖（Node 内置 fetch + WebSocket
 * 直连 CDP），不下载浏览器、不装包。
 *
 * 用法
 * ----
 *   node scripts/shoot_manual.mjs
 *   TC_BASE=http://192.168.2.192:8020 node scripts/shoot_manual.mjs
 *   TC_ONLY=trends,demand node scripts/shoot_manual.mjs      # 只重截某几页
 *
 * 产物：`docs/manual_shots/*.png`（文件名前缀即页面编号，与手册章节一一对应）
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const BASE = process.env.TC_BASE ?? 'http://127.0.0.1:8020'
const OUT_DIR = join(ROOT, 'docs', 'manual_shots')
const ONLY = (process.env.TC_ONLY ?? '').split(',').filter(Boolean)
const PORT = Number(process.env.TC_CDP_PORT ?? 9336)
const EMAIL = 'admin@demo.com'
const PASSWORD = 'TrackCompass@2026'
/** 视口用常见的 1440×900（笔记本全屏），截图观感与用户实际一致 */
const VIEW = { width: 1440, height: 900 }

const BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
]

/**
 * 逐页拍摄计划。
 *
 * - `wait`：进页后必须出现的文本（**用页面特征文本而不是固定 sleep**，
 *   否则截到的是加载态 —— 这类「测的是骨架不是内容」的坑已被踩过）；
 * - `full`：是否截整页（长表格页取整页，其余取视口，避免图太长不便阅读）；
 * - `act`：可选交互（点击某按钮/链接的文案），`after` 是其后的等待文本。
 */
const PAGES = [
  { id: 'p01-login', hash: null, wait: '赛道罗盘', full: false, title: '登录页' },
  { id: 'p02-dashboard', hash: '#/dashboard', wait: '工作台', full: true, title: '工作台' },
  { id: 'p02b-dashboard-engine', hash: '#/dashboard', wait: '工作台', full: true,
    act: '分析引擎状态', actKind: 'tab', title: '工作台 · 分析引擎状态' },
  { id: 'p03-profile', hash: '#/profile', wait: '当前公司', full: false, title: '能力画像' },
  { id: 'p12-signals', hash: '#/signals', wait: '信号总量', full: false, title: '信号库' },
  { id: 'p04-trends', hash: '#/trends', wait: '趋势', full: false, title: '趋势雷达' },
  { id: 'p05-graph', hash: '#/graph', wait: '赛道地图', full: false, title: '机会图谱' },
  { id: 'p06-demand', hash: '#/demand', wait: '开始分析', full: true,
    // 预置一个**库内真有命中**的关键词：默认词「质检」在当前 68 条信号里命中 0 条，
    // 截出来是空态图，不能代表功能（手册配图必须是「功能正常时」的样子）
    session: { 'tc:ui:demand:keywords': '["光学"]' },
    act: '开始分析', after: '痛点聚类', afterTimeoutMs: 180_000, title: '需求探针（含分析结果）' },
  { id: 'p07-scorecard', hash: '#/scorecard', wait: '排序对比', full: true,
    // 不选机会卡片截出来是「尚未选择机会」空态，**不能代表功能** ⇒ 预选分数最高那张
    hashFn: (ctx) => (ctx.firstOppId ? `#/scorecard?opportunity=${ctx.firstOppId}` : '#/scorecard'),
    title: '赛道评分' },
  { id: 'p08-experiments', hash: '#/experiments', wait: '新建实验', full: false, title: '验证实验' },
  { id: 'p09-reports', hash: '#/reports', wait: '报告列表', full: false, title: '决策报告' },
  { id: 'p10-monitor', hash: '#/monitor', wait: '预警', full: false, title: '监控复盘' },
  { id: 'p11-settings', hash: '#/settings', wait: '用户与权限', full: false, title: '设置' },
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

class Cdp {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.errors = []
    this.navigations = []
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
      if (msg.method === 'Page.frameNavigated' && !msg.params?.frame?.parentId) {
        cdp.navigations.push(msg.params.frame.url)
      }
      if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params?.exceptionDetails
        cdp.errors.push(`[异常] ${d?.exception?.description ?? d?.text ?? ''}`.slice(0, 400))
      }
      if (msg.method === 'Runtime.consoleAPICalled' && msg.params?.type === 'error') {
        const text = (msg.params.args ?? []).map((a) => a.value ?? a.description ?? '').join(' ')
        cdp.errors.push(`[console.error] ${text}`.slice(0, 400))
      }
      const slot = msg.id ? cdp.pending.get(msg.id) : undefined
      if (!slot) return
      cdp.pending.delete(msg.id)
      if (msg.error) slot.reject(new Error(msg.error.message))
      else slot.resolve(msg.result)
    }
    return cdp
  }

  send(method, params = {}, timeoutMs = 90_000) {
    const id = ++this.seq
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`CDP 超时：${method}`))
      }, timeoutMs)
    })
  }

  async eval(expression) {
    const r = await this.send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true,
    })
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? '页面脚本抛错')
    return r.result.value
  }

  async waitFor(expression, timeoutMs) {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      try {
        if (await this.eval(`!!(${expression})`)) return true
      } catch { /* 导航中 */ }
      if (Date.now() > deadline) return false
      await sleep(300)
    }
  }

  async waitText(text, timeoutMs) {
    return this.waitFor(
      `document.body && document.body.innerText.includes(${JSON.stringify(text)})`,
      timeoutMs,
    )
  }

  /**
   * 点击「文本命中」的元素。
   *
   * 两个实测坑：
   * 1. antd 的交互多挂在 `mousedown` 上，**纯 `click()` 打不开**（Select 尤甚）⇒ 事件派发全；
   * 2. **必须点真正挂了 handler 的那个元素**：`.ant-tabs-tab` 的文本在**子节点**
   *    `.ant-tabs-tab-btn` 里，事件派发到父节点时 `event.target` 是父节点，
   *    React 的合成事件从 target 往上找 fiber，**找不到子节点的 onClick** ⇒ 白点。
   *    故：候选按「最内层文本节点所属元素」优先（`el.querySelector('*')` 为空者优先）。
   *
   * 未命中时返回**诊断信息**而不是 `false` —— 否则只知道「没点到」，
   * 不知道是「选择器不对」还是「页面还没渲染」。
   */
  click(kind, text) {
    // `tab` 这类「互斥选项」在本项目里既有 antd `Tabs`（决策报告/监控复盘/设置）
    // 也有 `Segmented`（工作台的机会与预警/分析引擎状态）—— **两套类名都要覆盖**，
    // 只写 `.ant-tabs-tab` 会得到「候选节点 0 个」这种看似诡异的结果。
    const sel =
      kind === 'tab'
        ? '.ant-tabs-tab-btn, .ant-segmented-item-label, .ant-segmented-item, .ant-radio-button-wrapper'
        : 'button, a, .ant-tabs-tab-btn, .ant-segmented-item-label, .ant-steps-item-container'
    return this.eval(`(() => {
      const all = [...document.querySelectorAll(${JSON.stringify(sel)})]
      // ⚠️ 判文本用 \`textContent\` 而非 \`innerText\`：\`innerText\` 依赖布局，
      //    元素尚未布局/被隐藏时会返回空串 ⇒ 明明有节点却「一个都匹配不上」。
      const txt = (e) => (e.textContent || e.innerText || '').replace(/\\s+/g, ' ').trim()
      const hit = all.filter(e => txt(e).includes(${JSON.stringify(text)}))
      if (!hit.length) {
        return {
          ok: false,
          why: '无匹配元素',
          count: all.length,
          candidates: all.map(txt).filter(Boolean).slice(0, 10),
        }
      }
      // 最内层优先：文本落在子节点（如 .ant-tabs-tab-btn）时，
      // 事件派发到父节点会因 target 是父节点而**跳过子节点的 handler**
      const el = hit[hit.length - 1]
      for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
        el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }))
      }
      return { ok: true, tag: el.tagName + '.' + String(el.className).slice(0, 40), matched: hit.length }
    })()`)
  }

  async shoot(file, full) {
    const params = { format: 'png', captureBeyondViewport: true }
    if (!full) {
      params.clip = { x: 0, y: 0, width: VIEW.width, height: VIEW.height, scale: 1 }
    } else {
      const size = await this.eval(
        `({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight })`,
      )
      params.clip = {
        x: 0, y: 0,
        width: Math.min(VIEW.width, Math.max(size.w, VIEW.width)),
        height: Math.max(size.h, VIEW.height),
        scale: 1,
      }
    }
    const shot = await this.send('Page.captureScreenshot', params)
    writeFileSync(file, Buffer.from(shot.data, 'base64'))
  }

  close() { try { this.ws.close() } catch { /* 已关闭 */ } }
}

async function waitJson(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    try {
      const r = await fetch(url)
      if (r.ok) return await r.json()
    } catch { /* 还没起来 */ }
    if (Date.now() > deadline) throw new Error(`等待 ${url} 超时`)
    await sleep(300)
  }
}

let proc
let cdp
let profileDir
const results = []

async function main() {
  const exe = BROWSERS.find((p) => existsSync(p))
  if (!exe) throw new Error('本机未找到 Chromium 内核浏览器')
  mkdirSync(OUT_DIR, { recursive: true })

  const auth = (
    await fetch(`${BASE}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
    }).then((r) => r.json())
  ).data
  if (!auth?.access_token) throw new Error('接口登录失败，无法截图')

  profileDir = mkdtempSync(join(tmpdir(), 'tc-shoot-'))
  proc = spawn(exe, [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profileDir}`,
    `--window-size=${VIEW.width},${VIEW.height}`,
    '--hide-scrollbars',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-gpu',
    'about:blank',
  ], { stdio: 'ignore' })

  const targets = await waitJson(`http://127.0.0.1:${PORT}/json/list`, 20_000)
  cdp = await Cdp.connect(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
  await cdp.send('Runtime.enable')
  await cdp.send('Page.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: VIEW.width, height: VIEW.height, deviceScaleFactor: 1, mobile: false,
  })

  console.log(`基址 ${BASE} → 输出 ${OUT_DIR}\n`)

  /* ---------- 第 1 张：登录页（必须在注入登录态之前） ---------- */
  const want = (id) => !ONLY.length || ONLY.some((k) => id.includes(k))
  const loginShot = join(OUT_DIR, 'p01-login.png')

  // ⚠️ 先导航到站点：`about:blank` 上访问 `localStorage` 会抛
  //    `SecurityError: Access is denied for this document`（origin 为 opaque）。
  //    只在「跳过登录页」时最易踩到 —— 那时前面的导航被 `if` 跳过了。
  await cdp.send('Page.navigate', { url: `${BASE}/` })
  await cdp.waitFor(`document.readyState === 'complete'`, 20_000)
  await sleep(800)

  if (want('p01')) {
    await cdp.eval(`localStorage.clear(); sessionStorage.clear()`)
    await cdp.send('Page.navigate', { url: `${BASE}/` })
    await cdp.waitText('赛道罗盘', 20_000)
    await sleep(800)
    await cdp.shoot(loginShot, false)
    results.push({ id: 'p01-login', title: '登录页', ok: true, note: '未登录态' })
    console.log('✅ p01-login 登录页')
  }

  /* ---------- 注入登录态 ---------- */
  await cdp.eval(`(() => {
    localStorage.setItem('trackcompass_token', ${JSON.stringify(auth.access_token)})
    localStorage.setItem('trackcompass_user', ${JSON.stringify(JSON.stringify(auth.user))})
    return 'ok'
  })()`)
  // 先 reload 让守卫带上登录态，再逐页改 hash（顺序反了会停在 #/login）
  await cdp.send('Page.reload')
  await cdp.waitFor(`document.readyState === 'complete'`, 20_000)
  await sleep(1200)

  /* ---------- 其余页面 ---------- */
  // 上下文：部分页面需要「有数据»」的初始状态，否则截到的是空态图
  const oppList = await fetch(`${BASE}/api/v1/opportunities?sort=score&limit=1`, {
    headers: { Authorization: `Bearer ${auth.access_token}` },
  }).then((r) => r.json()).catch(() => null)
  const ctx = { firstOppId: oppList?.data?.opportunities?.[0]?.id ?? null }
  console.log(`上下文：分数最高机会卡片 = ${ctx.firstOppId ?? '（无，相关页会截到空态）'}\n`)

  for (const p of PAGES) {
    if (!p.hash || !want(p.id)) continue
    cdp.errors.length = 0
    const hash = p.hashFn ? p.hashFn(ctx) : p.hash
    // 该页需要的会话态（在改 hash 之前写入，页面挂载时即可读到）
    if (p.session) {
      await cdp.eval(`(() => { for (const [k, v] of Object.entries(${JSON.stringify(p.session)})) sessionStorage.setItem(k, v); return 'ok' })()`)
    }
    await cdp.eval(`location.hash = ${JSON.stringify(hash)}`)
    const ok = await cdp.waitText(p.wait, 30_000)
    await sleep(1500) // 等图表与列表动画落定

    let note = ''
    if (p.act) {
      const r = await cdp.click(p.actKind, p.act)
      note = r?.ok
        ? `点击「${p.act}」→ ${r.tag}（命中 ${r.matched} 个，取最内层）`
        : `点击「${p.act}」**未命中**：${r?.why}（候选节点 ${r?.count ?? '?'} 个）；候选文本=${JSON.stringify(r?.candidates ?? [])}`
      if (r?.ok && p.after) {
        const got = await cdp.waitText(p.after, p.afterTimeoutMs ?? 60_000)
        note += ` · 等待「${p.after}」${got ? '出现' : '**超时**'}`
      }
      await sleep(1200)
    }

    const file = join(OUT_DIR, `${p.id}.png`)
    // 关掉右上角的通知再拍：它默认停 8 秒，会盖住页头（截图是给手册用的，要干净）
    await cdp.eval(`(() => {
      for (const x of document.querySelectorAll('.ant-notification-notice-close')) {
        for (const t of ['mousedown', 'mouseup', 'click']) {
          x.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }))
        }
      }
      return 'ok'
    })()`).catch(() => {})
    await sleep(400)
    await cdp.shoot(file, p.full)
    const textLen = await cdp.eval(`(document.body.innerText || '').length`)
    results.push({
      id: p.id, title: p.title, ok, textLen, note: note || '—',
      errors: [...new Set(cdp.errors)].slice(0, 2),
    })
    console.log(`${ok ? '✅' : '❌'} ${p.id.padEnd(24)} ${p.title}  文本 ${textLen} 字  ${note}`)
    if (cdp.errors.length) for (const e of new Set(cdp.errors)) console.log(`     ⚠️ ${e}`)
  }

  console.log('\n=== 汇总 ===')
  const bad = results.filter((r) => !r.ok || /超时|未找到/.test(r.note))
  console.log(`共 ${results.length} 张，异常 ${bad.length} 张`)
  for (const b of bad) console.log(`  ⚠️ ${b.id}：${b.note || '未等到期望文本'}`)
  console.log(`产物目录：${OUT_DIR}`)
}

main()
  .catch((e) => {
    console.error(`\n截图失败：${e.message}`)
    process.exitCode = 1
  })
  .finally(() => {
    cdp?.close()
    proc?.kill()
    if (profileDir) {
      try { rmSync(profileDir, { recursive: true, force: true }) } catch { /* 无妨 */ }
    }
  })
