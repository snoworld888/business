#!/usr/bin/env node
/**
 * 登录跳转诊断：用真实浏览器走一遍**表单登录**，观察 URL 时间线。
 *
 * 为什么单独做
 * ------------
 * `ui_smoke_persist.mjs` 是「往 localStorage 写 token + 整页 reload」，**绕过登录表单**，
 * 因此「点登录按钮之后会不会跳转」这条路径从未被覆盖。
 *
 * 用法
 * ----
 *   node scripts/diag_login.mjs
 *   TC_BASE=http://192.168.2.192:8020 node scripts/diag_login.mjs
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.env.TC_BASE ?? 'http://127.0.0.1:8020'
const PORT = Number(process.env.TC_CDP_PORT ?? 9334)
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
    this.console = []
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
      // 收集页面控制台报错（诊断用）
      if (msg.method === 'Runtime.consoleAPICalled' && msg.params?.type === 'error') {
        cdp.console.push(msg.params.args.map((a) => a.value ?? a.description).join(' '))
      }
      if (msg.method === 'Runtime.exceptionThrown') {
        cdp.console.push(
          `[异常] ${msg.params?.exceptionDetails?.exception?.description ?? ''}`.slice(0, 300),
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
      expression,
      awaitPromise: true,
      returnByValue: true,
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
        /* 导航中上下文销毁 */
      }
      if (Date.now() > deadline) return false
      await sleep(300)
    }
  }

  close() {
    try {
      this.ws.close()
    } catch {
      /* 已关闭 */
    }
  }
}

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

let proc
let cdp
let profileDir

async function main() {
  const exe = BROWSERS.find((p) => existsSync(p))
  if (!exe) throw new Error('本机未找到 Chromium 内核浏览器（Chrome / Edge）')

  profileDir = mkdtempSync(join(tmpdir(), 'tc-diag-login-'))
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

  const targets = await waitHttp(`http://127.0.0.1:${PORT}/json/list`, 20_000)
  const page = targets.find((t) => t.type === 'page')
  cdp = await Cdp.connect(page.webSocketDebuggerUrl)
  await cdp.send('Runtime.enable')
  await cdp.send('Page.enable')

  console.log(`浏览器：${exe}`)
  console.log(`目标：${BASE}\n`)

  /* ---- 1. 干净起点：打开站点 → 清 localStorage → 整页 reload ---- */
  await cdp.send('Page.navigate', { url: `${BASE}/` })
  await cdp.waitFor(`document.readyState === 'complete'`, 20_000)
  await cdp.eval(`(() => { localStorage.clear(); sessionStorage.clear(); return 'ok' })()`)
  await cdp.send('Page.reload')
  await cdp.waitFor(`document.readyState === 'complete'`, 20_000)
  await cdp.waitFor(`document.body.innerText.includes('登')`, 20_000)
  await sleep(800)

  const startHref = await cdp.eval(`location.href`)
  const onLogin = await cdp.eval(
    `!!document.querySelector('button[type=submit]') && document.body.innerText.includes('演示账号已预填')`,
  )
  console.log(`清空登录态后：URL = ${startHref}`)
  console.log(`停在登录页：${onLogin ? '是' : '否'}`)
  console.log(`localStorage token：${await cdp.eval(`String(localStorage.getItem('trackcompass_token'))`)}`)

  if (!onLogin) {
    console.log('\n⚠️ 起点不是登录页，诊断结果不可信')
  }

  /* ---- 2. 填表并点登录（页面已预填演示账号，直接提交） ---- */
  const filled = await cdp.eval(`(() => {
    const set = (el, v) => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(el, v)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    }
    const email = document.querySelector('input#email') || document.querySelectorAll('input')[0]
    const pwd = document.querySelector('input#password') || document.querySelector('input[type=password]')
    if (!email || !pwd) return false
    set(email, ${JSON.stringify(EMAIL)})
    set(pwd, ${JSON.stringify(PASSWORD)})
    return true
  })()`)
  console.log(`\n已填入账号：${filled}`)

  await cdp.eval(`(() => {
    const btn = document.querySelector('button[type=submit]')
    if (!btn) return false
    for (const t of ['mousedown', 'mouseup', 'click']) {
      btn.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }))
    }
    return true
  })()`)
  console.log('已点击登录，开始采样 URL 时间线...\n')

  /* ---- 3. 采样 URL 时间线（关键证据：是否跳了又被弹回） ---- */
  const timeline = []
  const t0 = Date.now()
  let last = ''
  for (let i = 0; i < 40; i++) {
    await sleep(400)
    let href = '?'
    try {
      href = await cdp.eval(`location.hash || '/'`)
    } catch {
      href = '(导航中)'
    }
    if (href !== last) {
      timeline.push(`${((Date.now() - t0) / 1000).toFixed(1)}s  ${href}`)
      last = href
    }
    // 已到 dashboard 再多看 2 秒，确认不会被弹回
    if (href.includes('/dashboard') && Date.now() - t0 > 3000) break
  }

  console.log('URL 时间线：')
  for (const line of timeline) console.log(`  ${line}`)

  const final = await cdp.eval(`({
    href: location.href,
    hash: location.hash,
    token: localStorage.getItem('trackcompass_token') ? 'ok' : 'null',
    user: localStorage.getItem('trackcompass_user') ? 'ok' : 'null',
    onLoginPage: document.body.innerText.includes('演示账号已预填'),
    hasSidebar: document.body.innerText.includes('工作台'),
    alert: (document.querySelector('.ant-alert-error') || {}).innerText || null,
    toast: [...document.querySelectorAll('.ant-message-notice')].map(e => e.innerText).join(' | '),
  })`)

  console.log('\n最终状态：')
  for (const [k, v] of Object.entries(final)) console.log(`  ${k}: ${v}`)

  if (cdp.console.length) {
    console.log('\n页面控制台报错：')
    for (const c of cdp.console.slice(0, 8)) console.log(`  ${c}`)
  }

  const jumped = String(final.hash).includes('/dashboard')
  const bounced = timeline.length > 1 && timeline.some((l) => l.includes('/dashboard')) &&
    String(final.onLoginPage) === 'true'
  console.log('\n' + '='.repeat(60))
  console.log(
    jumped && !final.onLoginPage
      ? '✅ 登录后正常进入工作台'
      : bounced
        ? '❌ 跳到了 #/dashboard 但被弹回登录页（isAuthenticated 未更新）'
        : '❌ 登录后未跳转（仍停在登录页）',
  )
  console.log('='.repeat(60))
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
      try {
        rmSync(profileDir, { recursive: true, force: true })
      } catch {
        /* 清理失败无妨 */
      }
    }
  })
