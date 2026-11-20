// Run after npm --prefix frontend run build. All source records belong to a temporary database.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { access, mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { freePort, stopOwned, waitUntil } from './online_fixture/browser.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
await access(path.join(root, 'frontend/dist/index.html'))
const temporary = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-assistant-source-'))
const port = await freePort(), base = 'http://127.0.0.1:' + port
const backend = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_fixture.diagnosis', '--port', String(port)], {
  cwd: root, detached: true, stdio: 'ignore', env: { PATH: process.env.PATH,
    DATA_DIR: path.join(temporary, 'data'), MEDIA_ROOT: path.join(temporary, 'media'), AI_ENABLED: 'false' },
})
let browser
let activePage
try {
  await waitUntil(async () => (await fetch(base + '/api/health')).ok, 'Isolated service failed to start')
  browser = await chromium.launch()
  const oldUrl = 'https://cj.lziapi.com/api.php/provide/vod/', newUrl = 'https://lzzyapi.com/api.php/provide/vod/'
  const hosts = ['cj.lziapi.com', 'v.lzcdn28.com', 'p.lztutu.com', 'extra.example']
  const created = await fetch(base + '/api/online/sources', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider: 'maccms', name: '我的测试片库', api_base_url: oldUrl, allowed_hosts: hosts, enabled: false }) })
  assert.equal(created.status, 201)
  const source = '/api/online/sources/' + (await created.json()).id
  for (const width of [1440, 390, 320]) {
    const context = await browser.newContext({ baseURL: base, viewport: { width, height: 850 } })
    const page = await context.newPage()
    activePage = page
    page.setDefaultTimeout(12000)
    const writes = [], errors = [], foreign = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('request', request => { if (request.method() === 'PATCH' && new URL(request.url()).pathname === source) writes.push(request) })
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === base) return route.continue()
      foreign.push(route.request().url())
      return route.abort()
    })
    const reset = await context.request.patch(source, { data: { api_base_url: oldUrl, allowed_hosts: hosts } })
    assert.ok(reset.ok())
    if (width === 320) {
      assert.ok((await context.request.get('/api/viewing/session')).ok())
      assert.ok((await context.request.post('/api/viewing/password', { data: { new_password: 'demo-password' } })).ok())
      assert.equal((await context.request.get(source)).status(), 403)
    }
    await page.goto('/online', { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: '打开小映', exact: true }).click()
    if (width !== 320) {
      const chat = page.locator('#assistant-question')
      await chat.fill('为什么量子在线资源无法播放了？')
      await chat.press('Enter')
      const disabled = page.locator('[data-assistant-diagnosis]')
      await disabled.getByText(/当前来源已停用/).waitFor()
      assert.equal(await disabled.getByRole('button', { name: '同意修改' }).count(), 0)
    }
    const question = '帮我更新量子资源接口'
    await page.locator('#assistant-question').fill(question)
    await page.locator('#assistant-question').press('Enter')
    await page.getByRole('button', { name: '在设置中预览换址' }).click()
    if (width === 320) {
      const unlock = page.getByRole('dialog', { name: '验证保护密码' })
      await unlock.locator('#protection-password').fill('demo-password')
      await unlock.getByRole('button', { name: '确认', exact: true }).click()
    }
    const card = page.locator('[data-assistant-source-migration]')
    const choices = card.locator('[data-online-host-migration]')
    await choices.waitFor()
    assert.equal(new URL(page.url()).searchParams.get('sec'), 'sec-online')
    assert.equal(await choices.getByRole('checkbox', { checked: true }).count(), 3)
    assert.equal((await (await context.request.get(source)).json()).api_base_url, oldUrl)
    assert.equal(writes.length, 0)
    if (width === 1440) {
      assert.ok((await context.request.patch(source, { data: { priority: 78 } })).ok())
      await card.getByRole('button', { name: '确认应用' }).click()
      await card.getByRole('button', { name: '重新核对' }).click()
      await choices.waitFor()
      assert.equal((await (await context.request.get(source)).json()).api_base_url, oldUrl)
    }
    await choices.locator('input[value="p.lztutu.com"]').uncheck()
    assert.ok(await card.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'Migration card fits the panel')
    await card.getByRole('button', { name: '确认应用' }).click()
    await card.getByText(/已更新“我的测试片库”/).waitFor()
    const saved = await (await context.request.get(source)).json()
    assert.equal(saved.api_base_url, newUrl)
    assert.deepEqual([...saved.allowed_hosts].sort(), ['lzzyapi.com', 'v.cdnlz28.com', 'p.lztutu.com', 'extra.example'].sort())
    assert.equal(saved.enabled, false)
    assert.equal(saved.priority, 78)
    assert.equal(writes.length, width === 1440 ? 2 : 1)
    // Keep the real chat, diagnostic decisions, SQLite commit and follow-up probe.
    // Only the external website and catalogue responses are supplied by the fixture.
    const ask = async value => {
      await page.locator('#settings-question').fill(value)
      await page.locator('#settings-question').press('Enter')
    }
    assert.ok((await context.request.patch('/api/online/settings', { data: { enabled: true } })).ok())
    assert.ok((await context.request.patch(source, { data: { enabled: true, api_base_url: oldUrl, allowed_hosts: hosts } })).ok())
    await ask('为什么量子在线资源无法播放了？')
    const diagnosis = page.locator('[data-assistant-diagnosis]').last()
    const confirm = diagnosis.getByRole('button', { name: '同意修改' })
    await confirm.waitFor()
    await diagnosis.locator('summary').click()
    assert.ok(await diagnosis.getByText(/上游返回 HTTP 410/).count() >= 2)
    assert.equal(new URL(await diagnosis.getByRole('link', { name: '查看来源网页' }).getAttribute('href')).hostname, 'lzizy.net')
    assert.equal((await (await context.request.get(source)).json()).api_base_url, oldUrl)
    if (width === 390) {
      await context.request.patch(source, { data: { priority: 79 } })
      await confirm.click()
      await diagnosis.getByText(/来源配置或方案已变化/).waitFor()
      await diagnosis.getByRole('button', { name: '重新诊断' }).click()
      await confirm.waitFor()
    }
    if (width === 320) await confirm.click()
    else await ask('同意')
    await diagnosis.getByText(/已更新此来源的接口和允许主机/).waitFor()
    const diagnosed = await (await context.request.get(source)).json()
    assert.equal(diagnosed.api_base_url, newUrl)
    assert.deepEqual([...diagnosed.allowed_hosts].sort(), ['lzzyapi.com', ...hosts.slice(1)].sort())
    assert.ok(await diagnosis.evaluate(el => el.scrollWidth <= el.clientWidth + 1))
    await context.request.patch(source, { data: { enabled: false, priority: 78 } })
    await context.request.patch('/api/online/settings', { data: { enabled: false } })
    console.log('PASS 旧目录两次失败、公告出处、同意换址、已保存地址复测 ' + width + 'px')
    assert.deepEqual(errors, [])
    assert.deepEqual(foreign, [])
    await context.close()
    console.log('PASS 小映浏览转设置、域名预览和确认保存 ' + width + 'px')
  }
} catch (error) {
  if (activePage && !activePage.isClosed()) console.error((await activePage.locator('body').innerText()).slice(-1700))
  throw error
} finally {
  await browser?.close()
  await stopOwned(backend)
  await rm(temporary, { recursive: true, force: true })
}
