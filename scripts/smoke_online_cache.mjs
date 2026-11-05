// Real Vue/router/player, synthetic MP4 and an in-memory cache API only.
// node scripts/smoke_online_cache.mjs
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { createFixture } from './fixtures/uiReviewData.mjs'

const root = fileURLToPath(new URL('../', import.meta.url)), work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-online-cache-'))
const output = path.join(root, 'output/playwright/online-cache'), dist = path.join(work, 'dist')
const SOURCE = 'src_' + '1'.repeat(32), ITEM = 'itm_' + '2'.repeat(32), LINE = 'ln_' + '3'.repeat(32), scope = 'a'.repeat(64)
const identity = number => ({ source_id: SOURCE, item_id: ITEM, line_id: LINE, kind: 'episode', episode_id: 'ep_' + number.toString(16).padStart(32, '0') })
const detail = { source: { id: SOURCE, name: '海岸影库' }, item: { id: ITEM, source_id: SOURCE, kind: 'tv', title: '群岛之间 · 缓存演示' },
  year: '2026', description: '本页只使用隔离的虚构剧集和本机生成的测试画面。', attribution: [],
  lines: [{ id: LINE, label: '高清线路', entries: Array.from({ length: 123 }, (_, index) => ({ label: `第${index + 1}集`, supported: true, transport: 'mp4', identity: identity(index + 1) })) }] }
function entry(number, state) { return { id: 'cache_' + number, task_id: 'cache_' + number, identity: identity(number), label: `第${number}集`, line_label: '高清线路', state,
  downloaded_bytes: state === 'ready' ? 25 * 1024 ** 2 : 10 * 1024 ** 2, total_bytes: 25 * 1024 ** 2, completed_resources: state === 'ready' ? 10 : 4, total_resources: 10,
  speed_bytes_per_second: state === 'downloading' ? 1024 ** 2 : 0, error_code: state === 'failed' ? 'online_cache_corrupt' : '' } }
