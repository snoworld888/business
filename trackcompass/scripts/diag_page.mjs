#!/usr/bin/env node
/**
 * 通用页面诊断：打开某个页 → 采样页面文本与控制台报错。
 *
 * 用途：当冒烟只报「等待文本超时 + 页面文本为空」时，这句话本身分不出
 * 「页面真白屏（React 崩溃）」/「还在加载」/「渲染了但没这个文本」。
 * 本脚本把**控制台报错与异常**一并抓出来，用于定位白屏根因。
 *
 * 用法
 * ----
 *   node scripts/diag_page.mjs                       # 默认 #/demand
 *   TC_HASH='#/trends' node scripts/diag_page.mjs
 *   TC_BASE=http://192.168.2.192:8020 node scripts/diag_page.mjs
 *   TC_CLICK='开始分析' node scripts/diag_page.mjs   # 进页后点一下再观察
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.env.TC_BASE ?? 'http://127.0.0.1:8020'
const HASH = process.env.TC_HASH ?? '#/demand'
const CLICK = process.env.TC_CLICK ?? ''
/** 预置 sessionStorage（JSON 对象，值是**已序列化的字符串**），用于复现冒烟的会话态条件 */
const SESSION = process.env.TC_SESSION ? JSON.parse(process.env.TC_SESSION) : null
/** 'dispatch'（默认，派发鼠标事件）| 'click'（原生 el.click()，与冒烟脚本一致） */
const CLICK_MODE = process.env.TC_CLICK_MODE ?? 'dispatch'
const WATCH_S = Number(process.env.TC_WATCH_S ?? 40)
const PORT = Number(process.env.TC_CDP_PORT ?? 9335)
const EMAIL = 'admin@demo.com'
const PASSWORD = 'TrackCompass@2026'

const BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

class Cdp {
  constructor(ws) {
    this.ws = ws
    this.seq = 0
    this.pending = new Map()
    this.errors = []
    /** 整页导航历史：白屏与「页面被重载」在只有文本输出时无法区分 */
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
      if (msg.method === 'Runtime.consoleAPICalled') {
        const text = (msg.params?.args ?? [])
          .map((a) => a.value ?? a.description ?? a.preview?.description ?? '')
          .join(' ')
        if (msg.params?.type === 'error' || /Maximum update depth|Minified React error/i.test(text)) {
          cdp.errors.push(`[console.${msg.params.type}] ${text}`.slice(0, 500))
        }
      }
      if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params?.exceptionDetails
        cdp.errors.push(
          `[异常] ${d?.exception?.description ?? d?.text ?? ''}`.slice(0, 800),
        )
      }
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

  async eval(expression) {
    const r = await this.send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true,
    })
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.exception?.description ?? '页面脚本抛错')
    }
    return r.result.value
  }

  async waitFor(expression, timeoutMs) {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      try {
        if (await this.eval(`!!(${expression})`)) return true
      } catch {
        /* 导航中 */
      }
      if (Date.now() > deadline) return false
      await sleep(300)
    }
  }

  close() {
    try { this.ws.close() } catch { /* 已关闭 */ }
  }
}

