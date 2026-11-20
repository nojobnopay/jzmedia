// Two assistant entry points, real catalogue identities and real synthetic MP4 playback.
// The failing first session and diagnostic responses are supplied by Playwright.
import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { buildOnlinePreview, freePort, stopOwned, waitUntil } from './online_fixture/browser.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-online-catalog-'))
const fixture = ['-m', 'scripts.online_fixture.catalog', '--root', work]
const python = path.join(root, '.venv/bin/python'), dist = path.join(work, 'dist')
let backend, browser
try {
  execFileSync(python, [...fixture, '--generate-media'], { cwd: root, stdio: 'inherit' })
  await buildOnlinePreview({ root, work, dist, kind: 'catalog' })
  const port = await freePort(), base = 'http://127.0.0.1:' + port
  backend = spawn(python, [...fixture, '--port', String(port), '--static-dir', dist], { cwd: root, detached: true, stdio: 'inherit' })
  await waitUntil(async () => (await fetch(base + '/api/fixture/health')).ok, 'Fixture did not start')
  browser = await chromium.launch()
  for (const entry of ['player', 'detail']) {
    const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 900 } })
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    const requests = [], sessions = [], errors = []
    page.on('pageerror', error => errors.push(error.message))
    const bootstrap = await (await context.request.get('/api/fixture/bootstrap')).json()
    const headers = { 'X-Api-Token': bootstrap.api_token }
    const created = await context.request.post('/api/online/sources', { headers, data: { name: '诊断演示-' + entry,
      provider: 'maccms', api_base_url: bootstrap.api_base_url, allowed_hosts: bootstrap.allowed_hosts, enabled: true } })
    assert.equal(created.status(), 201)
    const source = await created.json()
    assert.ok((await context.request.patch('/api/online/settings', { headers, data: { enabled: true } })).ok())
    let saved = false
    await context.route('**/*', route => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== base) { errors.push('Unexpected origin ' + url.origin); return route.abort() }
      if (url.pathname === '/api/ai/assistant') {
        requests.push('chat')
        return route.fulfill({ json: { mode: 'local', diagnose_online: true, answer: '我来检查来源和播放线路。' } })
      }
      if (url.pathname === '/api/online/sessions' && request.method() === 'POST') {
        assert.equal(request.headers()['x-online-playback-context'], 'cache')
        assert.equal(request.headers()['x-online-cache-only'], undefined)
        sessions.push(request.postDataJSON())
        if (!saved) return route.fulfill({ status: 502, json: { code: 'provider_timeout' } })
      }
      if (url.pathname === '/api/online/sources/' + source.id + '/diagnosis') {
        const body = request.postDataJSON()
        requests.push(body)
        return route.fulfill({ json: body.compare_direct
          ? { answer: '直连读到了原线路的媒体样本。', evidence: [], proposal: { kind: 'media_direct', id: 'pending', changes: [], expires_at: Math.floor(Date.now() / 1000) + 60 } }
          : { answer: '代理未能取得这一集的媒体。', evidence: [], can_compare_direct: true } })
      }
      if (url.pathname === '/api/online/sources/' + source.id + '/diagnosis/pending/confirm') {
        requests.push({ confirm: request.postDataJSON() })
        saved = true
        return route.fulfill({ json: { status: 'applied', answer: '已保存并复测。', source, evidence: [] } })
      }
      return route.continue()
    })
    await page.goto('/')
    await page.waitForFunction(() => window.__jzOnlineFixture?.snapshot().ready)
    const detail = page.waitForResponse(response => new URL(response.url()).pathname === '/api/online/catalog/detail')
    await page.evaluate(id => window.__jzOnlineFixture.navigate('/online?source=' + id + '&item=show-1'), source.id)
    const data = await (await detail).json()
    const line = data.lines.find(row => row.entries[1]?.supported && row.entries[1].transport === 'mp4')
    assert.ok(line, 'Fixture offers a playable second episode on an MP4 line')
    await page.locator('[data-online-line="' + line.id + '"]').click()
    const expected = line.entries[1].identity
    await page.locator('[data-online-entry-index="1"]').click()
    const player = page.locator('.online-player-dlg')
    await player.locator('.online-player-error').waitFor()
    assert.deepEqual(sessions, [expected])
    if (entry === 'detail') {
      await page.keyboard.press('Escape')
      await player.waitFor({ state: 'hidden' })
      await page.getByRole('button', { name: '打开小映', exact: true }).click()
    } else await player.getByRole('button', { name: '问小映', exact: true }).click()
    const question = '为什么这一集播不了？', input = page.locator('#assistant-question')
    await input.fill(question); await input.press('Enter')
    const card = page.locator('[data-assistant-diagnosis]')
    const compare = card.getByRole('button', { name: '允许临时直连测试本集' })
    const confirm = card.getByRole('button', { name: '同意修改' })
    await compare.waitFor()
    const body = { question, external_id: 'show-1', item_id: expected.item_id, line_id: line.id,
      episode_id: expected.episode_id, error_code: 'provider_timeout', cache_only: false }
    assert.deepEqual(requests, ['chat', body])
    assert.equal(await confirm.count(), 0)
    await compare.click(); await confirm.waitFor()
    assert.deepEqual(requests, ['chat', body, { ...body, compare_direct: true }])
    assert.equal(saved, false)
    await input.fill('同意'); await input.press('Enter')
    const replay = card.getByRole('button', { name: '重新播放' })
    await replay.waitFor()
    assert.deepEqual(requests, ['chat', body, { ...body, compare_direct: true }, { confirm: {} }])
    assert.deepEqual(sessions, [expected], 'Saving never starts another session')
    const reopened = page.waitForResponse(response => new URL(response.url()).pathname === '/api/online/sessions' &&
      response.request().method() === 'POST' && response.ok())
    await replay.click()
    assert.equal((await (await reopened).json()).cached, false, 'The new web client requests the actual session cache state')
    await page.waitForFunction(() => document.querySelector('.online-player-dlg video')?.currentTime > 0.25)
    assert.deepEqual(sessions, [expected, expected])
    assert.deepEqual(errors, [])
    await context.close()
    console.log('PASS ' + entry + ' · 诊断原分集、单次直连、同意保存、手动重播后真实画面推进')
  }
} finally {
  await browser?.close()
  await stopOwned(backend)
  await rm(work, { recursive: true, force: true })
}