const fixture = createFixture(), sessions = new Map(), report = { generated_at: new Date().toISOString(), scope: 'Synthetic fixtures only; Chromium desktop and mobile emulation, not a physical device.', checks: [], errors: [], unexpected: [] }
let entries = [], requests = [], onlineEnabled = true, browser, server, media, failure
function reset() { entries = [entry(1, 'ready'), entry(2, 'paused'), entry(3, 'failed'), entry(4, 'downloading')]; requests = []; onlineEnabled = true; sessions.clear() }
function api(url, method, body, headers = {}) {
  const key = url.pathname
  requests.push({ key, method, body, cache_only: headers['x-online-cache-only'] })
  if (key === '/api/online/cache/items') return { enabled: onlineEnabled, storage: { used_bytes: 55 * 1024 ** 2, available_bytes: 120 * 1024 ** 3, min_free_bytes: 5 * 1024 ** 3, ok: true },
    items: entries.length ? [{ item_id: ITEM, source_id: SOURCE, external_id: 'fixture-show', title: detail.item.title, source_name: detail.source.name,
      poster_path: '', ready_count: entries.filter(row => row.state === 'ready').length, total_count: entries.length, entries }] : [] }
  if (key === '/api/online/cache/items/' + ITEM || key === '/api/online/cache/lookup') return { enabled: onlineEnabled, detail, external_id: 'fixture-show', entries }
  if (key === '/api/online/cache/tasks') {
    const tasks = body.episode_ids.map(ep => { const number = parseInt(ep.slice(3), 16); const value = entry(number, 'queued'); entries.push(value); return value })
    return { tasks }
  }
  const action = /^\/api\/online\/cache\/tasks\/(cache_\d+)\/actions$/.exec(key)
  if (action) { const task = entries.find(row => row.id === action[1]); task.state = body.action === 'pause' ? 'paused' : 'queued'; task.error_code = ''; return task }
  if (key === '/api/online/cache/entries/delete') { entries = entries.filter(row => !body.ids.includes(row.id)); return { results: body.ids.map(id => ({ id, deleted: true, error_code: '' })) } }
  if (key === '/api/online/catalog/sources') return { enabled: true, items: [{ id: SOURCE, name: detail.source.name, provider: 'maccms', capabilities: { browse: true } }], scope_token: scope }
  if (key === '/api/online/catalog/browse' || key === '/api/online/catalog/search') return { source: detail.source, categories: [], items: [{ external_id: 'fixture-show', title: detail.item.title, kind: 'tv', poster_path: '' }], total: 1, page: 1, page_count: 1 }
  if (key === '/api/online/catalog/detail') return detail
  if (key === '/api/online/catalog/health') return { purposes: {} }
  if (key === '/api/online/catalog/related') return { source: detail.source, external_id: 'fixture-show', status: 'unsupported' }
  if (key === '/api/online/state/library') return { items: [], total: 0, offset: 0, limit: 20, scope_token: scope }
  if (key === '/api/online/state/items/' + ITEM) return { item_id: ITEM, favorite: false, last_progress: null, selected_progress: null, available: true, scope_token: scope }
  if (key === '/api/online/sessions' && method === 'POST') {
    const sid = ('session-' + sessions.size).padEnd(40, 'x'); sessions.set(sid, body)
    return { identity: body, session_id: sid, media_url: `/api/online/sessions/${sid}/media`, transport: 'mp4', processing: 'passthrough', seekable: true, duration_seconds: 3, time_offset_seconds: 0, expires_at: Math.floor(Date.now() / 1000) + 3600 }
  }
  if (/^\/api\/online\/sessions\/[\w-]+$/.test(key) && method === 'DELETE') return {}
  if (key === '/api/online/state/progress/claim') return { identity: sessions.get(body.session_id), writer_id: body.session_id, writer_revision: body.expected_revision + 1, scope_token: scope }
  if (key === '/api/online/state/progress') return { progress: { identity: sessions.get(body.writer_id), ...body, updated_at: Date.now() / 1000 }, scope_token: scope }
  const value = fixture.api(url, method, body)
  if (value === undefined) report.unexpected.push(method + ' ' + key)
  return value
}
async function screenshot(page, name, width) {
  await page.screenshot({ path: path.join(output, `${name}-${width}.png`), animations: 'disabled' })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name}: no horizontal overflow at ${width}`)
}
async function command(executable, args) {
  const child = spawn(executable, args, { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] })
  let stdout = '', stderr = ''
  child.stdout.on('data', chunk => { stdout += chunk }); child.stderr.on('data', chunk => { stderr += chunk })
  const code = await new Promise((resolve, reject) => { child.once('exit', resolve); child.once('error', reject) })
  assert.equal(code, 0, stderr)
  return stdout.trim()
}
async function fingerprint(directory) {
  const hash = createHash('sha256')
  async function visit(current) {
    for (const entry of (await readdir(current, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const absolute = path.join(current, entry.name)
      if (entry.isDirectory()) await visit(absolute)
      else { hash.update(path.relative(directory, absolute)); hash.update(await readFile(absolute)) }
    }
  }
  await visit(directory)
  return hash.digest('hex')
}
try {
  await mkdir(output, { recursive: true })
  report.source_sha256 = await fingerprint(path.join(root, 'frontend/src'))
  const ffmpeg = await command(path.join(root, '.venv/bin/python'), ['-c', 'from scripts.preview_onboarding import installed_binary; print(installed_binary("ffmpeg"))'])
  const mediaPath = path.join(work, 'synthetic.mp4')
  await command(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24', '-t', '3', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mediaPath])
  media = await readFile(mediaPath)
  await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false, cacheDir: path.join(work, 'cache'), plugins: [vue()], worker: { format: 'es' }, logLevel: 'error', build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false } })
  server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://127.0.0.1'), key = url.pathname
      if (/^\/api\/online\/sessions\/[\w-]+\/media$/.test(key)) {
        const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '')
        const start = range ? Number(range[1]) : 0, end = range?.[2] ? Math.min(media.length - 1, Number(range[2])) : media.length - 1
        res.writeHead(range ? 206 : 200, { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1, ...(range ? { 'Content-Range': `bytes ${start}-${end}/${media.length}` } : {}) }); res.end(media.subarray(start, end + 1)); return
      }
      if (key.startsWith('/api/')) {
        const chunks = []; for await (const chunk of req) chunks.push(chunk)
        const raw = Buffer.concat(chunks).toString(), value = api(url, req.method, raw ? JSON.parse(raw) : undefined, req.headers)
        res.writeHead(value === undefined ? 501 : 200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value ?? {})); return
      }
      const file = key.startsWith('/assets/') || /^\/(?:favicon[^/]*|logo\.svg|icon-512\.png|apple-touch-icon\.png)$/.test(key) ? path.join(dist, key) : ['/online', '/'].includes(key) ? path.join(dist, 'index.html') : null
      if (!file) { report.unexpected.push(req.method + ' ' + key); res.writeHead(404); res.end(); return }
      res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' })[path.extname(file)] || 'application/octet-stream' }); res.end(await readFile(file))
    } catch (error) { report.unexpected.push(String(error)); res.writeHead(500); res.end() }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = `http://127.0.0.1:${server.address().port}`
  browser = await chromium.launch({ headless: true })
  for (const width of [1440, 390]) {
    reset()
    const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce' }), page = await context.newPage()
    page.setDefaultTimeout(12000)
    page.on('pageerror', error => report.errors.push(String(error)))
    page.on('console', message => { if (message.type() === 'error') report.errors.push(message.text()) })
    page.on('request', request => { if (new URL(request.url()).origin !== base) report.unexpected.push(request.url()) })
    await page.goto(base + '/online?view=cache')
    await page.getByRole('heading', { name: '本地缓存', exact: true }).waitFor()
    await page.locator('[data-online-cache-entry="cache_1"]').waitFor()
    assert.equal(requests.some(row => row.key.includes('/catalog/')), false)
    await screenshot(page, 'cache-list', width)
    await page.getByRole('button', { name: '暂停', exact: true }).click()
    await page.locator('[data-online-cache-entry="cache_4"] [data-online-cache-state="paused"]').waitFor()
    await page.locator('[data-online-cache-entry="cache_2"]').getByRole('button', { name: '继续缓存' }).click()
    await page.locator('[data-online-cache-entry="cache_2"] [data-online-cache-state="queued"]').waitFor()
    await page.locator('[data-online-cache-entry="cache_3"]').getByRole('button', { name: '重试', exact: true }).click()
    await page.locator('[data-online-cache-entry="cache_3"] [data-online-cache-state="queued"]').waitFor()
    await page.locator('[data-online-cache-detail]').click()
    await page.locator('[data-online-cache-detail-note]').waitFor()
    await page.locator('[data-online-cache-create]').click()
    const dialog = page.getByRole('dialog', { name: '缓存到本地' })
    await dialog.locator('[data-online-cache-select="4"]').check()
    await dialog.locator('[data-online-cache-group]').selectOption('2')
    await dialog.locator('[data-online-cache-select="122"]').check()
    await screenshot(page, 'cache-selection', width)
    await dialog.locator('[data-online-cache-submit]').click()
    await dialog.waitFor({ state: 'hidden' })
    const queued = requests.filter(row => row.key === '/api/online/cache/tasks').at(-1).body
    assert.deepEqual(queued.episode_ids, [identity(5).episode_id, identity(123).episode_id])
    await page.locator('[data-online-autoplay]').check()
    await page.locator('[data-online-entry="第1集"]').click()
    await page.waitForFunction(() => { const video = document.querySelector('video'); return video && video.currentTime > .3 })
    await page.locator('[data-online-cache-network-confirm]').waitFor()
    assert.ok(requests.filter(row => row.key === '/api/online/sessions' && row.method === 'POST').every(row => row.cache_only === 'true'))
    assert.equal(requests.some(row => row.key === '/api/online/catalog/detail'), false)
    await page.getByRole('button', { name: '暂不播放', exact: true }).click()
    await page.getByRole('button', { name: '返回本地缓存', exact: true }).click()
    onlineEnabled = false
    await page.getByRole('button', { name: '刷新缓存', exact: true }).click()
    await page.locator('[data-online-cache-disabled]').waitFor()
    assert.equal(await page.locator('[data-online-cache-play="cache_1"]').isDisabled(), true)
    assert.equal(await page.getByRole('button', { name: '删除整剧', exact: false }).isEnabled(), true)
    await page.getByRole('button', { name: '删除整剧', exact: false }).click()
    const deleting = page.getByRole('dialog', { name: '删除本地缓存' })
    await deleting.waitFor()
    assert.equal(requests.some(row => row.key === '/api/online/cache/entries/delete'), false)
    assert.match(await deleting.innerText(), /共 6 集，占用 75.0 MiB/)
    assert.match(await deleting.innerText(), /停止相关设备的播放/)
    await screenshot(page, 'cache-delete-confirm', width)
    await page.keyboard.press('Escape'); await deleting.waitFor({ state: 'hidden' })
    assert.equal(await page.getByRole('button', { name: '删除整剧', exact: false }).evaluate(element => element === document.activeElement), true)
    await page.getByRole('button', { name: '删除整剧', exact: false }).click()
    await page.locator('[data-online-cache-delete-confirm]').click()
    await page.getByRole('heading', { name: '还没有本地缓存' }).waitFor()
    if (width < 700) {
      const small = await page.locator('button:visible, .online-cache-entry > label:visible').evaluateAll(elements => elements.map(element => ({ text: element.textContent, height: element.getBoundingClientRect().height })).filter(value => value.height < 43.5))
      assert.deepEqual(small, [], 'Phone action targets retain 44px height')
    }
    report.checks.push({ width, actual_mp4_decoded: true, offline_catalog_requests: requests.filter(row => row.key.includes('/catalog/')).length, selection: queued.episode_ids, confirmed_delete: true })
    await context.close()
  }
  assert.deepEqual(report.errors, [])
  assert.deepEqual(report.unexpected, [])
} catch (error) { failure = error; report.failure = String(error) }
finally {
  await browser?.close()
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) }
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2))
  await rm(work, { recursive: true, force: true })
}
if (failure) throw failure
console.log('PASS online cache desktop/mobile fixture, actual synthetic MP4, selection/actions/delete/focus')