async function waitHttp(url, timeoutMs) {
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

async function main() {
  const exe = BROWSERS.find((p) => existsSync(p))
  if (!exe) throw new Error('本机未找到 Chromium 内核浏览器')

  // 起手式用接口登录 + 注入 storage（本脚本只关心**页面渲染**，不测登录链路）
  const login = await fetch(`${BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  }).then((r) => r.json())
  const auth = login.data

  profileDir = mkdtempSync(join(tmpdir(), 'tc-diag-page-'))
  proc = spawn(exe, [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profileDir}`,
    '--window-size=1600,1000',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-gpu',
    'about:blank',
  ], { stdio: 'ignore' })

  const targets = await waitHttp(`http://127.0.0.1:${PORT}/json/list`, 20_000)
  const page = targets.find((t) => t.type === 'page')
  cdp = await Cdp.connect(page.webSocketDebuggerUrl)
  await cdp.send('Runtime.enable')
  await cdp.send('Page.enable')
  console.log(`目标：${BASE}${HASH}${CLICK ? ` → 点击「${CLICK}」` : ''}\n`)

  await cdp.send('Page.navigate', { url: `${BASE}/` })
  await cdp.waitFor(`document.readyState === 'complete'`, 20_000)
  await cdp.eval(`(() => {
    localStorage.setItem('trackcompass_token', ${JSON.stringify(auth.access_token)})
    localStorage.setItem('trackcompass_user', ${JSON.stringify(JSON.stringify(auth.user))})
    ${SESSION ? `for (const [k, v] of Object.entries(${JSON.stringify(SESSION)})) sessionStorage.setItem(k, v)` : ''}
    return 'ok'
  })()`)
  // ⚠️ 顺序：**先 reload 再改 hash**。
  // 反过来的话，目标页在「快照还是未登录」时被访问，守卫会先把 hash 改写成
  // `#/login`；而 `#/login` 是显式路由，reload 之后即使已登录也仍停在登录页 ——
  // 会被误读成「登录态没生效」。
  await cdp.send('Page.reload')
  await cdp.waitFor(`document.readyState === 'complete'`, 20_000)
  await sleep(1200)
  await cdp.eval(`location.hash = ${JSON.stringify(HASH)}`)
  await sleep(2500)

  const snap = async (label) => {
    const s = await cdp.eval(`(() => {
      const t = document.body.innerText || ''
      const hit = ['痛点聚类', '未聚类到痛点', '匹配', '需求强度', '综合需求强度', '样本覆盖']
        .filter(k => t.includes(k))
      return {
        hash: location.hash,
        len: t.length,
        hits: hit.join(' / ') || '（无）',
        tail: t.slice(-260).replace(/\\n+/g, ' / '),
      }
    })()`).catch(() => null)
    console.log(
      `${label}  len=${s?.len ?? '?'}\n      命中关键文本：${s?.hits ?? '?'}\n      末尾：${s?.tail ?? '?'}`,
    )
    return s
  }

  console.log('— 进页后 —')
  await snap('  t+0s')

  if (CLICK) {
    const ok = await cdp.eval(`(() => {
      const el = [...document.querySelectorAll('button')]
        .find(e => (e.innerText || '').includes(${JSON.stringify(CLICK)}))
      if (!el) return false
      ${
        CLICK_MODE === 'click'
          ? 'el.click()'
          : `for (const t of ['mousedown', 'mouseup', 'click']) {
        el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }))
      }`
      }
      return true
    })()`)
    console.log(`\n— 点击「${CLICK}」（${CLICK_MODE}）：${ok ? '已触发' : '没找到按钮'} —`)
    const t0 = Date.now()
    for (let i = 0; i < Math.ceil(WATCH_S / 4); i++) {
      await sleep(4000)
      await snap(`  t+${((Date.now() - t0) / 1000).toFixed(0)}s`)
      if (cdp.errors.length) break // 一旦报错就不必继续等
    }
  }

  console.log('\n整页导航历史：')
  if (!cdp.navigations.length) console.log('  （无）')
  for (const n of cdp.navigations) console.log(`  ${n}`)

  console.log('\n控制台报错 / 异常：')
  if (!cdp.errors.length) console.log('  （无）')
  for (const e of [...new Set(cdp.errors)].slice(0, 10)) console.log(`  ${e}`)
}

main()
  .catch((e) => {
    console.error(`\n诊断失败：${e.message}`)
    process.exitCode = 1
  })
  .finally(() => {
    cdp?.close()
    proc?.kill()
    if (profileDir) {
      try { rmSync(profileDir, { recursive: true, force: true }) } catch { /* 无妨 */ }
    }
  })
