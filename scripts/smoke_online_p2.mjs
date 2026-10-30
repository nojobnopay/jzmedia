// Formal online gateway + OnlinePlayerModal, temporary DB and generated media only.
// Run under the repository's bounded validation wrapper. No production service is used.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const argv = process.argv.slice(2)
let selectedCase = 'all', demo = false
for (let index = 0; index < argv.length; index++) {
  const value = argv[index]
  if (value === '--demo') demo = true
  else if (value === '--case') selectedCase = argv[++index]
  else if (value.startsWith('--case=')) selectedCase = value.slice(7)
  else if (value === '--help') {
    console.log('Usage: node scripts/smoke_online_p2.mjs [--demo] [--case matrix|catalogue|discovery|loading|image-cache|categories|related|detail|detail-recovery|metadata-recovery|refresh-strategy|hosts|all]')
    process.exit(0)
  } else throw new Error(`Unknown argument: ${value}`)
}
assert.ok(['matrix', 'catalogue', 'discovery', 'loading', 'image-cache', 'categories', 'related', 'detail', 'detail-recovery', 'metadata-recovery', 'refresh-strategy', 'hosts', 'all'].includes(selectedCase), 'Invalid --case')
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-online-p2-'))
const output = path.join(root, 'output/playwright/online-p2')
const dist = path.join(work, 'dist')
const entryId = 'virtual:jzmedia-online-p2'
const report = { generatedAt: new Date().toISOString(), case: selectedCase,
  demo,
  scope: 'Production MacCMS parser, catalogue/source pages and formal online gateway with synthetic media and a temporary database. Desktop Chromium viewport/touch emulation; no real phone/TV, public source or human audio acceptance.',
  fingerprints: {}, checks: [], errors: [], unexpectedRequests: [], unexpectedResponses: [],
  expectedFailures: { responses: [], console: [] }, abortedRequests: [], backendChildrenChecks: [],
  cleanup: {} }
let backend = null, generator = null, browser = null, backendLog = '', failure = null
let base = '', stopping = false, phase = { name: 'setup', expectedHttp: false }
const contexts = new Set()
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))
const stopped = child => !child || child.exitCode !== null || child.signalCode !== null
function passed(check) { report.checks.push(check); console.log(`PASS online P2 ${check.case}`) }

function killOwned(child, signal) {
  if (!child?.pid || stopped(child)) return
  try { process.kill(-child.pid, signal) } catch { try { child.kill(signal) } catch { /* Already stopped. */ } }
}
function interrupted() {
  stopping = true
  killOwned(generator, 'SIGTERM')
  if (!demo) {
    killOwned(backend, 'SIGTERM')
    void browser?.close().catch(() => {})
  }
}
process.on('SIGINT', interrupted)
process.on('SIGTERM', interrupted)
function checkStop() { if (stopping) throw new Error('Validation interrupted') }

async function freePort() {
  const server = createServer()
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  return port
}
async function until(predicate, message, timeout = 12000) {
  const deadline = Date.now() + timeout
  let lastError
  while (Date.now() < deadline) {
    checkStop()
    try { const value = await predicate(); if (value) return value } catch (error) { lastError = error }
    await delay(60)
  }
  throw new Error(`${message}${lastError ? `: ${lastError.message}` : ''}`)
}
async function metrics() {
  const response = await fetch(`${base}/api/p2/metrics`, { signal: AbortSignal.timeout(2000) })
  assert.equal(response.status, 200)
  const value = await response.json()
  report.lastMetrics = value
  return value
}
async function idle(message = 'All formal sessions, workers and reservations must be released') {
  const started = performance.now()
  const value = await until(async () => {
    const value = await metrics()
    return value.registry.sessions === 0 && value.registry.active_requests === 0
      && value.registry.reserved_bytes === 0 && value.pending_requests === 0
      && value.catalogue_active === 0 && value.catalogue_owners === 0
      && value.api_pool.active === 0
      && value.image_pool.active === 0
      && value.artwork_cache.inflight === 0 && value.artwork_cache.pending === 0 ? value : false
  }, message)
  assert.deepEqual(value.local_rows, { movies: 0, tv_episodes: 0, playback_progress: 0 })
  return { releaseMs: Math.round(performance.now() - started), metrics: value }
}
async function scenario(page, plan) {
  const response = await page.evaluate(async plan => {
    const response = await fetch('/api/p2/scenario', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Api-Token': localStorage.getItem('jzmedia.token') || '' },
      body: JSON.stringify(plan) })
    return { status: response.status, body: await response.json() }
  }, plan)
  assert.equal(response.status, 200, JSON.stringify(response))
}
async function backendChildren(label) {
  const children = new Set()
  const tasks = await readdir(`/proc/${backend.pid}/task`)
  for (const task of tasks) {
    try {
      for (const pid of (await readFile(`/proc/${backend.pid}/task/${task}/children`, 'utf8')).trim().split(/\s+/)) {
        if (pid) children.add(Number(pid))
      }
    } catch (error) { if (error.code !== 'ENOENT' && error.code !== 'ESRCH') throw error }
  }
  const values = [...children]
  report.backendChildrenChecks.push({ label, children: values })
  assert.deepEqual(values, [], 'The serving gateway must never start FFmpeg or any media-processing child')
}
async function mediaHashes(folder) {
  const result = {}
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name)
      assert.equal(entry.isSymbolicLink(), false, 'Fixture media must not include symlinks')
      if (entry.isDirectory()) await visit(file)
      else if (entry.isFile()) result[path.relative(folder, file)] = createHash('sha256').update(await readFile(file)).digest('hex')
    }
  }
  await visit(folder)
  return Object.fromEntries(Object.entries(result).sort(([first], [second]) => first.localeCompare(second)))
}
async function runGenerator() {
  let log = ''
  generator = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_p2.server', '--root', work, '--generate-media'],
    { cwd: root, env: { ...process.env, PYTHONUNBUFFERED: '1' }, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  generator.stdout.on('data', data => { log = (log + data.toString()).slice(-10000) })
  generator.stderr.on('data', data => { log = (log + data.toString()).slice(-10000) })
  const code = await new Promise((resolve, reject) => { generator.once('error', reject); generator.once('exit', resolve) })
  assert.equal(code, 0, `Synthetic generation failed: ${log}`)
  report.mediaHashesBefore = await mediaHashes(path.join(work, 'media'))
}
async function buildFixture() {
  const source = `
import { createApp, h } from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/tokens.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/base.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/browse.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/settings.css'))};
import { createMemoryHistory, createRouter } from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue-router/dist/vue-router.mjs'))};
import Preview from ${JSON.stringify(path.join(root, 'scripts/fixtures/OnlineP2Preview.vue'))};
import Online from ${JSON.stringify(path.join(root, 'frontend/src/views/Online.vue'))};
import Sources from ${JSON.stringify(path.join(root, 'frontend/src/components/OnlineSourcesPanel.vue'))};
const router = createRouter({history:createMemoryHistory(), routes:[{path:'/online',component:Online},{path:'/settings',component:{render:()=>h('div',{class:'settings-layout',style:{display:'block',padding:'20px 0'}},[h('div',{class:'settings-main'},[h(Sources)])])}},{path:'/:pathMatch(.*)*',redirect:'/online'}]});
router.push('/online').then(() => createApp(Preview).use(router).mount('#app'));`
  const result = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    publicDir: path.join(root, 'frontend/public'), cacheDir: path.join(work, 'vite-cache'), logLevel: 'error',
    define: { 'import.meta.env.DEV': 'true' }, worker: { format: 'es' },
    resolve: { alias: { vue: path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'),
      'hls.js': path.join(root, 'frontend/node_modules/hls.js/dist/hls.mjs'),
      'vue-router': path.join(root, 'frontend/node_modules/vue-router/dist/vue-router.mjs') } },
    plugins: [vue(), { name: 'online-p2-preview', resolveId: id => id === entryId ? '\0' + entryId : null,
      load: id => id === '\0' + entryId ? source : null }],
    build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false,
      rollupOptions: { input: { preview: entryId } } } })
  const entry = result.output.find(item => item.type === 'chunk' && item.isEntry).fileName
  const css = result.output.filter(item => item.type === 'asset' && item.fileName.endsWith('.css')).map(item => item.fileName)
  await writeFile(path.join(dist, 'index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width,initial-scale=1"><title>jzmedia · 在线来源隔离验证</title>'
    + '<link rel="icon" href="data:,">' + css.map(file => `<link rel="stylesheet" href="/${file}">`).join('')
    + `</head><body><div id="app"></div><script type="module" src="/${entry}"></script></body></html>`)
}
async function startBackend() {
  const port = await freePort()
  base = `http://127.0.0.1:${port}`
  backend = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_p2.server', '--root', work,
    '--port', String(port), '--static-dir', dist],
  { cwd: root, env: { ...process.env, PYTHONUNBUFFERED: '1' }, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  backend.stdout.on('data', data => { backendLog = (backendLog + data.toString()).slice(-16000) })
  backend.stderr.on('data', data => { backendLog = (backendLog + data.toString()).slice(-16000) })
  await until(async () => {
    if (stopped(backend)) throw new Error(`Fixture backend exited: ${backendLog}`)
    return (await fetch(`${base}/api/p2/health`, { signal: AbortSignal.timeout(1000) })).ok
  }, `Fixture backend must become ready: ${backendLog}`, 20000)
  await backendChildren('after-start-no-media-generation')
}
function trackPage(page) {
  page.setDefaultTimeout(20000)
  page.on('pageerror', error => report.errors.push({ phase: phase.name, type: 'pageerror', message: String(error) }))
  page.on('console', message => {
    const text = message.text()
    if (message.type() === 'error') {
      const item = { phase: phase.name, message: text }
      if (phase.expectedHttp && /Failed to load resource|MEDIA_ERR|DEMUXER_ERROR|PIPELINE_ERROR/i.test(text)) report.expectedFailures.console.push(item)
      else report.errors.push({ ...item, type: 'console' })
    } else if (/\[Vue warn\].*(Unhandled error|Failed to resolve|Missing required|Invalid prop)/i.test(text)) {
      report.errors.push({ phase: phase.name, type: 'vue-runtime', message: text })
    }
  })
  page.on('response', response => {
    if (response.status() < 400) return
    const item = { phase: phase.name, path: new URL(response.url()).pathname, status: response.status() }
    if (phase.expectedHttp) report.expectedFailures.responses.push(item)
    else report.unexpectedResponses.push(item)
  })
  page.on('requestfailed', request => {
    const item = { phase: phase.name, path: new URL(request.url()).pathname, reason: request.failure()?.errorText || '' }
    if (/ABORTED|CANCELED|CANCELLED/i.test(item.reason)) report.abortedRequests.push(item)
    else if (!phase.expectedHttp) report.errors.push({ ...item, type: 'requestfailed' })
  })
}
async function newPage(width = 1440, { init, hasTouch = width <= 700 } = {}) {
  const context = await browser.newContext({ viewport: { width, height: width > 700 ? 1040 : 844 },
    hasTouch, serviceWorkers: 'block' })
  contexts.add(context)
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (!['data:', 'blob:'].includes(url.protocol) && url.origin !== base) {
      report.unexpectedRequests.push({ phase: phase.name, reason: 'foreign-origin', url: url.href })
      return route.abort()
    }
    if (/^\/api\/(stream|cast)(?:\/|$)/.test(url.pathname)) {
      report.unexpectedRequests.push({ phase: phase.name, reason: 'local-playback-api', path: url.pathname })
      return route.abort()
    }
    return route.continue()
  })
  if (init) await context.addInitScript(init)
  const page = await context.newPage()
  trackPage(page)
  await page.goto(base)
  await page.waitForFunction(() => window.__jzOnlineP2Fixture?.snapshot().ready)
  return { context, page }
}
async function closeContext(context) {
  await context.close()
  contexts.delete(context)
}
async function readPlayer(page) {
  return page.locator('video').evaluate(video => ({ time: video.currentTime, paused: video.paused,
    ready: video.readyState, width: video.videoWidth, height: video.videoHeight,
    frames: video.getVideoPlaybackQuality?.().totalVideoFrames || 0, duration: video.duration,
    muted: video.muted, volume: video.volume, rate: video.playbackRate, src: video.currentSrc,
    error: video.error?.code || null }))
}
async function snapshot(page) { return page.evaluate(() => window.__jzOnlinePlayer?.snapshot() || null) }
async function loaded(page) {
  try {
    await page.waitForFunction(() => {
      const video = document.querySelector('video'), state = window.__jzOnlinePlayer?.snapshot()
      return state?.firstFrame && !state.booting && video?.readyState >= 2 && video.videoWidth > 0
        && video.getVideoPlaybackQuality?.().totalVideoFrames > 0
    })
  } catch (error) {
    report.playerFailure = { state: await snapshot(page).catch(() => null),
      media: await readPlayer(page).catch(() => null), metrics: await metrics().catch(() => null) }
    await page.screenshot({ path: path.join(output, `${selectedCase}-failed-player.png`) }).catch(() => {})
    throw error
  }
  const value = await readPlayer(page)
  assert.equal(value.error, null)
  return value
}
async function advancing(page, minimum = .4) {
  await loaded(page)
  await page.waitForFunction(value => document.querySelector('video')?.currentTime > value, minimum)
  const before = await readPlayer(page)
  await page.waitForFunction(value => {
    const video = document.querySelector('video')
    return video && !video.paused && video.currentTime > value + .35
  }, before.time)
  const after = await readPlayer(page)
  assert.ok(after.frames > before.frames && after.time > before.time, 'Decoded frames and actual media clock must advance')
  return { before, after }
}
async function open(page, key) {
  const started = performance.now()
  await page.locator(`[data-online-item="${key}"]`).click()
  await page.getByRole('dialog').waitFor()
  await loaded(page)
  return Math.round(performance.now() - started)
}
async function pause(page) {
  if (!(await readPlayer(page)).paused) await page.getByRole('dialog').getByRole('button', { name: '暂停', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('video')?.paused === true)
  return readPlayer(page)
}
async function closePlayer(page, { keyboard = false, focusKey = null } = {}) {
  if (keyboard) await page.keyboard.press('Escape')
  else await page.getByRole('dialog').getByRole('button', { name: '关闭播放器', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.equal(await page.locator('video').count(), 0, 'Closed player detaches its video element')
  assert.equal(await page.evaluate(() => window.__jzOnlinePlayer || null), null)
  if (focusKey) await page.waitForFunction(key => document.activeElement?.dataset.onlineItem === key, focusKey)
  return idle()
}
async function layout(page, width) {
  const state = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth + 1,
    buttons: [...document.querySelectorAll('button')].filter(button => {
      const rect = button.getBoundingClientRect()
      return rect.width && rect.height && getComputedStyle(button).visibility !== 'hidden'
        && !button.closest('[inert]') && button.getAttribute('aria-hidden') !== 'true'
    }).map(button => { const rect = button.getBoundingClientRect(); return {
      label: button.getAttribute('aria-label') || button.textContent.trim(), width: rect.width, height: rect.height } }),
    navTargets: [...document.querySelectorAll('.app-nav a.nav-link')].map(link => {
      const rect = link.getBoundingClientRect(); return { label: link.textContent.trim(), width: rect.width, height: rect.height }
    }) }))
  assert.equal(state.overflow, false, `${width}px has no horizontal overflow`)
  if (width <= 700) {
    assert.deepEqual(state.buttons.filter(button => button.width < 43.5 || button.height < 43.5), [],
      'Every visible phone control retains a 44px touch target')
    assert.equal(state.navTargets.length, 6, 'The real App exposes all six normal-view navigation targets')
    assert.deepEqual(state.navTargets.filter(target => target.width < 43.5 || target.height < 43.5), [],
      'The real App navigation retains 44px phone targets')
  }
  return state
}
async function call(page, method, apiPath, body) {
  return page.evaluate(async ({ method, apiPath, body }) => {
    const response = await fetch(apiPath, { method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Api-Token': localStorage.getItem('jzmedia.token') || '' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    const text = await response.text()
    let value
    try { value = JSON.parse(text) } catch { value = text }
    return { status: response.status, body: value }
  }, { method, apiPath, body })
}
function assertPrivateCatalogue(value) {
  const text = JSON.stringify(value)
  for (const forbidden of ['fixture.invalid', 'fixture_signature', 'api_base_url', 'allowed_hosts', 'vod_play_url', 'X-Api-Token']) {
    assert.equal(text.includes(forbidden), false, `Catalogue DTO must not expose ${forbidden}`)
  }
}
async function navigate(page, pathName) {
  const label = pathName === '/settings' ? '设置' : '在线'
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: label, exact: true }).click()
  await page.waitForFunction(pathName => window.__jzOnlineP2Fixture.snapshot().path === pathName, pathName)
}
async function createSourceUi(page, name) {
  const bootstrap = (await call(page, 'GET', '/api/p2/bootstrap')).body
  await navigate(page, '/settings')
  await page.getByRole('button', { name: '添加来源', exact: true }).click()
  const form = page.locator('[data-online-source-form]')
  await form.getByLabel('来源名称', { exact: true }).fill(name)
  await form.getByLabel('提供方', { exact: true }).selectOption('maccms')
  await form.getByLabel('接口地址', { exact: true }).fill(bootstrap.api_base_url)
  await form.getByLabel('允许访问的主机', { exact: true }).fill('fixture.invalid')
  assert.equal(await form.getByLabel('启用此来源', { exact: true }).isChecked(), true, 'new sources are enabled by default')
  await form.getByRole('button', { name: '保存来源', exact: true }).click()
  const source = await until(async () => {
    const response = await call(page, 'GET', '/api/online/sources')
    return response.status === 200 && response.body.items.find(item => item.name === name && item.enabled)
  }, 'A source created by the real form must be persisted')
  const enable = page.getByRole('button', { name: '启用在线点播', exact: true })
  if (await enable.count()) await enable.click()
  await page.getByRole('button', { name: '关闭在线点播', exact: true }).waitFor()
  return source
}
let primarySource = null
async function selectSource(page, source = primarySource) {
  await navigate(page, '/online')
  const select = page.locator('[data-online-source]')
  if (!await select.isVisible() && await page.getByRole('button', { name: '返回搜索结果', exact: true }).isVisible()) await returned(page)
  await select.waitFor()
  await select.selectOption(source.id)
}
async function search(page, keyword = '合成', { results = true } = {}) {
  const form = page.locator('[data-online-search]')
  if (!await form.isVisible() && await page.getByRole('button', { name: '返回搜索结果', exact: true }).isVisible()) {
    await returned(page)
    await until(async () => (await page.locator('[data-online-browser]').getAttribute('aria-busy')) === 'false',
      'The restored directory settles before a different search')
  }
  await form.waitFor()
  await form.locator('input').first().fill(keyword)
  await form.locator('input').first().press('Enter')
  if (results) await page.locator('[data-online-result]').first().waitFor()
}
async function detailUi(page, externalId = 'movie-1') {
  await page.locator(`[data-online-result="${externalId}"]`).click()
  await page.locator('[data-online-detail]').waitFor()
}
async function chooseLine(page, label) {
  await page.locator('[data-online-line]').filter({ hasText: label }).click()
}
async function playEntry(page, label = '正片') {
  const started = performance.now()
  await page.locator('[data-online-entry]').filter({ hasText: label }).click()
  await page.getByRole('dialog').waitFor()
  await loaded(page)
  return Math.round(performance.now() - started)
}
async function returned(page) {
  await page.getByRole('button', { name: '返回搜索结果', exact: true }).click()
  await page.locator('[data-online-result]').first().waitFor()
}
async function setup() {
  phase = { name: 'source-management-ui', expectedHttp: false }
  const { context, page } = await newPage()
  try {
    const disabled = await call(page, 'GET', '/api/online/catalog/sources')
    assert.equal(disabled.body.enabled, false)
    assert.deepEqual(disabled.body.items, [])
    assert.match(disabled.body.scope_token, /^[0-9a-f]{64}$/)
    primarySource = await createSourceUi(page, '隔离合成MacCMS')
    await page.getByRole('button', { name: '网络 ' + primarySource.name, exact: true }).click()
    const network = page.locator('[data-online-network-form]')
    await network.locator('summary').filter({ hasText: '高级：按用途分别配置' }).click()
    for (const label of ['来源默认', '搜索与详情', '视频播放']) await network.getByLabel(label, { exact: true }).selectOption('direct')
    await network.getByRole('button', { name: '保存网络规则', exact: true }).click()
    await network.waitFor({ state: 'hidden' })
    const policy = await call(page, 'GET', `/api/online/sources/${primarySource.id}/network`)
    assert.equal(policy.status, 200, JSON.stringify(policy))
    for (const key of ['default', 'api', 'media']) assert.equal(policy.body.policy[key].mode, 'direct')
    await page.getByRole('button', { name: '测试来源 ' + primarySource.name, exact: true }).click()
    await page.getByText('目录读取成功；可前往在线页选择影片试播。', { exact: true }).waitFor()
    await page.screenshot({ path: path.join(output, 'source-management-1440.png') })
    const publicSources = await call(page, 'GET', '/api/online/catalog/sources')
    assertPrivateCatalogue(publicSources.body)
    assert.equal(publicSources.body.items.length, 1)
    passed({ case: phase.name, sourceId: primarySource.id, explicitlyEnabled: true, networkPolicy: policy.body,
      scope: 'Source creation, feature enable and explicit API/media direct rules via the real management UI' })
  } finally { await closeContext(context) }
}
async function discovery() {
  for (const width of [1440, 390, 320]) {
    phase = { name: `discovery-${width}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    try {
      await selectSource(page)
      await page.locator('[data-online-result="movie-1"]').waitFor()
      await page.waitForFunction(() => [...document.querySelectorAll('.online-results .online-poster > img')]
        .filter(image => image.complete && image.naturalWidth > 0).length >= 2)
      assert.equal(await page.locator('.online-connection').evaluate(element => element.open), false)
      const geometry = await layout(page, width)
      const images = await page.locator('.online-results .online-poster > img').evaluateAll(elements => elements.map(image => ({
        url: image.src, decoded: image.complete && image.naturalWidth > 0,
      })))
      assert.ok(images.length >= 2 && images.every(image => image.decoded && image.url.startsWith(`blob:${base}/`)))
      await page.screenshot({ path: path.join(output, `discovery-${width}.png`), fullPage: true })
      await page.locator('[data-online-category="2"]').click()
      await page.waitForFunction(() => document.querySelectorAll('[data-online-result]').length === 1
        && document.querySelector('[data-online-result="show-1"]'))
      assert.equal(await page.locator('[data-online-category="2"]').getAttribute('aria-pressed'), 'true')
      passed({ case: phase.name, geometry, images, actualSourceCategoryApplied: true })
    } finally { await closeContext(context) }
  }
  await backendChildren('after-discovery')
}
async function categoryNavigation() {
  async function taxonomyScenario(taxonomy) {
    const response = await fetch(`${base}/api/p2/scenario`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Api-Token': 'online-p2-fixture-only' },
      body: JSON.stringify({ taxonomy, taxonomy_unavailable: false, gallery: false, image_delay_ms: 0 }) })
    assert.equal(response.status, 200)
  }
  async function settled(page) {
    await until(async () => (await page.locator('[data-online-browser]').getAttribute('aria-busy')) === 'false',
      'The selected directory request must finish')
  }
  async function categoryDialog(page) {
    await page.locator('[data-online-categories-open]:visible').first().click()
    const dialog = page.getByRole('dialog')
    await dialog.waitFor()
    return dialog
  }
  async function chooseCategory(page, id) {
    let button = page.locator(`[data-online-category="${id}"]:visible`).first()
    if (!await button.count()) button = (await categoryDialog(page)).locator(`[data-online-category="${id}"]`).first()
    await button.click()
    await settled(page)
  }
  async function chooseChannel(page, id) {
    let button = page.locator(`[data-online-channel="${id}"]:visible`).first()
    if (!await button.count()) {
      const dialog = await categoryDialog(page)
      const channel = dialog.locator(`[data-online-channel="${id}"]`).first()
      button = await channel.count() ? channel : dialog.locator(`[data-online-category="${id}"]`).first()
    }
    await button.click()
    await settled(page)
  }
  async function chooseYear(page, year) {
    const select = page.locator('[data-online-year]')
    const available = await select.locator('option').evaluateAll(options => options.map(option => option.value))
    if (available.includes(year)) await select.selectOption(year)
    else {
      await select.selectOption('custom')
      await page.locator('[data-online-year-custom]').fill(year)
      await page.getByRole('button', { name: '应用年份', exact: true }).click()
    }
    await settled(page)
  }
  async function resultIds(page) {
    return page.locator('[data-online-result]').evaluateAll(elements => elements.map(element => element.dataset.onlineResult))
  }
  for (const width of [1440, 390, 320]) {
    phase = { name: `categories-${width}`, expectedHttp: false }
    // No previous page has this device scope. Enabling the isolated feature
    // before boot also ensures capability discovery and the first tree agree.
    await taxonomyScenario(true)
    const { context, page } = await newPage(width)
    try {
      await selectSource(page)
      await page.locator('[data-online-year]').waitFor()
      await settled(page)
      // A valid filtered page can omit class while its optional taxonomy
      // discovery fails. Real metadata remains usable and the known source
      // navigation must survive every subsequent response and warm snapshot.
      await scenario(page, { taxonomy_unavailable: true, clear_taxonomy_cache: true })
      const movieResponse = page.waitForResponse(response => {
        const url = new URL(response.url())
        return url.pathname === '/api/online/catalog/browse' && url.searchParams.get('category_id') === '21'
      })
      await chooseChannel(page, '20')
      const movie = await movieResponse
      assert.equal(movie.status(), 200)
      assert.deepEqual((await movie.json()).categories, [], 'the real provider parser exposes the failed optional taxonomy as empty')
      assert.deepEqual(await resultIds(page), ['action-1'])
      assert.equal(await page.locator('[data-online-channel="20"]').getAttribute('aria-pressed'), 'true')
      assert.ok((await page.locator('[data-online-category-current]').textContent()).includes('动作片'))
      assert.equal(await page.locator('[data-online-categories-open]:visible').first().isEnabled(), true)
      const retainedDialog = await categoryDialog(page)
      await retainedDialog.locator('[data-online-category="74"]').waitFor()
      await page.keyboard.press('Escape')
      await retainedDialog.waitFor({ state: 'hidden' })
      assert.ok((await metrics()).origin_calls.some(call => call.category_discovery_failure),
        'the optional discovery request actually failed instead of a browser-only response mock')
      await chooseChannel(page, '58')
      await chooseCategory(page, '71')
      await chooseYear(page, '2026')
      const first = await resultIds(page)
      assert.equal(first.length, 20)
      assert.ok(first.every(id => id.startsWith('urban-2026-')))
      assert.ok((await page.locator('.online-results-heading').textContent()).includes('都市脑洞'))
      assert.ok((await page.locator('.online-results').textContent()).includes('更新至第2集'))
      const filtered = await metrics()
      assert.ok(filtered.origin_calls.some(call => call.kind === 'api' && call.category_id === '71' && call.year === '2026'),
        'The real source must receive the category and year; a current-page filter is insufficient')
      await page.getByRole('button', { name: '下一页', exact: true }).click()
      await page.locator('[data-online-result="urban-2026-24"]').waitFor()
      await settled(page)
      assert.equal((await resultIds(page)).length, 4)
      assert.ok((await page.locator('.online-pagination').first().textContent()).includes('2 / 2'))
      const last = page.locator('[data-online-result="urban-2026-24"]')
      await last.scrollIntoViewIfNeeded()
      await until(() => last.locator('.online-poster > img').evaluateAll(images => images.some(image => image.complete && image.naturalWidth > 0)),
        'A real source poster must decode on the client')
      const position = await page.evaluate(() => scrollY)
      await detailUi(page, 'urban-2026-24')
      const detail = await page.locator('[data-online-detail]').textContent()
      for (const value of ['合成演员甲', '合成导演', '大陆', '国语']) assert.ok(detail.includes(value), `Detail exposes available ${value}`)
      await returned(page)
      await until(() => page.evaluate(expected => Math.abs(scrollY - expected) < 12, position), 'Detail return restores the filtered-page anchor')
      assert.equal(await page.locator('[data-online-year]').inputValue(), '2026')
      assert.ok((await page.locator('.online-pagination').first().textContent()).includes('2 / 2'))
      await idle()
      const warmBefore = await metrics()
      await page.evaluate(() => window.__jzOnlineP2Fixture.navigate('/settings'))
      await page.waitForFunction(() => window.__jzOnlineP2Fixture.snapshot().path === '/settings')
      await page.evaluate(() => window.__jzOnlineP2Fixture.navigate('/online'))
      await page.locator('[data-online-result="urban-2026-24"]').waitFor({ state: 'attached' })
      await settled(page)
      await until(() => page.evaluate(expected => Math.abs(scrollY - expected) < 12, position), 'Reentry restores the year, category, page and anchor')
      assert.equal(await page.locator('[data-online-year]').inputValue(), '2026')
      assert.ok((await page.locator('.online-pagination').first().textContent()).includes('2 / 2'))
      assert.equal(await page.locator('[data-online-categories-open]:visible').first().isEnabled(), true,
        'warm reentry retains the known source taxonomy despite empty classifications on its cached page')
      assert.equal(await page.locator('[data-online-channel="58"]').getAttribute('aria-pressed'), 'true')
      assert.ok((await page.locator('[data-online-category-current]').textContent()).includes('都市脑洞'))
      const warmAfter = await metrics()
      assert.equal(warmAfter.origin_calls.filter(call => call.kind === 'api').length,
        warmBefore.origin_calls.filter(call => call.kind === 'api').length, 'The verified warm snapshot includes the selected year')

      await chooseCategory(page, '58')
      assert.deepEqual(await resultIds(page), ['short-overview'], 'The native parent query cannot masquerade as all short dramas')
      assert.equal((await page.locator('.online-page').innerText()).includes('全部短剧'), false)
      await chooseCategory(page, '71')
      await chooseYear(page, '1988')
      assert.deepEqual(await resultIds(page), ['urban-1988-1'])
      assert.equal(await page.locator('[data-online-year-custom]').inputValue(), '1988')
      const trigger = page.locator('[data-online-categories-open]:visible').first()
      const categoryReads = (await metrics()).origin_calls.filter(call => call.kind === 'api').length
      const dialog = await categoryDialog(page)
      await dialog.locator('[data-online-category-query]').fill('AI')
      await dialog.locator('[data-online-category="74"]').waitFor()
      assert.equal((await metrics()).origin_calls.filter(call => call.kind === 'api').length, categoryReads,
        'Finding a category in the complete local tree must not query every source category')
      await page.keyboard.press('Escape')
      await dialog.waitFor({ state: 'hidden' })
      assert.equal(await trigger.evaluate(element => document.activeElement === element), true, 'Escape returns keyboard focus to category selection')
      await chooseYear(page, '1900')
      await page.getByText('没有找到相关内容', { exact: true }).waitFor()
      assert.equal((await resultIds(page)).length, 0)
      await chooseYear(page, '2026')
      await page.locator('[data-online-result="urban-2026-1"]').waitFor()

      phase.expectedHttp = true
      await scenario(page, { metadata_failures_remaining: 2, metadata_failure_status: 503 })
      await page.locator('[data-online-refresh]').click()
      await page.getByRole('button', { name: '重试', exact: true }).waitFor()
      assert.equal((await resultIds(page)).length, 20, 'A failed refresh preserves the previous filtered wall')
      assert.equal(await page.locator('[data-online-year]').inputValue(), '2026')
      await page.getByRole('button', { name: '重试', exact: true }).click()
      await settled(page)
      phase.expectedHttp = false
      await scenario(page, { delay_ms: 900 })
      await page.locator('[data-online-refresh]').click()
      await until(async () => (await metrics()).pending_requests > 0, 'A delayed filtered request actually starts')
      assert.equal(await page.locator('[data-online-refresh]').isDisabled(), true)
      await page.evaluate(() => window.__jzOnlineP2Fixture.navigate('/settings'))
      await idle('Cancelled category work must release its actual worker')
      await page.evaluate(() => window.__jzOnlineP2Fixture.navigate('/online'))
      await page.locator('[data-online-result="urban-2026-1"]').waitFor()
      await settled(page)
      assert.equal(await page.locator('[data-online-year]').inputValue(), '2026')
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `categories-${width}.png`), fullPage: true })
      passed({ case: phase.name, geometry, taxonomyCategories: 49, actualYearForwarded: true,
        parentQueryRemainsExact: true, missingTaxonomyKeepsNavigation: true, optionalDiscoveryActuallyFailed: true,
        customYear: '1988', emptyYear: '1900', pageAndAnchorRestored: true,
        warmCatalogueReads: 0, escapeRestoresFocus: true, failedRefreshKeepsResults: true, cancelledLateRead: true })
    } finally {
      await closeContext(context)
      await idle()
      await taxonomyScenario(false)
    }
  }
  await backendChildren('after-categories')
}
async function relatedRecommendations() {
  const relatedPath = id => `/api/online/catalog/related?source_id=${primarySource.id}&external_id=${id}`
  const apiCalls = value => value.origin_calls.filter(call => call.kind === 'api')
  async function configure(plan) {
    const response = await fetch(`${base}/api/p2/scenario`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Api-Token': 'online-p2-fixture-only' },
      body: JSON.stringify({ related: true, taxonomy: false, gallery: false, related_delay_ms: 0,
        related_failure: false, related_category_failure: false, reverse_lines: false,
        clear_related_results: true, ...plan }) })
    assert.equal(response.status, 200)
  }
  async function settled(page) {
    await until(async () => (await page.locator('[data-online-browser]').getAttribute('aria-busy')) === 'false',
      'The directory selection must settle before opening its detail')
  }
  async function finished(page) {
    await page.locator('[data-online-detail]').waitFor()
    await page.locator('[data-online-detail] [data-online-favorite]:enabled').waitFor()
    await until(async () => (await metrics()).admission.background === 0
      && await page.locator('[data-online-related-loading]').count() === 0, 'Optional recommendation work must finish')
  }
  async function ids(page, group) {
    return page.locator(`[data-online-related-group="${group}"] [data-online-related-id]`)
      .evaluateAll(elements => elements.map(element => element.dataset.onlineRelatedId))
  }
  async function openSearch(page, keyword, id) {
    await search(page, keyword)
    await page.locator(`[data-online-result="${id}"]`).waitFor()
    await settled(page)
    await detailUi(page, id)
  }
  for (const width of [1440, 390]) {
    phase = { name: `related-${width}`, expectedHttp: false }
    await configure({})
    const { context, page } = await newPage(width)
    try {
      await selectSource(page)
      await page.locator('[data-online-year]').waitFor()
      await settled(page)
      await page.locator('[data-online-channel="30"]').click()
      await settled(page)
      let category = page.locator('[data-online-category="31"]:visible').first()
      if (!await category.count()) {
        await page.locator('[data-online-categories-open]:visible').first().click()
        category = page.getByRole('dialog').locator('[data-online-category="31"]').first()
      }
      await category.click()
      await settled(page)
      await page.locator('[data-online-year]').selectOption('2024')
      await settled(page)
      await page.getByRole('button', { name: '下一页', exact: true }).click()
      await page.locator('[data-online-result="academy-s2"]').waitFor()
      await settled(page)
      await page.locator('[data-online-result="academy-s2"]').scrollIntoViewIfNeeded()
      const position = await page.evaluate(() => scrollY)
      const before = await metrics()
      await detailUi(page, 'academy-s2')
      await page.locator('[data-online-related-id="academy-s4"]').waitFor()
      await finished(page)
      assert.deepEqual(await ids(page, 'series'), ['academy-s1', 'academy-s3', 'academy-s4'])
      assert.match(await page.locator('[data-online-current-season]').textContent(), /第 2 季/)
      const candidates = await page.locator('[data-online-related-id]').evaluateAll(elements => elements.map(element => element.dataset.onlineRelatedId))
      assert.ok(candidates.length <= 12 && !candidates.includes('academy-s2'))
      assert.equal(new Set(candidates).size, candidates.length)
      const after = await metrics()
      const extraCalls = apiCalls(after).slice(apiCalls(before).length)
      assert.equal(extraCalls.length, 3, 'The selected foreground detail is followed by only search and category metadata')
      assert.equal(extraCalls.filter(call => call.external_id).length, 1, 'Recommendations reuse the foreground detail without per-card reads')
      assert.equal(after.catalog_rows.online_items, before.catalog_rows.online_items + (width === 1440 ? 1 : 0),
        'Only explicitly opened targets are persisted, and existing targets retain their identity')
      assert.equal(await page.locator('video').count(), 0)
      await page.locator('[data-online-related]').scrollIntoViewIfNeeded()
      await until(() => page.locator('[data-online-related-group="series"] .online-poster > img')
        .evaluateAll(images => images.some(image => image.complete && image.naturalWidth > 0)),
      'At least one real recommendation poster must decode on the client')
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `related-${width}.png`), fullPage: true })
      const secondItem = await page.locator('[data-online-detail] [data-online-favorite]').getAttribute('data-online-favorite')
      const sessionCount = after.created_sessions.length
      await page.locator('[data-online-related-id="academy-s3"]').click()
      await page.locator('[data-online-detail] h2').filter({ hasText: '青山书院第三季' }).waitFor()
      await finished(page)
      const thirdItem = await page.locator('[data-online-detail] [data-online-favorite]').getAttribute('data-online-favorite')
      assert.notEqual(thirdItem, secondItem, 'Different seasons retain independent item identities')
      assert.equal(await page.locator('video').count(), 0, 'Recommendation clicks only open details')
      assert.equal((await metrics()).created_sessions.length, sessionCount, 'Opening another season cannot start playback')
      const thirdState = await call(page, 'GET', `/api/online/state/items/${thirdItem}`)
      assert.equal(thirdState.status, 200)
      assert.equal(thirdState.body.last_progress, null, 'Opening another season never copies the selected season progress')
      await returned(page)
      assert.equal(await page.locator('[data-online-year]').inputValue(), '2024')
      assert.match(await page.locator('.online-pagination').first().textContent(), /2 \/ 2/)
      assert.match(await page.locator('[data-online-category-current]').textContent(), /国产剧/)
      await until(() => page.evaluate(expected => Math.abs(scrollY - expected) < 12, position),
        'Returning after a recommendation preserves the original category/year/page anchor')

      // Optional same-category fallback and an empty category must leave every
      // playable episode available, including items with no season in the title.
      await openSearch(page, '重启之路第二季', 'restart-s2')
      await finished(page)
      assert.deepEqual(await ids(page, 'series'), [])
      assert.ok((await ids(page, 'related')).length > 0)
      assert.equal(await page.locator('[data-online-entry]').first().isEnabled(), true)
      await openSearch(page, '独行预告', 'lonely-trailer')
      await finished(page)
      assert.equal(await page.locator('[data-online-related-id]').count(), 0)
      assert.equal(await page.locator('[data-online-entry]').first().isEnabled(), true)

      phase.name = `related-cancel-${width}`
      await configure({ related_delay_ms: 1600 })
      await openSearch(page, '青山书院第二季', 'academy-s2')
      await until(async () => (await metrics()).admission.background === 1, 'A real delayed related HTTP search starts')
      await openSearch(page, '独行预告', 'lonely-trailer')
      await idle('Changing the selected item cancels the real recommendation worker')
      await finished(page)
      assert.equal(await page.locator('[data-online-related-id]').count(), 0, 'Late recommendations cannot repaint a different item')
      assert.match(await page.locator('[data-online-detail] h2').textContent(), /独行预告/)

      phase = { name: `related-search-partial-${width}`, expectedHttp: false }
      await configure({ related_failure: true })
      await openSearch(page, '青山书院第二季', 'academy-s2')
      await finished(page)
      await page.locator('[data-online-related-partial]').waitFor()
      assert.ok((await ids(page, 'related')).length > 0, 'A failed name search keeps available same-category posters')
      assert.deepEqual(await ids(page, 'series'), [])
      assert.equal(await page.locator('[data-online-related-error]').count(), 0)
      assert.equal(await page.locator('[data-online-entry]').first().isEnabled(), true)
      const beforeRetry = await metrics()
      await configure({ related_delay_ms: 600, clear_related_results: false })
      await page.locator('[data-online-related-retry]').click()
      assert.ok((await ids(page, 'related')).length > 0, 'Manual retry preserves already available posters while loading')
      await page.locator('[data-online-related-id="academy-s4"]').waitFor()
      await finished(page)
      assert.equal(await page.locator('[data-online-related-partial]').count(), 0)
      const retryCalls = apiCalls(await metrics()).slice(apiCalls(beforeRetry).length)
      assert.equal(retryCalls.length, 2, 'Partial responses are not cached and retry still reuses the foreground target')
      assert.equal(retryCalls.some(call => call.external_id), false)

      phase.name = `related-category-partial-${width}`
      await configure({ related_category_failure: true })
      await openSearch(page, '青山书院第二季', 'academy-s2')
      await finished(page)
      await page.locator('[data-online-related-partial]').waitFor()
      assert.deepEqual(await ids(page, 'series'), ['academy-s1', 'academy-s3', 'academy-s4'],
        'A failed category query keeps successfully discovered seasons')
      assert.deepEqual(await ids(page, 'related'), [])
      await page.locator('[data-online-related]').scrollIntoViewIfNeeded()
      await page.screenshot({ path: path.join(output, `related-partial-${width}.png`), fullPage: true })

      phase.name = `related-search-timeout-fallback-${width}`
      await configure({ related_delay_ms: 3600 })
      await openSearch(page, '青山书院第二季', 'academy-s2')
      await finished(page)
      await page.locator('[data-online-related-partial]').waitFor()
      assert.ok((await ids(page, 'related')).length > 0, 'A bounded slow name query leaves time for the independent category fallback')
      assert.equal(await page.locator('[data-online-entry]').first().isEnabled(), true)
      await idle('The timed-out source response and its cancelled reader are fully released')

      phase = { name: `related-both-failed-${width}`, expectedHttp: true }
      await configure({ related_failure: true, related_category_failure: true })
      await openSearch(page, '青山书院第二季', 'academy-s2')
      await finished(page)
      await page.locator('[data-online-related-error]').waitFor()
      assert.equal(await page.locator('[data-online-related-id]').count(), 0)
      assert.equal(await page.locator('[data-online-entry]').first().isEnabled(), true, 'Two unavailable branches cannot disable the selected detail')
      const failedRequests = (await metrics()).api_calls.filter(call => call.path === '/api/online/catalog/related').length
      await delay(450)
      assert.equal((await metrics()).api_calls.filter(call => call.path === '/api/online/catalog/related').length, failedRequests,
        'Recommendation errors never start an automatic retry loop')
      await configure({ clear_related_results: false })
      await page.locator('[data-online-related-retry]').click()
      await page.locator('[data-online-related-id="academy-s4"]').waitFor()
      await finished(page)
      phase.expectedHttp = false

      phase.name = `related-preempt-play-${width}`
      await returned(page)
      await configure({ related_delay_ms: 5000 })
      await detailUi(page, 'academy-s2')
      assert.equal(await page.locator('[data-online-line]').filter({ hasText: 'MP4直链' }).getAttribute('aria-pressed'), 'true',
        'The fixed fixture already selects MP4, so no line change cancels optional work before the play check')
      await until(async () => (await metrics()).admission.background === 1, 'Recommendation admission is occupied before play')
      const prior = await metrics()
      const clickToFrameMs = await playEntry(page, '第1集')
      await advancing(page, .25)
      const playing = await snapshot(page)
      assert.equal(playing.identity.item_id, secondItem)
      const during = await metrics()
      assert.equal(during.admission.background, 0, 'Foreground playback cancels optional source work')
      assert.equal(during.api_calls.slice(prior.api_calls.length).some(call => call.status === 429), false,
        'An optional recommendation cannot make real playback return 429')
      const released = await closePlayer(page)
      assert.equal(released.metrics.admission.active, 0)
      assert.equal(released.metrics.admission.background, 0)
      assert.equal(released.metrics.admission.preempt_waiters, 0)
      const playedState = await call(page, 'GET', `/api/online/state/items/${secondItem}`)
      const untouchedState = await call(page, 'GET', `/api/online/state/items/${thirdItem}`)
      assert.equal(playedState.status, 200)
      assert.equal(untouchedState.body.last_progress, null, 'Actual second-season playback never writes third-season progress')

      // Exercise an HTTP caller that does not cancel through the UI client, so
      // the server itself must preempt background work for the same owner.
      await configure({ related_delay_ms: 5000 })
      await page.evaluate(apiPath => {
        window.__p2RelatedRequest = fetch(apiPath).then(async response => ({ status: response.status, body: await response.json() }))
      }, relatedPath('academy-s2'))
      await until(async () => (await metrics()).admission.background === 1, 'Independent related HTTP request holds the same owner admission')
      await playEntry(page, '第1集')
      await advancing(page, .25)
      const deferred = await page.evaluate(() => window.__p2RelatedRequest)
      assert.equal(deferred.status, 200)
      assert.equal(deferred.body.status, 'deferred', 'The server yields the background request without cancelling foreground playback')
      await closePlayer(page)
      await configure({})
      passed({ case: `related-${width}`, geometry, orderedSeries: ['academy-s1', 'academy-s3', 'academy-s4'],
        currentSeasonExcluded: true, candidatesBound: 12, recommendationMetadataCalls: extraCalls.length - 1,
        perCardDetailCalls: 0, opensDetailsWithoutPlayback: true, separateSeasonProgress: true,
        categoryYearPageAnchorRestored: true, sameCategoryFallback: true, emptyRelatedKeepsEpisodes: true,
        lateResultsDiscarded: true, partialResultsKeepPosters: true, partialManualRetryRecovers: true,
        slowSearchKeepsCategoryFallback: true, bothFailuresStayVisibleWithoutAutoRetry: true,
        foregroundTargetReused: true, failureDoesNotBlockPlayback: true, foregroundPlayPreempts: true,
        serverDeferredStatus: deferred.body.status, clickToFrameMs, actualDecodedFrames: true })

      if (width === 390 && selectedCase === 'related') {
        phase = { name: 'related-permission-cleanup', expectedHttp: false }
        const password = 'isolated-p2-protection'
        assert.equal((await call(page, 'POST', '/api/viewing/password', { new_password: password })).status, 200)
        await page.reload()
        await page.waitForFunction(() => window.__jzOnlineP2Fixture?.snapshot().ready)
        await selectSource(page)
        const back = page.getByRole('button', { name: '返回搜索结果', exact: true })
        if (await back.count()) await back.first().click()
        await settled(page)
        await idle('Permission setup finishes any restored detail or directory work before clearing the related cache')
        const protectedViewing = await call(page, 'GET', '/api/viewing/session')
        assert.equal(protectedViewing.body.enabled, true)
        assert.equal(protectedViewing.body.mode, 'normal')
        assert.equal(protectedViewing.body.management_unlocked, false,
          'Recommendations must remain ordinary viewing content without a management grant')
        await configure({ related_delay_ms: 1600 })
        await openSearch(page, '青山书院第二季', 'academy-s2')
        await until(async () => (await metrics()).admission.background === 1, 'Recommendation work begins before viewing permission changes')
        await page.getByRole('button', { name: '普通模式', exact: true }).click()
        await page.getByRole('dialog').getByRole('button', { name: /^儿童模式/ }).click()
        await page.getByText('儿童模式暂不支持在线内容', { exact: true }).waitFor()
        await idle('A viewing-mode change clears and cancels recommendations')
        assert.equal(await page.locator('[data-online-related]').count(), 0)
        assert.equal(await page.locator('[data-online-detail]').count(), 0)
        phase.expectedHttp = true
        assert.equal((await call(page, 'GET', relatedPath('academy-s2'))).status, 403)
        assert.equal((await call(page, 'POST', '/api/viewing/mode', { mode: 'normal', password })).status, 200)
        passed({ case: phase.name, pendingWorkerReleased: true, recommendationContentCleared: true, childrenDenied: true })
      }
    } finally {
      await closeContext(context)
      await idle()
      const response = await fetch(`${base}/api/p2/scenario`, { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Api-Token': 'online-p2-fixture-only' },
        body: JSON.stringify({ related: false, related_delay_ms: 0, related_failure: false,
          related_category_failure: false, clear_related_cache: true }) })
      assert.equal(response.status, 200)
    }
  }
  await backendChildren('after-related')
}
async function loading() {
  const imageReads = value => value.origin_calls.filter(call => call.kind === 'image').length
  const apiReads = value => value.origin_calls.filter(call => call.kind === 'api').length
  for (const width of [1440, 390]) {
    phase = { name: `loading-${width}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    try {
      await navigate(page, '/settings')
      await idle()
      assert.equal((await call(page, 'POST', '/api/p2/scenario', {
        gallery: true, image_delay_ms: 1200, clear_artwork_cache: true,
      })).status, 200)
      await selectSource(page)
      await page.locator('[data-online-result]').first().waitFor()
      // Drain the previous visible work. A full isolated page reload creates
      // genuinely cold poster components; manual refresh now keeps good art.
      await until(async () => (await page.locator('.online-results [data-online-artwork-state="loading"]').count()) === 0,
        'Previously visible posters finish before the cold measurement', 25000)
      await idle()
      assert.equal((await call(page, 'POST', '/api/p2/scenario', { clear_artwork_cache: true })).status, 200)
      const before = await metrics(), started = performance.now()
      const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/online/catalog/browse'
        && response.status() === 200)
      await page.reload()
      await page.waitForFunction(() => window.__jzOnlineP2Fixture?.snapshot().ready)
      await refreshed
      await until(() => page.locator('[data-online-refresh]').isEnabled(), 'Fresh directory replaces the previous render')
      await page.locator('[data-online-result="gallery-1"]').waitFor()
      let firstMs = null
      async function loadGallery(first, last) {
        for (let number = first; number <= last; number++) {
          const poster = page.locator(`[data-online-result="gallery-${number}"]`)
          await poster.scrollIntoViewIfNeeded()
          await until(() => poster.locator('.online-poster > img').evaluateAll(images => images.some(image => image.complete && image.naturalWidth > 0)),
            `Poster ${number} must decode without a queue timeout`, 18000)
          if (firstMs === null) firstMs = Math.round(performance.now() - started)
        }
      }
      await loadGallery(1, 20)
      const cold = (await idle()).metrics, coldMs = Math.round(performance.now() - started)
      assert.equal(imageReads(cold) - imageReads(before), 20, 'Twenty unique leases cause twenty bounded source reads')
      assert.ok(cold.images.peak <= 2, 'Only two actual source image reads may run at once')
      assert.ok(cold.artwork_cache.bytes <= 16 * 1024 * 1024)
      assert.equal(await page.locator('.online-results [data-online-artwork-state="error"]').count(), 0)
      await page.locator('[data-online-category="1"]').click()
      await page.getByRole('button', { name: '下一页', exact: true }).click()
      await page.locator('[data-online-result="gallery-21"]').waitFor()
      await loadGallery(21, 40)
      await idle()
      const position = await page.evaluate(() => scrollY)
      await detailUi(page, 'gallery-40')
      await page.getByRole('button', { name: '返回搜索结果', exact: true }).click()
      await until(() => page.evaluate(expected => Math.abs(scrollY - expected) < 12, position), 'Detail back restores the poster anchor')
      const warmBefore = await metrics()
      // Navigate programmatically so clicking a fixed header does not first
      // change the scroll position that the page is expected to remember.
      await page.evaluate(() => window.__jzOnlineP2Fixture.navigate('/settings'))
      await page.waitForFunction(() => window.__jzOnlineP2Fixture.snapshot().path === '/settings')
      const warmStarted = performance.now()
      await page.evaluate(() => window.__jzOnlineP2Fixture.navigate('/online'))
      await page.locator('[data-online-result="gallery-40"]').waitFor({ state: 'attached' })
      await until(() => page.evaluate(expected => Math.abs(scrollY - expected) < 12, position), 'Route reentry restores the previous anchor')
      assert.equal(await page.locator('[data-online-category="1"]').getAttribute('aria-pressed'), 'true')
      assert.ok((await page.locator('.online-pagination').first().textContent()).includes('2 / 2'))
      const last = page.locator('[data-online-result="gallery-40"]')
      await until(() => last.locator('.online-poster > img').evaluateAll(images => images.some(image => image.complete && image.naturalWidth > 0)),
        'Warm route reentry shows decoded artwork')
      const warmMs = Math.round(performance.now() - warmStarted)
      const warm = (await idle()).metrics
      assert.equal(imageReads(warm), imageReads(warmBefore), 'Warm reentry does not download any image from the source')
      assert.equal(apiReads(warm), apiReads(warmBefore), 'Verified warm reentry does not repeat the provider catalogue request')
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `loading-${width}.png`), fullPage: true })
      passed({ case: phase.name, geometry, cold: { firstMs, completeMs: coldMs, upstreamImages: 20 },
        warm: { visibleMs: warmMs, upstreamImages: 0, upstreamCatalogue: 0 },
        maxImageReads: cold.images.peak, page: 2, category: '1', positionRestored: true,
        cache: warm.artwork_cache })
    } finally {
      await closeContext(context)
      await idle()
      const response = await fetch(`${base}/api/p2/scenario`, { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Api-Token': 'online-p2-fixture-only' },
        body: JSON.stringify({ gallery: false, image_delay_ms: 0 }) })
      assert.equal(response.status, 200)
    }
  }
  await backendChildren('after-loading')
}

async function imageCache() {
  const imageReads = value => value.origin_calls.filter(call => call.kind === 'image').length
  const apiReads = value => value.origin_calls.filter(call => call.kind === 'api').length
  for (const width of [1440, 390]) {
    phase = { name: `image-cache-${width}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    let firstMs = null, started = 0
    async function decodedGallery() {
      for (let number = 1; number <= 20; number++) {
        const poster = page.locator(`[data-online-result="gallery-${number}"]`)
        await poster.scrollIntoViewIfNeeded()
        await until(() => poster.locator('.online-poster > img').evaluateAll(images => images.some(image => image.complete && image.naturalWidth > 0)),
          `Cached poster ${number} must decode through its authorized image lease`, 18000)
        if (firstMs === null) firstMs = Math.round(performance.now() - started)
      }
    }
    async function reloadGallery() {
      const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/online/catalog/browse'
        && response.status() === 200)
      await page.reload()
      await page.waitForFunction(() => window.__jzOnlineP2Fixture?.snapshot().ready)
      await refreshed
      await page.locator('[data-online-result="gallery-1"]').waitFor()
      await decodedGallery()
      return (await idle()).metrics
    }
    try {
      await navigate(page, '/settings'); await idle()
      await scenario(page, { gallery: true, taxonomy: false, related: false, detail_mode: false,
        image_delay_ms: 250, clear_artwork_cache: true })
      await selectSource(page)
      await page.locator('[data-online-result="gallery-1"]').waitFor()
      await until(async () => await page.locator('.online-results [data-online-artwork-state="loading"]').count() === 0,
        'Visible work settles before the measured cold reload', 25000)
      await idle()
      await scenario(page, { clear_artwork_cache: true })
      const before = await metrics()
      assert.equal(before.artwork_cache.entries, 0)
      assert.equal(before.artwork_disk_cache.entries, 0, 'Cold setup clears both cache levels')
      started = performance.now(); firstMs = null
      const cold = await reloadGallery()
      const coldTime = { firstMs, completeMs: Math.round(performance.now() - started) }
      assert.equal(imageReads(cold) - imageReads(before), 20, 'Cold loading reads each unique original exactly once')
      assert.ok(cold.images.peak <= 2, 'Image optimization does not raise actual upstream concurrency')
      assert.ok(cold.artwork_disk_cache.entries >= 20)
      assert.ok(cold.artwork_disk_cache.bytes <= 128 * 1024 * 1024)
      assert.ok(cold.artwork_disk_cache.entries <= 2048)

      // A complete browser reload discards the session catalogue and all Blob
      // objects. Clear L1 separately so zero source reads prove the disk level.
      await scenario(page, { clear_artwork_memory: true })
      const warmBefore = await metrics()
      assert.equal(warmBefore.artwork_cache.entries, 0)
      assert.equal(warmBefore.artwork_disk_cache.entries, cold.artwork_disk_cache.entries)
      started = performance.now(); firstMs = null
      const warm = await reloadGallery()
      const warmTime = { firstMs, completeMs: Math.round(performance.now() - started) }
      assert.ok(apiReads(warm) > apiReads(warmBefore), 'A full reload still obtains a fresh authorized directory')
      assert.equal(imageReads(warm) - imageReads(warmBefore), 0, 'Disk hits make no source image request')
      assert.equal(warm.network_dials.image - warmBefore.network_dials.image, 0, 'Disk hits open no image TCP connection')
      assert.equal(await page.locator('.online-results [data-online-artwork-state="error"]').count(), 0)
      assert.equal(warm.image_pool.active, 0)
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `image-cache-${width}.png`), fullPage: true })
      passed({ case: phase.name, geometry, originals: 20, maxImageReads: cold.images.peak,
        cold: { ...coldTime, upstreamImages: 20, imageTcpDials: cold.network_dials.image - before.network_dials.image },
        diskWarm: { ...warmTime, upstreamImages: 0, imageTcpDials: 0, freshDirectory: true },
        fullBrowserReload: true, memoryClearedBeforeDiskRead: true,
        diskCache: warm.artwork_disk_cache, imagePool: warm.image_pool,
        scope: 'Measured synthetic latency only; full browser reload and L1 reset, not an application process restart or public-source speed promise' })
    } finally {
      await closeContext(context); await idle()
      const response = await fetch(`${base}/api/p2/scenario`, { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Api-Token': 'online-p2-fixture-only' },
        body: JSON.stringify({ gallery: false, image_delay_ms: 0, clear_artwork_cache: true }) })
      assert.equal(response.status, 200)
    }
  }
  await backendChildren('after-image-cache')
}

async function detailExperience() {
  async function configure(active) {
    const response = await fetch(`${base}/api/p2/scenario`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Api-Token': 'online-p2-fixture-only' },
      body: JSON.stringify({ detail_mode: active, taxonomy: false, gallery: false, related: false,
        reverse_lines: false, related_delay_ms: 0, related_failure: false, clear_related_cache: true }) })
    assert.equal(response.status, 200)
  }
  async function settled(page) {
    await until(async () => (await page.locator('[data-online-browser]').getAttribute('aria-busy')) === 'false',
      'The detail fixture directory must settle before the next action')
  }
  async function ready(page) {
    await page.locator('[data-online-detail]').waitFor()
    await page.locator('[data-online-detail] [data-online-favorite]:enabled').waitFor()
  }
  async function selectItem(page, keyword, id) {
    await search(page, keyword)
    await page.locator(`[data-online-result="${id}"]`).waitFor()
    await settled(page)
    await detailUi(page, id)
    await ready(page)
  }
  const visibleEntries = page => page.locator('[data-online-entry]')
  const labels = page => visibleEntries(page).evaluateAll(elements => elements.map(element => element.dataset.onlineEntry))
  const primary = page => page.locator('[data-online-primary-play]')
  for (const width of [1440, 390, 320]) {
    phase = { name: `detail-${width}`, expectedHttp: false }
    await configure(true)
    const { context, page } = await newPage(width)
    try {
      await selectSource(page)
      await page.locator('[data-online-year]').waitFor()
      await settled(page)
      await page.locator('[data-online-channel="30"]').click()
      await settled(page)
      let category = page.locator('[data-online-category="31"]:visible').first()
      if (!await category.count()) {
        await page.locator('[data-online-categories-open]:visible').first().click()
        category = page.getByRole('dialog').locator('[data-online-category="31"]').first()
      }
      await category.click(); await settled(page)
      await page.locator('[data-online-year]').selectOption('2025'); await settled(page)
      await page.getByRole('button', { name: '下一页', exact: true }).click(); await settled(page)
      const result = page.locator('[data-online-result="long-show"]')
      await result.waitFor(); await result.scrollIntoViewIfNeeded()
      const browsePosition = await page.evaluate(() => scrollY)
      await detailUi(page, 'long-show'); await ready(page)
      assert.equal(await page.locator('[data-online-browse-tools]').isVisible(), false)
      assert.equal(await page.locator('[data-online-source]').isVisible(), false)
      assert.equal(await page.locator('[data-online-year]').isVisible(), false)
      assert.equal(await page.locator('video').count(), 0, 'Entering a detail never starts playback')
      assert.equal(await primary(page).isEnabled(), true)
      assert.match(await primary(page).textContent(), /第1集/)
      await until(async () => {
        const bounds = await primary(page).boundingBox()
        return bounds && bounds.y >= 0 && bounds.y + bounds.height <= (width > 700 ? 1040 : 844)
      }, 'The main play action is visible on the first detail screen')
      await until(() => page.locator('[data-online-detail-heading] .online-artwork > img')
        .evaluateAll(images => images.some(image => image.complete && image.naturalWidth > 0)),
      'The detail poster is decoded on the browser before recording the first screen')
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `detail-${width}-first-screen.png`) })
      const itemId = await page.locator('[data-online-detail] [data-online-favorite]').getAttribute('data-online-favorite')
      const detailResponse = await call(page, 'GET', `/api/online/catalog/detail?source_id=${primarySource.id}&external_id=long-show`)
      assert.equal(detailResponse.status, 200)
      const detail = detailResponse.body
      const mp4 = detail.lines.find(line => line.label === 'MP4直链')
      const hls = detail.lines.find(line => line.label === 'HLS直链')
      assert.ok(mp4 && hls)
      assert.equal(await visibleEntries(page).count(), 50, 'A 783-entry line mounts only one 50-entry group')
      const group = page.locator('[data-online-entry-group]'), order = page.locator('[data-online-entry-order]')
      assert.equal(await group.locator('option').count(), 16)
      await group.selectOption('1')
      assert.equal((await labels(page))[0], '第51集')
      assert.equal(await page.locator('[data-online-entry="第52集"]').isEnabled(), false)
      await group.selectOption('2')
      assert.equal(await visibleEntries(page).count(), 50)
      assert.equal((await labels(page))[0], '番外·特别篇')
      assert.equal((await labels(page)).at(-1), '第150集')
      await order.selectOption('desc')
      assert.equal(await group.inputValue(), '15')
      assert.equal((await labels(page))[0], '终章（下）')
      await order.selectOption('asc')
      assert.equal(await group.inputValue(), '0')
      assert.equal((await labels(page))[0], '第1集')

      // The main action and actual episode choices precede the long optional
      // information. Native focus and keyboard activation remain usable.
      assert.equal(await page.evaluate(() => {
        const action = document.querySelector('[data-online-primary-play]')
        const overview = document.querySelector('[data-online-detail] .media-overview')
        return !!(action.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING)
      }), true)
      const overviewToggle = page.locator('[data-online-detail] .overview-toggle')
      await overviewToggle.scrollIntoViewIfNeeded(); await overviewToggle.focus(); await page.keyboard.press('Enter')
      assert.equal(await overviewToggle.getAttribute('aria-expanded'), 'true')
      await page.keyboard.press('Enter')
      assert.equal(await overviewToggle.getAttribute('aria-expanded'), 'false')
      const metadata = page.locator('[data-online-detail-metadata]')
      assert.equal(await metadata.getAttribute('open'), null)
      await metadata.locator('summary').focus(); await page.keyboard.press('Enter')
      assert.notEqual(await metadata.getAttribute('open'), null)
      assert.equal(await page.locator('[data-online-metadata]').isVisible(), true)
      await metadata.locator('summary').focus(); await page.keyboard.press('Enter')
      await page.locator('[data-online-detail-heading]').scrollIntoViewIfNeeded()
      await page.screenshot({ path: path.join(output, `detail-${width}.png`), fullPage: true })

      // Read and play beyond the former 500-entry limit using stable identity.
      await group.selectOption('15')
      assert.equal(await visibleEntries(page).count(), 33)
      assert.equal((await labels(page))[0], '第751集')
      assert.equal((await labels(page)).at(-1), '终章（下）')
      await page.locator('[data-online-entry="终章（下）"]').click()
      await loaded(page)
      assert.deepEqual((await snapshot(page)).identity, mp4.entries[782].identity)
      await closePlayer(page)

      // Persist a late-group episode through the real player. Seek provides a
      // distinguishable resume point without turning this into a long-play test.
      await group.selectOption('2')
      await page.locator('[data-online-entry="第105集"]').click()
      await loaded(page)
      assert.deepEqual((await snapshot(page)).identity, mp4.entries[104].identity)
      await page.locator('video').evaluate(video => { video.currentTime = 8 })
      await advancing(page, 8)
      await pause(page); await closePlayer(page)
      const saved = await until(async () => {
        const response = await call(page, 'GET', `/api/online/state/items/${itemId}`)
        return response.body.last_progress?.identity.episode_id === mp4.entries[104].identity.episode_id
          && response.body.last_progress?.position > 7 ? response.body.last_progress : false
      }, 'Actual late-group playback must persist its own identity and resume time')
      assert.equal(saved.identity.line_id, mp4.id)
      await page.locator('[data-online-detail] [data-online-favorite]').click()
      await until(async () => (await page.locator('[data-online-detail] [data-online-favorite]').textContent()).includes('取消收藏'),
        'Favorite remains available beside the main play action')
      await page.getByRole('button', { name: '返回搜索结果', exact: true }).focus()
      await page.keyboard.press('Enter')
      await page.locator('[data-online-result="long-show"]').waitFor()
      assert.equal(await page.locator('[data-online-year]').inputValue(), '2025')
      assert.match(await page.locator('.online-pagination').first().textContent(), /2 \/ 2/)
      await until(() => page.evaluate(expected => Math.abs(scrollY - expected) < 12, browsePosition),
        'Detail return preserves category/year/page and the poster anchor')
      await until(() => page.evaluate(() => document.activeElement?.getAttribute('data-online-result') === 'long-show'),
        'Keyboard return restores focus to the original poster without losing its anchor')
      await detailUi(page, 'long-show'); await ready(page)
      assert.match(await primary(page).textContent(), /继续.*第105集/)
      assert.equal(await group.inputValue(), '2')
      assert.equal(await page.locator('[data-online-detail] [data-online-favorite]').textContent().then(text => text.includes('取消收藏')), true)
      const sessionCount = (await metrics()).created_sessions.length
      await group.selectOption('0')
      await page.locator('[data-online-locate-last]').click()
      await until(() => page.evaluate(() => document.activeElement?.dataset.onlineEntry === '第105集'),
        'Locate last watched focuses its exact episode without playback')
      assert.equal(await group.inputValue(), '2')
      assert.equal((await metrics()).created_sessions.length, sessionCount)
      await chooseLine(page, 'HLS直链')
      assert.equal(await group.inputValue(), '0', 'Changing lines resets the displayed group')
      assert.match(await primary(page).textContent(), /第1集/)
      assert.equal(await primary(page).getAttribute('data-online-detail-resume'), null)
      const otherLineState = await call(page, 'GET', `/api/online/state/items/${itemId}?` + new URLSearchParams({
        episode_id: hls.entries[104].identity.episode_id, line_id: hls.id }))
      assert.equal(otherLineState.body.selected_progress, null, 'An alternate line cannot inherit the saved progress')
      await page.locator('[data-online-detail-locate-last]').click()
      await until(() => page.evaluate(() => document.activeElement?.dataset.onlineEntry === '第105集'),
        'Explicit last-watch navigation restores its original line and exact group')
      assert.equal(await page.locator(`[data-online-line="${mp4.id}"]`).getAttribute('aria-pressed'), 'true')
      assert.equal((await metrics()).created_sessions.length, sessionCount)
      await primary(page).click(); await loaded(page)
      assert.deepEqual((await snapshot(page)).identity, saved.identity)
      assert.ok((await readPlayer(page)).time >= saved.position - .5, 'Primary resume restores the saved playback time')
      await closePlayer(page)

      let actualForwardAutoplay = false
      if (width === 1440) {
        await page.locator('[data-online-autoplay]').check()
        await order.selectOption('desc')
        await group.selectOption('2')
        await page.locator('[data-online-entry="第105集"]').click(); await loaded(page)
        await page.evaluate(() => {
          window.__p2RealEndedCount = 0
          document.addEventListener('ended', () => { window.__p2RealEndedCount++ }, { capture: true })
          const video = document.querySelector('video'); video.currentTime = video.duration - .6
        })
        await until(async () => (await snapshot(page))?.identity?.episode_id === mp4.entries[105].identity.episode_id,
          'Real ended advances to the original next entry, even while the selector is descending')
        await loaded(page)
        assert.ok(await page.evaluate(() => window.__p2RealEndedCount >= 1))
        assert.equal((await snapshot(page)).identity.line_id, mp4.id)
        await closePlayer(page); await page.locator('[data-online-autoplay]').uncheck()
        actualForwardAutoplay = true
      }
      await selectItem(page, '短篇合成剧', 'short-show')
      assert.equal(await page.locator('[data-online-entry-group]').count(), 0)
      assert.deepEqual(await labels(page), ['序章', '正篇', '后记'])
      assert.equal(await page.locator('[data-online-entry-last="true"]').count(), 0)
      assert.equal(await page.locator('[data-online-detail-locate-last]').count(), 0)
      await selectItem(page, '暂不可播放的合成剧', 'unavailable-show')
      assert.equal(await primary(page).isEnabled(), false)
      assert.equal(await visibleEntries(page).count(), 2)
      assert.equal(await visibleEntries(page).evaluateAll(elements => elements.every(element => element.disabled)), true)
      assert.equal(await page.locator('video').count(), 0)
      passed({ case: `detail-${width}`, geometry, firstScreenPrimary: true, hiddenBrowseTools: true,
        sourceEntries: 783, lastGroupPlayback: true, maxMountedEntries: 50, preservesNonstandardLabels: true,
        keyboardDescriptionAndMetadata: true, realLateGroupPlayback: true, resumePosition: saved.position,
        lastWatchedFocusWithoutAutoplay: true, crossLineProgressIsolated: true,
        categoryYearPageAnchorRestored: true, resetForDifferentItem: true,
        unsupportedPrimaryDisabled: true, actualForwardAutoplay })
    } finally {
      await closeContext(context); await idle(); await configure(false)
    }
  }
  await backendChildren('after-detail')
}

async function detailRecovery() {
  const calls = (value, id = 'movie-1') => value.origin_calls.filter(call => call.kind === 'api' && call.external_id === id)
  const ready = page => page.locator('[data-online-detail] [data-online-favorite]:enabled').waitFor()
  async function configure(page, plan = {}) {
    await scenario(page, { related: false, taxonomy: false, gallery: false, detail_mode: false,
      media_alias: false, reverse_lines: false,
      detail_failures_remaining: 0, detail_failure_status: 503, detail_failure_delay_ms: 0,
      detail_success_delay_ms: 0, ...plan })
  }
  async function begin(page) { await page.locator('[data-online-result="movie-1"]').click() }
  async function settled(page) {
    await until(async () => (await page.locator('[data-online-browser]').getAttribute('aria-busy')) === 'false',
      'The recovered directory finishes before another detail intent')
  }
  let alternate = null
  for (const width of [1440, 390]) {
    phase = { name: `detail-recovery-${width}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    let originalRule = null
    try {
      // Use an actual short, explicit API profile in this isolated database.
      // A delayed first header therefore exercises the real ReadTimeout path.
      await navigate(page, '/settings'); await idle()
      originalRule = (await call(page, 'GET', `/api/online/sources/${primarySource.id}/network`)).body.policy.api
      const profile = await call(page, 'POST', '/api/online/network/profiles', {
        name: `隔离恢复读取 ${width}`, mode: 'direct', connect_timeout_seconds: 1,
        read_timeout_seconds: 1, max_connections: 1 })
      assert.equal(profile.status, 201)
      assert.equal((await call(page, 'PATCH', `/api/online/sources/${primarySource.id}/network`, {
        api: { mode: 'direct', profile_id: profile.body.id } })).status, 200)
      if (!alternate) {
        const bootstrap = (await call(page, 'GET', '/api/p2/bootstrap')).body
        const created = await call(page, 'POST', '/api/online/sources', { provider: 'maccms', name: '隔离恢复备用来源',
          api_base_url: bootstrap.api_base_url, allowed_hosts: bootstrap.allowed_hosts, enabled: true })
        assert.equal(created.status, 201); alternate = created.body
      }
      await selectSource(page); await settled(page); await search(page, '合成电影'); await settled(page)
      await configure(page, { detail_failures_remaining: 1, detail_failure_status: 503 })
      let before = await metrics()
      await begin(page); await ready(page)
      let after = await metrics()
      assert.equal(calls(after).length - calls(before).length, 2)
      assert.deepEqual(calls(after).slice(calls(before).length).map(call => call.planned_status), [503, 200])
      assert.equal(await page.getByText('详情加载失败', { exact: true }).count(), 0)
      assert.equal(await page.locator('video').count(), 0, 'Automatic metadata recovery never starts playback')
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `detail-recovery-${width}.png`) })
      await chooseLine(page, 'MP4直链'); await playEntry(page); await advancing(page); await closePlayer(page)
      await returned(page); await settled(page); await idle()

      phase = { name: `detail-recovery-exhausted-${width}`, expectedHttp: true }
      await configure(page, { detail_failures_remaining: 2 })
      before = await metrics(); await begin(page)
      const reload = page.getByRole('button', { name: '重新加载', exact: true })
      await reload.waitFor()
      assert.equal(calls(await metrics()).length - calls(before).length, 2, 'A failed detail has at most two upstream attempts')
      await delay(250)
      assert.equal(calls(await metrics()).length - calls(before).length, 2, 'There is no automatic retry loop')
      await reload.click(); await ready(page)
      assert.equal(calls(await metrics()).length - calls(before).length, 3, 'One manual reload is a new foreground operation')
      assert.equal(await page.locator('video').count(), 0)
      phase.expectedHttp = false
      await returned(page); await settled(page); await idle()

      phase.name = `detail-recovery-read-timeout-${width}`
      await configure(page, { detail_failures_remaining: 1, detail_failure_status: 200, detail_failure_delay_ms: 1300 })
      before = await metrics(); await begin(page); await ready(page); await idle()
      assert.equal(calls(await metrics()).length - calls(before).length, 2)
      assert.equal(await page.getByText('详情加载失败', { exact: true }).count(), 0)
      await returned(page); await settled(page)

      phase.name = `detail-recovery-cancel-first-${width}`
      await configure(page, { detail_failures_remaining: 1, detail_failure_delay_ms: 1500 })
      before = await metrics(); await begin(page)
      await until(async () => calls(await metrics()).length === calls(before).length + 1,
        'The first detail attempt reaches the real origin before returning')
      await returned(page); await settled(page); await idle()
      assert.equal(calls(await metrics()).length - calls(before).length, 1, 'Returning during the first attempt prevents a second attempt')
      assert.equal(await page.locator('[data-online-detail]').count(), 0)

      phase.name = `detail-recovery-cancel-second-${width}`
      await configure(page, { detail_failures_remaining: 1, detail_success_delay_ms: 1500 })
      before = await metrics(); await begin(page)
      await until(async () => calls(await metrics()).length === calls(before).length + 2,
        'The second guarded attempt starts before changing the source')
      await returned(page); await settled(page); await idle()
      await configure(page)
      await selectSource(page, alternate); await settled(page); await search(page, '合成剧集'); await settled(page)
      await detailUi(page, 'show-1'); await ready(page)
      assert.equal(calls(await metrics()).length - calls(before).length, 2, 'An abandoned second attempt never starts a third')
      assert.match(await page.locator('[data-online-detail] .online-eyebrow').textContent(), /隔离恢复备用来源/)
      assert.match(await page.locator('[data-online-detail] h2').textContent(), /合成剧集/)
      assert.equal(await page.locator('video').count(), 0)
      await selectSource(page); await settled(page); await search(page, '合成电影'); await settled(page)

      passed({ case: `detail-recovery-${width}`, geometry, transient503Recovered: true,
        realReadTimeoutRecovered: true, maximumAttempts: 2, manualRetryRecovers: true,
        returningCancelsFirstAttempt: true, changingSourceDiscardsSecondAttempt: true,
        noAutoplay: true, actualMp4DecodeAfterRecovery: true })

      if (width === 390 && selectedCase === 'detail-recovery') {
        phase = { name: 'detail-recovery-permission-cancellation', expectedHttp: true }
        const password = 'isolated-detail-recovery-protection'
        assert.equal((await call(page, 'POST', '/api/viewing/password', { new_password: password })).status, 200)
        await page.reload(); await page.waitForFunction(() => window.__jzOnlineP2Fixture?.snapshot().ready)
        await selectSource(page); await settled(page); await search(page, '合成电影'); await settled(page); await idle()
        await configure(page, { detail_failures_remaining: 1, detail_success_delay_ms: 1500 })
        before = await metrics(); await begin(page)
        await until(async () => calls(await metrics()).length === calls(before).length + 2,
          'The second detail attempt is active before a viewing permission change')
        await page.getByRole('button', { name: '普通模式', exact: true }).click()
        await page.getByRole('dialog').getByRole('button', { name: /^儿童模式/ }).click()
        await page.getByText('儿童模式暂不支持在线内容', { exact: true }).waitFor()
        await idle()
        assert.equal(calls(await metrics()).length - calls(before).length, 2)
        assert.equal(await page.locator('[data-online-detail]').count(), 0)
        assert.equal(await page.locator('video').count(), 0)
        assert.equal((await call(page, 'POST', '/api/viewing/mode', { mode: 'normal', password })).status, 200)
        passed({ case: phase.name, secondAttemptCancelled: true, noLateDetailOrAutoplay: true })
      }
    } finally {
      if (originalRule) assert.equal((await call(page, 'PATCH', `/api/online/sources/${primarySource.id}/network`, { api: originalRule })).status, 200)
      await configure(page); await closeContext(context); await idle()
    }
  }
  await backendChildren('after-detail-recovery')
}

async function metadataRecovery() {
  const mainCalls = value => value.origin_calls.filter(call => call.kind === 'api' && call.metadata_status !== undefined)
  const discoveries = value => value.origin_calls.filter(call => call.kind === 'api' && call.category_discovery_failure)
  async function settled(page) {
    await until(async () => (await page.locator('[data-online-browser]').getAttribute('aria-busy')) === 'false',
      'The foreground metadata request settles before another intent')
  }
  async function configure(page, extra = {}) {
    await scenario(page, { taxonomy: true, taxonomy_unavailable: false, related: false, gallery: false,
      detail_mode: false, media_alias: false, metadata_failures_remaining: 0,
      metadata_failure_status: 503, metadata_failure_delay_ms: 0, metadata_success_delay_ms: 0, ...extra })
  }
  for (const width of [1440, 390]) {
    phase = { name: `metadata-recovery-${width}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    let originalRule = null
    try {
      await navigate(page, '/settings'); await idle(); await page.waitForLoadState('networkidle')
      originalRule = (await call(page, 'GET', `/api/online/sources/${primarySource.id}/network`)).body.policy.api
      const profile = await call(page, 'POST', '/api/online/network/profiles', {
        name: `隔离目录恢复 ${width}`, mode: 'direct', connect_timeout_seconds: 1,
        read_timeout_seconds: 1, max_connections: 1 })
      assert.equal(profile.status, 201)
      assert.equal((await call(page, 'PATCH', `/api/online/sources/${primarySource.id}/network`, {
        api: { mode: 'direct', profile_id: profile.body.id } })).status, 200)
      await configure(page, { clear_taxonomy_cache: true })
      await selectSource(page); await settled(page); await idle()
      const warmBefore = await metrics()
      await page.locator('[data-online-refresh]').click(); await settled(page)
      const warmAfter = await metrics()
      assert.equal(mainCalls(warmAfter).length - mainCalls(warmBefore).length, 1,
        'A warm connection still performs the real main API read')
      assert.equal(warmAfter.network_dials.api, warmBefore.network_dials.api,
        'The next foreground query reuses its actual API connection')
      assert.ok(warmAfter.api_pool.idle >= 1)
      await configure(page, { metadata_failures_remaining: 1 })
      let before = await metrics()
      await page.locator('[data-online-refresh]').click(); await settled(page)
      let after = await metrics()
      assert.deepEqual(mainCalls(after).slice(mainCalls(before).length).map(call => call.metadata_status), [503, 200])
      assert.equal(await page.locator('.online-refresh-error').count(), 0)
      assert.equal(await page.locator('video').count(), 0, 'Recovered metadata never starts playback')

      await configure(page, { metadata_failures_remaining: 1 })
      before = await metrics(); await search(page, '合成动作片', { results: false }); await settled(page)
      after = await metrics()
      assert.deepEqual(mainCalls(after).slice(mainCalls(before).length).map(call => call.metadata_status), [503, 200])
      assert.deepEqual(await page.locator('[data-online-result]').evaluateAll(nodes => nodes.map(node => node.dataset.onlineResult)), ['action-1'])

      phase.expectedHttp = true
      await configure(page, { metadata_failures_remaining: 2 })
      before = await metrics(); await page.locator('[data-online-refresh]').click(); await settled(page)
      const retry = page.getByRole('button', { name: '重试', exact: true })
      await retry.waitFor()
      assert.match(await page.locator('.online-refresh-error').textContent(), /上次结果/)
      assert.equal(mainCalls(await metrics()).length - mainCalls(before).length, 2)
      await delay(150)
      assert.equal(mainCalls(await metrics()).length - mainCalls(before).length, 2, 'Two failed attempts cannot start a loop')
      await retry.click(); await settled(page)
      assert.equal(mainCalls(await metrics()).length - mainCalls(before).length, 3)
      phase.expectedHttp = false

      await configure(page, { metadata_failures_remaining: 1, metadata_failure_status: 200, metadata_failure_delay_ms: 1300 })
      before = await metrics(); await page.locator('[data-online-refresh]').click(); await settled(page); await idle()
      assert.equal(mainCalls(await metrics()).length - mainCalls(before).length, 2, 'Real read timeout is recovered once')
      assert.equal(await page.locator('.online-refresh-error').count(), 0)

      // A successful main response may omit class. A warm source tree avoids
      // the failing extra read, but cannot substitute for the main response.
      await configure(page)
      await search(page, '', { results: false }); await settled(page)
      await configure(page, { taxonomy_unavailable: true })
      before = await metrics()
      let read = page.waitForResponse(response => new URL(response.url()).pathname === '/api/online/catalog/browse')
      await page.locator('[data-online-refresh]').click(); await settled(page)
      assert.equal((await (await read).json()).categories.length, 49)
      after = await metrics()
      assert.equal(mainCalls(after).length - mainCalls(before).length, 1)
      assert.equal(discoveries(after).length, discoveries(before).length, 'Cached taxonomy eliminates optional discovery')

      await scenario(page, { clear_taxonomy_cache: true })
      before = await metrics()
      read = page.waitForResponse(response => new URL(response.url()).pathname === '/api/online/catalog/browse')
      await page.locator('[data-online-refresh]').click(); await settled(page)
      assert.deepEqual((await (await read).json()).categories, [])
      assert.equal(discoveries(await metrics()).length - discoveries(before).length, 1,
        'Optional discovery is still one bounded read, without inheriting main retry')
      assert.equal(await page.locator('[data-online-channel="20"]').isVisible(), true)

      for (const attempt of [1, 2]) {
        await configure(page, { metadata_failures_remaining: 1,
          metadata_failure_delay_ms: attempt === 1 ? 1500 : 0,
          metadata_success_delay_ms: attempt === 2 ? 1500 : 0 })
        before = await metrics()
        await page.locator('[data-online-refresh]').click()
        await until(async () => mainCalls(await metrics()).length === mainCalls(before).length + attempt,
          `The real metadata attempt ${attempt} starts before cancellation`)
        await navigate(page, '/settings'); await idle()
        assert.equal(mainCalls(await metrics()).length - mainCalls(before).length, attempt,
          'Leaving never starts another request after an abandoned attempt')
        await configure(page); await selectSource(page); await settled(page)
      }
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `metadata-recovery-${width}.png`) })
      passed({ case: `metadata-recovery-${width}`, geometry, browse503Recovered: true, search503Recovered: true,
        realReadTimeoutRecovered: true, maximumAttempts: 2, manualRetryAfterExhaustion: true,
        cachedTaxonomySkipsDiscovery: true, uncachedDiscoveryNeverRetries: true,
        cancellationDuringBothAttempts: true, warmQueryReusesActualConnection: true, noAutoplay: true })
    } finally {
      phase.name = `metadata-recovery-${width}-restore`
      await navigate(page, '/settings'); await idle(); await page.waitForLoadState('networkidle')
      if (originalRule) assert.equal((await call(page, 'PATCH', `/api/online/sources/${primarySource.id}/network`, { api: originalRule })).status, 200)
      await configure(page, { taxonomy: false, clear_taxonomy_cache: true })
      await closeContext(context); await idle()
    }
  }
  await backendChildren('after-metadata-recovery')
}

// Advance only wall-clock freshness. Real timers, cancellation, socket budgets,
// playback and performance measurements keep their ordinary clocks.
function installRefreshClock() {
  const now = Date.now.bind(Date)
  const visibility = Object.getOwnPropertyDescriptor(Document.prototype, 'visibilityState')?.get
  const hidden = Object.getOwnPropertyDescriptor(Document.prototype, 'hidden')?.get
  let offset = 0, forcedVisible = null
  Date.now = () => now() + offset
  Object.defineProperty(document, 'visibilityState', { configurable: true,
    get: () => forcedVisible === null ? visibility?.call(document) || 'visible' : forcedVisible ? 'visible' : 'hidden' })
  Object.defineProperty(document, 'hidden', { configurable: true,
    get: () => forcedVisible === null ? !!hidden?.call(document) : !forcedVisible })
  window.__onlineRefreshClock = {
    advance(milliseconds) { offset += milliseconds },
    visible(value) { forcedVisible = value; document.dispatchEvent(new Event('visibilitychange')) },
  }
}

async function refreshStrategy() {
  const PERIOD = 15 * 60 * 1000
  const primaryReads = value => value.origin_calls.filter(call => call.kind === 'api' && call.metadata_status !== undefined)
  const imageReads = value => value.origin_calls.filter(call => call.kind === 'image')
  async function settled(page) {
    await until(async () => (await page.locator('[data-online-browser]').getAttribute('aria-busy')) === 'false',
      'A foreground directory request settles')
  }
  const ids = page => page.locator('[data-online-result]').evaluateAll(nodes => nodes.map(node => node.dataset.onlineResult))
  async function advance(page, milliseconds = PERIOD + 100) {
    await page.evaluate(value => window.__onlineRefreshClock.advance(value), milliseconds)
  }
  async function visible(page, value = true) {
    await page.evaluate(value => window.__onlineRefreshClock.visible(value), value)
  }
  async function check(page) {
    const response = page.waitForResponse(response => {
      const url = new URL(response.url())
      return ['/api/online/catalog/browse', '/api/online/catalog/search'].includes(url.pathname)
        && url.searchParams.get('priority') === 'background'
    })
    await advance(page); await visible(page)
    return response
  }
  async function manual(page) {
    await page.locator('[data-online-refresh]').click(); await settled(page); await idle()
  }
  async function captureImages(page) {
    return page.evaluate(() => {
      window.__refreshPosterNodes = [...document.querySelectorAll('.online-results .online-poster > img')]
        .filter(node => node.complete && node.naturalWidth > 0)
        .map(node => ({ node, src: node.src, id: node.closest('[data-online-browse-id]').dataset.onlineBrowseId }))
      return window.__refreshPosterNodes.length
    })
  }
  async function preservedImages(page) {
    const values = await page.evaluate(() => (window.__refreshPosterNodes || []).map(({ node, src, id }) => {
      const card = [...document.querySelectorAll('[data-online-browse-id]')].find(card => card.dataset.onlineBrowseId === id)
      const current = card?.querySelector('.online-poster > img')
      return { id, node: current === node, src: current?.src === src }
    }))
    assert.ok(values.length > 0)
    assert.ok(values.every(value => value.node && value.src), 'Successful poster nodes and Blob URLs survive metadata refresh')
  }
  async function configure(page, extra = {}) {
    await scenario(page, { taxonomy: true, taxonomy_unavailable: false, gallery: false, related: false,
      detail_mode: false, catalogue_version: 0, metadata_failures_remaining: 0,
      metadata_failure_status: 503, metadata_failure_delay_ms: 0, metadata_success_delay_ms: 0,
      poster_failure_id: '', poster_failures_remaining: 0, ...extra })
  }
  for (const width of [1440, 390]) {
    phase = { name: `refresh-strategy-${width}`, expectedHttp: true }
    const { context, page } = await newPage(width, { init: installRefreshClock })
    const requests = [], posterPaths = new Map()
    let held = null
    page.on('request', request => {
      const url = new URL(request.url())
      if (url.pathname.startsWith('/api/online/catalog/')) requests.push({ path: url.pathname, priority: url.searchParams.get('priority') })
    })
    page.on('response', async response => {
      const pathname = new URL(response.url()).pathname
      if (!['/api/online/catalog/browse', '/api/online/catalog/search'].includes(pathname) || response.status() !== 200) return
      const body = await response.json().catch(() => null)
      for (const item of body?.items || []) if (item.poster_path) posterPaths.set(item.external_id, item.poster_path)
    })
    const backgrounds = () => requests.filter(value => value.priority === 'background').length
    const countPath = path => requests.filter(value => value.path === path).length
    const successfulReads = async () => (await page.evaluate(() => window.__refreshPosterNodes.map(value => value.id))).map(id => {
      const path = posterPaths.get(id)
      assert.ok(path, 'Every captured successful poster has a known authorized path')
      return { path, count: countPath(path) }
    })
    const preservedReads = reads => assert.ok(reads.every(value => countPath(value.path) === value.count),
      'Successful posters keep their original request; newly visible missing posters may load')
    try {
      await configure(page, { poster_failure_id: 'urban-2026-1', poster_failures_remaining: 2, clear_artwork_cache: true })
      await selectSource(page); await settled(page)
      const first = page.locator('[data-online-browse-id="urban-2026-1"]')
      if (!await first.count()) { await page.locator('[data-online-refresh]').click(); await settled(page) }
      await first.locator('[data-online-artwork-state="error"]').waitFor()
      await idle(); await page.waitForLoadState('networkidle')
      phase.expectedHttp = false
      assert.ok(await captureImages(page) > 0)
      const failedPath = posterPaths.get('urban-2026-1')
      assert.ok(failedPath)
      const failedBefore = countPath(failedPath)
      const readyReads = await successfulReads()
      await manual(page)
      await first.locator('[data-online-artwork-state="ready"]').waitFor()
      await page.locator('[data-online-checked-at]').waitFor()
      await idle(); await preservedImages(page)
      assert.equal(countPath(failedPath) - failedBefore, 1, 'Manual refresh retries the failed poster once')
      preservedReads(readyReads)

      // A genuine route change remounts the view. Metadata stays in session
      // memory, while already cached original posters require no source read.
      let before = await metrics()
      await navigate(page, '/settings'); await advance(page, 14 * 60 * 1000)
      await selectSource(page); await settled(page); await idle()
      let after = await metrics()
      assert.equal(primaryReads(after).length, primaryReads(before).length, 'Returning inside fifteen minutes makes no main API read')
      assert.equal(imageReads(after).length, imageReads(before).length, 'Warm route restoration makes no upstream image read')
      assert.equal(await page.locator('[data-online-update-notice]').count(), 0)

      // Expired rows remain visible during a real delayed background read.
      await configure(page, { catalogue_version: 1, metadata_success_delay_ms: 1000 })
      const originalIds = await ids(page)
      await navigate(page, '/settings'); await advance(page, 60 * 1000 + 200)
      await selectSource(page)
      await page.locator('[data-online-background-refreshing]').waitFor()
      assert.deepEqual(await ids(page), originalIds)
      assert.equal(await page.locator('[data-online-result]').first().isDisabled(), false, 'Background work does not disable browsing')
      assert.match(await first.locator('[data-online-item-status]').textContent(), /第2集/)
      await first.locator('[data-online-result]').focus()
      await page.locator('[data-online-update-notice]').waitFor(); await idle()
      assert.deepEqual(await ids(page), originalIds)
      assert.match(await first.locator('[data-online-item-status]').textContent(), /第2集/)
      assert.equal(await first.locator('[data-online-result]').evaluate(node => document.activeElement === node), true)
      assert.ok(await captureImages(page) > 0)
      const applyBefore = primaryReads(await metrics()).length
      const applyPosters = await successfulReads()
      await page.screenshot({ path: path.join(output, `refresh-update-${width}.png`) })
      await page.locator('[data-online-apply-update]').evaluate(button => button.click())
      await until(async () => /第3集/.test(await first.locator('[data-online-item-status]').textContent()), 'Explicit apply updates episode remarks')
      assert.equal(primaryReads(await metrics()).length, applyBefore, 'A bounded pending result applies without an extra API request')
      assert.equal(await page.locator('[data-online-update-notice]').count(), 0)
      await preservedImages(page)
      preservedReads(applyPosters)

      // Identical results refresh freshness, with neither a notice nor image
      // remounts. A visibility event cannot immediately start another check.
      await configure(page, { catalogue_version: 1 })
      await manual(page); await captureImages(page)
      const unchangedPosters = await successfulReads()
      assert.equal((await (await check(page)).json()).status, 'ready')
      await idle()
      assert.equal(await page.locator('[data-online-update-notice]').count(), 0)
      await preservedImages(page); preservedReads(unchangedPosters)
      const checkedCount = backgrounds()
      await visible(page); await visible(page); await delay(200)
      assert.equal(backgrounds(), checkedCount, 'A successful check restarts the fifteen-minute attempt interval')

      // Ordering changes stay pending until an explicit application. The
      // focused, scrolled card retains its identity, anchor and successful img.
      await manual(page); await configure(page, { catalogue_version: 2 })
      const ordered = await ids(page)
      assert.equal((await (await check(page)).json()).status, 'ready')
      await page.locator('[data-online-update-notice]').waitFor(); await idle()
      assert.deepEqual(await ids(page), ordered)
      const anchor = page.locator(`[data-online-result="${ordered[6]}"]`)
      await anchor.evaluate(node => { node.scrollIntoView({ block: 'center' }); node.focus({ preventScroll: true }) })
      await idle(); await captureImages(page)
      const anchorBefore = await anchor.evaluate(node => node.getBoundingClientRect().top)
      await page.locator('[data-online-apply-update]').evaluate(button => button.click())
      await until(async () => JSON.stringify(await ids(page)) === JSON.stringify([...ordered].reverse()), 'Only an explicit apply replaces the ordered page')
      const anchorAfter = await anchor.evaluate(node => ({ top: node.getBoundingClientRect().top, focused: document.activeElement === node }))
      assert.ok(Math.abs(anchorAfter.top - anchorBefore) < 12, 'Applying a reordered page retains the scrolled anchor')
      assert.equal(anchorAfter.focused, true)
      await preservedImages(page)

      // A single real upstream failure is not retried by background work.
      await configure(page, { catalogue_version: 2 }); await manual(page)
      await scenario(page, { metadata_failures_remaining: 1, metadata_failure_status: 503 })
      before = await metrics(); phase.expectedHttp = true
      const failedCheck = await check(page)
      assert.equal(failedCheck.status(), 502)
      await page.locator('[data-online-refresh-check-error]').waitFor(); await idle(); await page.waitForLoadState('networkidle')
      phase.expectedHttp = false
      assert.equal(primaryReads(await metrics()).length - primaryReads(before).length, 1)
      assert.deepEqual(await ids(page), [...ordered].reverse())
      const failureCount = backgrounds()
      await visible(page); await delay(200)
      assert.equal(backgrounds(), failureCount, 'A failed attempt does not start a retry loop')

      // Occupy the one shared optional slot, then let the visible page's
      // automatic check receive deferred without a second upstream request.
      await configure(page, { catalogue_version: 2 }); await manual(page)
      await scenario(page, { metadata_success_delay_ms: 1800 })
      before = await metrics()
      held = call(page, 'GET', `/api/online/catalog/browse?source_id=${primarySource.id}&priority=background`)
      await until(async () => (await metrics()).admission.background === 1, 'A real optional reader occupies its bounded slot')
      const deferred = await check(page)
      assert.deepEqual(await deferred.json(), { status: 'deferred' })
      assert.equal((await held).status, 200); held = null
      await idle()
      assert.equal(primaryReads(await metrics()).length - primaryReads(before).length, 1)
      assert.equal(await page.locator('[data-online-update-notice]').count(), 0)
      const deferredCount = backgrounds(); await visible(page); await delay(150)
      assert.equal(backgrounds(), deferredCount, 'Deferred checks wait for the next period or a manual intent')

      // Opening a real detail preempts a slow background catalogue read and
      // still reaches actual MP4 decode through its separate media client.
      await configure(page, { catalogue_version: 2 }); await manual(page)
      await scenario(page, { catalogue_version: 3, metadata_success_delay_ms: 1800 })
      await advance(page); await visible(page)
      await until(async () => (await metrics()).admission.background === 1, 'The delayed automatic background read is active')
      const selected = (await ids(page))[0]
      assert.equal(await page.locator(`[data-online-result="${selected}"]`).isDisabled(), false)
      await detailUi(page, selected)
      await page.locator('[data-online-primary-play]').waitFor()
      await chooseLine(page, 'MP4直链'); await playEntry(page, '第1集'); await advancing(page); await closePlayer(page)
      await returned(page); await settled(page); await idle()
      assert.equal(await page.locator('[data-online-update-notice]').count(), 0, 'A preempted completion cannot stage a late update')

      // Hidden documents do no automatic catalogue I/O; visible documents
      // check once on return. This is a visibility-state simulation, not a
      // claim of a real phone background lifecycle.
      await configure(page, { catalogue_version: 2 }); await manual(page)
      await scenario(page, { catalogue_version: 3 })
      await visible(page, false); const hiddenCount = backgrounds()
      await advance(page); await delay(200)
      assert.equal(backgrounds(), hiddenCount)
      await visible(page)
      await page.locator('[data-online-update-notice]').waitFor(); await idle()
      assert.equal(backgrounds() - hiddenCount, 1)
      await page.locator('[data-online-apply-update]').click(); await settled(page)

      // Beyond the thirty-minute navigation lifetime, a route return must
      // perform a foreground read rather than revive an over-age snapshot.
      await manual(page); await navigate(page, '/settings'); await advance(page, 31 * 60 * 1000)
      const expiredBefore = requests.length
      await selectSource(page); await settled(page); await idle()
      assert.equal(requests.slice(expiredBefore).filter(value => value.path === '/api/online/catalog/browse' && value.priority === null).length, 1)

      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `refresh-strategy-${width}.png`) })
      phase = { name: `refresh-strategy-permission-${width}`, expectedHttp: true }
      const permissionErrors = report.expectedFailures.responses.length
      const password = 'isolated-refresh-strategy-protection'
      if (width === 1440) {
        assert.equal((await call(page, 'POST', '/api/viewing/password', { new_password: password })).status, 200)
        const protectedBefore = primaryReads(await metrics()).length
        await page.evaluate(() => window.dispatchEvent(new Event('focus')))
        await until(async () => primaryReads(await metrics()).length > protectedBefore,
          'The actual protection revision is revalidated before staging another result')
        await settled(page); await idle()
      }
      await configure(page, { catalogue_version: 2 }); await manual(page)
      await configure(page, { catalogue_version: 1 })
      assert.equal((await (await check(page)).json()).status, 'ready')
      await page.locator('[data-online-update-notice]').waitFor(); await idle()
      phase.expectedHttp = true
      const child = await call(page, 'POST', '/api/viewing/mode', { mode: 'children' })
      assert.equal(child.status, 200)
      await page.evaluate(generation => window.dispatchEvent(new CustomEvent('jzmedia:viewing-generation', {
        detail: { generation },
      })), child.body.generation)
      await until(async () => await page.locator('[data-online-result]').count() === 0
        && await page.locator('[data-online-update-notice]').count() === 0,
      'A real viewing-generation change clears displayed and pending content')
      const normal = await call(page, 'POST', '/api/viewing/mode', { mode: 'normal', password })
      assert.equal(normal.status, 200)
      await page.evaluate(generation => window.dispatchEvent(new CustomEvent('jzmedia:viewing-generation', {
        detail: { generation },
      })), normal.body.generation)
      await selectSource(page); await settled(page); await idle()
      await page.waitForLoadState('networkidle'); phase.expectedHttp = false
      assert.ok(report.expectedFailures.responses.slice(permissionErrors).every(value => [401, 403].includes(value.status)),
        'The permission transition permits only explicit authorization denials')
      assert.equal(await page.locator('[data-online-update-notice]').count(), 0)
      passed({ case: `refresh-strategy-${width}`, geometry, fakeFreshnessClockOnly: true,
        fifteenMinuteWarmReturn: true, staleWallDuringBackground: true, explicitPendingApplication: true,
        unchangedResultsStayQuiet: true, successfulPosterNodesPreserved: true, onlyFailedPosterRetried: true,
        reorderedPagePreservesAnchorAndFocus: true, backgroundDoesNotRetry: true,
        deferredDoesNotLoop: true, detailPreemptsBackground: true, actualMp4Decode: true,
        hiddenDocumentDoesNotCheck: true, thirtyMinuteExpiryRequiresRead: true,
        realViewingGenerationClearsPending: true })
    } finally {
      if (held) await held.catch(() => {})
      await visible(page)
      await navigate(page, '/settings'); await idle(); await page.waitForLoadState('networkidle')
      await configure(page, { taxonomy: false, catalogue_version: 0 })
      await closeContext(context); await idle()
    }
  }
  await backendChildren('after-refresh-strategy')
}

async function matrix() {
  for (const width of [1440, 390]) for (const transport of ['mp4', 'hls']) {
    phase = { name: `matrix-${width}-${transport}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    try {
      await selectSource(page)
      await search(page)
      await detailUi(page)
      assert.equal(await page.evaluate(() => window.__fixtureInjected === true), false, 'Provider text never executes HTML')
      await chooseLine(page, transport === 'mp4' ? 'MP4直链' : 'HLS直链')
      await page.screenshot({ path: path.join(output, `detail-${width}-${transport}.png`) })
      const firstFrameMs = await playEntry(page)
      const advanced = await advancing(page)
      const initial = await snapshot(page)
      assert.equal(initial.transport, transport)
      assert.match(initial.identity.line_id, /^ln_[a-f0-9]{32}$/)
      const beforeSeek = await pause(page)
      await page.getByRole('dialog').getByRole('button', { name: '快进 10 秒', exact: true }).click()
      await page.waitForFunction(value => document.querySelector('video')?.currentTime >= value + 9.4, beforeSeek.time)
      const sought = await readPlayer(page)
      assert.equal(sought.paused, true)
      await page.screenshot({ path: path.join(output, `player-${width}-${transport}.png`) })
      const geometry = await layout(page, width)
      const release = await closePlayer(page)
      assert.equal(await page.locator('[data-online-detail]').count(), 1)
      const detail = await call(page, 'GET', `/api/online/catalog/detail?source_id=${primarySource.id}&external_id=movie-1`)
      assertPrivateCatalogue(detail.body)
      const replayMs = await playEntry(page)
      const restored = await readPlayer(page)
      assert.ok(restored.time >= sought.time - .8, 'The page restores only this complete online identity')
      await closePlayer(page, { keyboard: true })
      passed({ case: phase.name, firstFrameMs, replayMs, initial, advanced, beforeSeek, sought, restored,
        releaseMs: release.releaseMs, geometry })
    } finally { await closeContext(context) }
  }
  phase = { name: 'matrix-320-detail', expectedHttp: false }
  const narrow = await newPage(320)
  try {
    await selectSource(narrow.page)
    await search(narrow.page)
    await detailUi(narrow.page, 'show-1')
    await chooseLine(narrow.page, 'HLS直链')
    const geometry = await layout(narrow.page, 320)
    await narrow.page.screenshot({ path: path.join(output, 'detail-320.png') })
    await playEntry(narrow.page, '第2集')
    await advancing(narrow.page)
    await closePlayer(narrow.page)
    passed({ case: phase.name, geometry, episodeTwoActuallyDecoded: true })
  } finally { await closeContext(narrow.context) }
  await backendChildren('after-matrix')
}

async function hostRepair() {
  async function dialogFocus(panel) {
    return panel.evaluate(root => {
      const selector = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
        + 'textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
      const nodes = [...root.querySelectorAll(selector)].filter(node => !node.matches(':disabled')
        && !node.closest('[inert]') && (node.offsetWidth > 0 || node.offsetHeight > 0))
      return { contained: root.contains(document.activeElement), containerFocused: document.activeElement === root, count: nodes.length,
        index: nodes.indexOf(document.activeElement) }
    })
  }
  async function assertDialogFocus(page, panel, label, cycle = false) {
    await until(async () => (await dialogFocus(panel)).contained,
      `${label}: opening the dialog must place keyboard focus inside it`)
    let initial = await dialogFocus(panel)
    assert.equal(initial.contained, true, `${label}: ${JSON.stringify(initial)}`)
    if (!cycle) return
    await until(async () => (await dialogFocus(panel)).count >= 3,
      `${label}: the loaded review must finish enabling native controls; initial=${JSON.stringify(initial)}`)
    initial = await dialogFocus(panel)
    assert.equal(initial.contained, true, `${label}: focus remains inside after controls settle; ${JSON.stringify(initial)}`)
    if (initial.index < 0) {
      assert.equal(initial.containerFocused, true,
        `${label}: only the dialog container may hold focus outside its tab sequence; ${JSON.stringify(initial)}`)
      await page.keyboard.press('Tab')
      initial = await dialogFocus(panel)
    }
    assert.ok(initial.contained && initial.count >= 3 && initial.index >= 0,
      `${label}: a real Tab enters the loaded review controls; ${JSON.stringify(initial)}`)
    for (const direction of [1, -1]) for (let step = 1; step <= initial.count; step++) {
      await page.keyboard.press(direction > 0 ? 'Tab' : 'Shift+Tab')
      const focused = await dialogFocus(panel)
      assert.equal(focused.contained, true, `${label}: keyboard focus remains inside during each tab stop`)
      assert.equal(focused.index, (initial.index + direction * step + initial.count) % initial.count,
        `${label}: ${direction > 0 ? 'Tab' : 'Shift+Tab'} wraps through the dialog controls`)
    }
  }
  async function assertDetailFocus(page, manual = false) {
    await until(() => page.evaluate(explicit => {
      const active = document.activeElement
      return explicit ? active?.matches('[data-online-host-repair]')
        : !!active?.closest('[data-online-detail]')
    }, manual), `Closing host review restores focus to ${manual ? 'its explicit trigger' : 'the current detail'}`)
  }
  for (const width of [1440, 390]) {
    phase = { name: `hosts-explicit-approval-${width}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    let original = null
    try {
      const source = await call(page, 'GET', `/api/online/sources/${primarySource.id}`)
      assert.equal(source.status, 200, JSON.stringify(source))
      original = source.body
      assert.equal(original.allowed_hosts.includes('media.fixture.invalid'), false)
      // Keep protection changes inside the dedicated hosts run, so --case all
      // retains its original unprotected fixture for the following scenarios.
      const protectedReview = width === 390 && selectedCase === 'hosts'
      const password = 'isolated-host-review-protection'
      if (protectedReview) {
        // Configure the isolated protected state while the old app is fully
        // unloaded, so its lazy artwork/health reads cannot cross revisions.
        await page.goto('about:blank')
        await idle()
        const configured = await context.request.post(`${base}/api/viewing/password`, {
          headers: { 'X-Api-Token': 'online-p2-fixture-only' }, data: { new_password: password },
        })
        assert.equal(configured.status(), 200)
        await page.goto(base); await page.waitForFunction(() => window.__jzOnlineP2Fixture?.snapshot().ready)
      }
      await scenario(page, { media_alias: true })
      await selectSource(page)
      await search(page)
      await idle()
      const checkPath = `/api/online/sources/${primarySource.id}/host-check`
      const allowPath = `/api/online/sources/${primarySource.id}/allow-hosts`
      const beforePanel = await metrics()
      const countChecks = value => value.api_calls.filter(call => call.path === checkPath).length
      const countWrites = value => value.api_calls.filter(call => call.path === allowPath).length
      const inspectedBefore = countChecks(beforePanel), writesBefore = countWrites(beforePanel)
      const mediaBefore = beforePanel.origin_calls.filter(call => call.kind === 'media').length
      let checked = protectedReview ? null : page.waitForResponse(response => new URL(response.url()).pathname === checkPath
        && response.request().method() === 'POST')
      await detailUi(page, 'show-1')
      const blocked = page.locator('[data-online-entry]')
      assert.equal(await blocked.count(), 2)
      assert.equal(await blocked.evaluateAll(elements => elements.every(element => element.disabled)), true,
        'the unapproved media alias makes every episode unavailable before explicit approval')
      const repair = page.locator('[data-online-host-repair]')
      await repair.waitFor()
      const panel = page.locator('[data-online-host-dialog]')
      await panel.waitFor()
      await assertDialogFocus(page, panel, `automatic host review at ${width}px`)
      assert.equal(await page.evaluate(() => window.__jzOnlineP2Fixture.snapshot().path), '/online',
        'host review opens in the current detail instead of navigating to settings')
      if (protectedReview) {
        await panel.getByRole('button', { name: '验证并检查', exact: true }).waitFor()
        await idle(); await delay(150)
        assert.equal(countChecks(await metrics()), inspectedBefore,
          'a protected viewer is reminded without automatically attempting an administrator request')
        assert.equal(await page.getByRole('dialog', { name: '验证保护密码', exact: true }).count(), 0,
          'an automatic reminder never opens the password prompt')
        checked = page.waitForResponse(response => new URL(response.url()).pathname === checkPath
          && response.request().method() === 'POST')
        await panel.getByRole('button', { name: '验证并检查', exact: true }).click()
        const authorization = page.getByRole('dialog', { name: '验证保护密码', exact: true })
        await authorization.waitFor()
        await authorization.getByLabel('保护密码', { exact: true }).fill(password)
        await authorization.getByRole('button', { name: '确认', exact: true }).click()
        await authorization.waitFor({ state: 'hidden' })
      }
      const checkResponse = await checked
      assert.equal(checkResponse.status(), 200)
      let reportBody = await checkResponse.json()
      assert.deepEqual(reportBody.missing_hosts, ['media.fixture.invalid'])
      assert.equal(JSON.stringify(reportBody).includes('fixture_signature'), false)
      assert.equal(JSON.stringify(reportBody).includes('http://'), false)
      assert.match(reportBody.configuration_fingerprint, /^[a-f0-9]{64}$/)
      const inspected = await metrics()
      assert.equal(countChecks(inspected), inspectedBefore + 1)
      assert.equal(inspected.origin_calls.filter(call => call.kind === 'media').length, mediaBefore,
        'host inspection reads directory metadata only; it never contacts the proposed media alias')
      assert.deepEqual((await call(page, 'GET', `/api/online/sources/${primarySource.id}`)).body.allowed_hosts,
        original.allowed_hosts, 'reading candidates must not silently trust them')
      const candidate = panel.getByRole('checkbox', { name: 'media.fixture.invalid', exact: true })
      await candidate.waitFor()
      assert.equal(await candidate.isChecked(), true)
      await assertDialogFocus(page, panel,
        `${protectedReview ? 'management authorization returned to' : 'loaded'} host review at ${width}px`, true)
      await page.screenshot({ path: path.join(output, `hosts-review-${width}.png`), fullPage: true })

      // A declined automatic reminder stays dismissed, while the detail still
      // offers a keyboard-accessible way to review candidates on demand.
      await page.keyboard.press('Escape')
      await panel.waitFor({ state: 'hidden' })
      await assertDetailFocus(page)
      await idle(); await delay(150)
      assert.equal(countWrites(await metrics()), writesBefore, 'dismissing review never changes the source')
      assert.equal(await page.locator('video').count(), 0)
      assert.equal(await panel.count(), 0, 'a dismissed reminder does not immediately reopen')
      await returned(page); await idle()
      await detailUi(page, 'show-1')
      await page.locator('[data-online-detail] [data-online-favorite]:enabled').waitFor()
      await idle()
      assert.equal(await panel.count(), 0, 'returning to the same item in this page does not repeat the reminder')
      assert.equal(countChecks(await metrics()), inspectedBefore + 1)
      await chooseLine(page, 'HLS直链')
      const originalLine = await page.locator('[data-online-line][aria-pressed="true"]').getAttribute('data-online-line')
      assert.equal(await panel.count(), 0, 'switching playback lines does not repeat the reminder')
      checked = page.waitForResponse(response => new URL(response.url()).pathname === checkPath
        && response.request().method() === 'POST')
      await repair.focus(); await page.keyboard.press('Enter')
      await panel.waitFor()
      reportBody = await (await checked).json()
      await candidate.waitFor()
      assert.equal(countChecks(await metrics()), inspectedBefore + 2)
      await assertDialogFocus(page, panel, `manually reopened host review at ${width}px`, true)
      await page.keyboard.press('Escape')
      await panel.waitFor({ state: 'hidden' })
      await assertDetailFocus(page, true)
      checked = page.waitForResponse(response => new URL(response.url()).pathname === checkPath
        && response.request().method() === 'POST')
      await page.keyboard.press('Enter')
      await panel.waitFor()
      reportBody = await (await checked).json()
      await candidate.waitFor()
      assert.equal(countChecks(await metrics()), inspectedBefore + 3)
      await candidate.uncheck()
      assert.equal(await panel.getByRole('button', { name: '确认添加', exact: true }).isEnabled(), false)
      await candidate.check()

      let configurationConflictRecovered = false
      if (width === 1440) {
        phase.expectedHttp = true
        assert.equal((await call(page, 'PATCH', `/api/online/sources/${primarySource.id}`, {
          name: original.name + ' · 并发修改',
        })).status, 200)
        const conflict = page.waitForResponse(response => new URL(response.url()).pathname === allowPath
          && response.request().method() === 'POST')
        await panel.getByRole('button', { name: '确认添加', exact: true }).click()
        assert.equal((await conflict).status(), 409, 'stale source approval cannot overwrite a concurrent edit')
        await panel.locator('[data-online-host-error]').waitFor()
        assert.equal(await candidate.count(), 0, 'a stale fingerprint clears its candidate selection')
        checked = page.waitForResponse(response => new URL(response.url()).pathname === checkPath
          && response.request().method() === 'POST')
        await panel.getByRole('button', { name: '重新检查', exact: true }).click()
        reportBody = await (await checked).json()
        await candidate.waitFor()
        phase.expectedHttp = false
        configurationConflictRecovered = true
      }
      const applied = page.waitForResponse(response => new URL(response.url()).pathname === allowPath
        && response.request().method() === 'POST')
      await panel.getByRole('button', { name: '确认添加', exact: true }).click()
      const applyResponse = await applied
      assert.equal(applyResponse.status(), 200)
      assert.deepEqual(applyResponse.request().postDataJSON(), {
        hosts: ['media.fixture.invalid'], expected_fingerprint: reportBody.configuration_fingerprint,
      })
      const saved = await applyResponse.json()
      assert.deepEqual(saved.allowed_hosts, [...original.allowed_hosts, 'media.fixture.invalid'])
      for (const key of ['id', 'name', 'provider', 'api_base_url', 'provider_options', 'enabled', 'priority']) {
        assert.deepEqual(saved[key], key === 'name' && configurationConflictRecovered ? original.name + ' · 并发修改' : original[key],
          `approval must preserve the most recent ${key}`)
      }
      await panel.waitFor({ state: 'hidden' })
      await page.locator('[data-online-detail]').waitFor()
      assert.equal(await page.evaluate(() => window.__jzOnlineP2Fixture.snapshot().path), '/online')
      assert.equal(await page.locator('[data-online-line][aria-pressed="true"]').getAttribute('data-online-line'), originalLine,
        'refreshing the approved detail preserves the selected playback line')
      assert.equal(await page.locator('video').count(), 0, 'confirming media hosts never starts playback')
      assert.equal(await page.locator('[data-online-entry]').evaluateAll(elements => elements.every(element => !element.disabled)), true)
      const firstFrameMs = await playEntry(page, '第1集')
      const advanced = await advancing(page)
      assert.equal((await snapshot(page)).transport, 'hls')
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `hosts-player-${width}.png`) })
      const released = await closePlayer(page)
      const aliasReads = released.metrics.origin_calls.filter(call => call.kind === 'media' && call.host === 'media.fixture.invalid')
      assert.ok(aliasReads.length >= 2, 'HLS manifests and media resources use only the explicitly approved loopback alias')
      passed({ case: phase.name, initiallyBlockedEpisodes: 2, automaticDialog: true, inspectionOnlyMetadata: true,
        protectedReview, explicitPasswordOnly: protectedReview, cancelledWithoutWrite: true,
        keyboardReopen: true, keyboardFocusContained: true, tabAndShiftTabWrap: true, escapeRestoresFocus: true,
        sameDetailAndLinePreserved: true, noAutoplayAfterApproval: true, configurationConflictRecovered,
        addedHosts: ['media.fixture.invalid'], firstFrameMs, advanced, geometry,
        aliasMediaRequests: aliasReads.length, releaseMs: released.releaseMs })
    } finally {
      // Keep the existing discovery/matrix/catalogue cases' origin and source
      // configuration unchanged when this scenario runs as part of --case all.
      await scenario(page, { media_alias: false }).catch(() => {})
      if (original) await call(page, 'PATCH', `/api/online/sources/${primarySource.id}`, {
        allowed_hosts: original.allowed_hosts, name: original.name,
      }).catch(() => {})
      await closeContext(context)
    }
  }
  await backendChildren('after-host-repair')
}
async function catalogue() {
  phase = { name: 'catalogue-stable-lines-and-refresh', expectedHttp: false }
  const { context, page } = await newPage()
  try {
    await selectSource(page)
    await search(page)
    await detailUi(page, 'show-1')
    const getDetail = () => call(page, 'GET', `/api/online/catalog/detail?source_id=${primarySource.id}&external_id=show-1`)
    const first = (await getDetail()).body
    const identities = value => Object.fromEntries(value.lines.flatMap(line => line.entries.map(entry => [line.label + ':' + entry.label, entry.identity])))
    const firstIdentities = identities(first)
    assert.equal(Object.keys(firstIdentities).length, 4)
    await chooseLine(page, 'MP4直链')
    await playEntry(page, '第1集')
    const firstPlay = await advancing(page)
    const firstSession = await snapshot(page)
    await closePlayer(page)
    await chooseLine(page, 'HLS直链')
    await playEntry(page, '第2集')
    const secondPlay = await advancing(page)
    const secondSession = await snapshot(page)
    assert.notEqual(firstSession.identity.episode_id, secondSession.identity.episode_id)
    assert.notEqual(firstSession.identity.line_id, secondSession.identity.line_id)
    await closePlayer(page)
    await scenario(page, { revision: 2, reverse_lines: true })
    const fresh = (await getDetail()).body
    assert.deepEqual(identities(fresh), firstIdentities, 'Line order and URL signatures never change identity')
    assertPrivateCatalogue(fresh)
    await returned(page)
    await detailUi(page, 'show-1')
    await chooseLine(page, 'HLS直链')
    await playEntry(page, '第2集')
    await advancing(page)
    await closePlayer(page)
    passed({ case: phase.name, firstIdentities, firstSession, secondSession, firstPlay, secondPlay,
      refreshedIdentityStable: true, refreshedMediaActuallyDecoded: true })

    phase = { name: 'catalogue-unsupported-web-source', expectedHttp: false }
    await returned(page)
    await detailUi(page, 'unsupported-1')
    const unsupported = page.locator('[data-online-entry]')
    assert.ok(await unsupported.count() > 0)
    for (const button of await unsupported.all()) assert.equal(await button.isDisabled(), true)
    assert.equal(await page.locator('video').count(), 0)
    passed({ case: phase.name, disabledUnsupportedEntries: await unsupported.count() })

    phase = { name: 'catalogue-empty-search', expectedHttp: false }
    await returned(page)
    await search(page, 'none', { results: false })
    await page.getByText(/没有找到|暂无匹配|没有匹配/).first().waitFor()
    assert.equal(await page.locator('[data-online-result]').count(), 0)
    passed({ case: phase.name })

    phase = { name: 'catalogue-error-retry', expectedHttp: true }
    await scenario(page, { next_mode: 'malformed' })
    await search(page, '合成电影', { results: false })
    await page.getByRole('button', { name: '重新加载', exact: true }).waitFor()
    await page.getByRole('button', { name: '重新加载', exact: true }).click()
    await page.locator('[data-online-result]').first().waitFor()
    passed({ case: phase.name, explicitErrorThenRetry: true })

    phase = { name: 'catalogue-cancel-late-search', expectedHttp: false }
    await scenario(page, { delay_ms: 900 })
    await search(page, '合成剧集', { results: false })
    await until(async () => (await metrics()).pending_requests > 0, 'The delayed origin query must actually start')
    await navigate(page, '/settings')
    await until(async () => (await metrics()).pending_requests === 0, 'The bounded delayed request must finish after cancellation')
    await selectSource(page)
    await search(page, 'none', { results: false })
    await page.getByText(/没有找到|暂无匹配|没有匹配/).first().waitFor()
    assert.equal(await page.locator('[data-online-result]').count(), 0, 'A discarded request cannot repaint a later page')
    passed({ case: phase.name, discardedLateResult: true })

    phase = { name: 'catalogue-close-during-provider-resolution', expectedHttp: false }
    await search(page)
    await detailUi(page)
    await chooseLine(page, 'MP4直链')
    const beforeCancelledCreate = await metrics()
    await scenario(page, { delay_ms: 1500 })
    await page.locator('[data-online-entry]').filter({ hasText: '正片' }).click()
    await page.getByRole('dialog').waitFor()
    await until(async () => (await metrics()).pending_requests > 0,
      'The real provider detail HTTP request must be blocked before closing the modal')
    const cancelled = await closePlayer(page)
    assert.equal(cancelled.metrics.created_sessions.length, beforeCancelledCreate.created_sessions.length,
      'Cancelling the HTTP provider resolution must not publish a late session')
    assert.equal(cancelled.metrics.catalogue_active, 0)
    assert.equal(cancelled.metrics.catalogue_owners, 0)
    passed({ case: phase.name, releaseMs: cancelled.releaseMs, registry: cancelled.metrics.registry,
      catalogueActive: cancelled.metrics.catalogue_active, catalogueOwners: cancelled.metrics.catalogue_owners,
      scope: 'Real blocked MacCMS HTTP detail and formal POST abort, without replacing providers.resolve' })

    phase = { name: 'catalogue-source-disable-revokes-session', expectedHttp: true }
    await search(page)
    await detailUi(page)
    await chooseLine(page, 'HLS直链')
    await playEntry(page)
    await advancing(page)
    const beforeDisable = await snapshot(page)
    const disabled = await call(page, 'PATCH', `/api/online/sources/${primarySource.id}`, { enabled: false })
    assert.equal(disabled.status, 200)
    await idle('Disabling the source must revoke its real active formal session')
    const denied = await call(page, 'HEAD', `/api/online/sessions/${beforeDisable.sessionId}/media`)
    assert.equal(denied.status, 404)
    await closePlayer(page)
    assert.equal((await call(page, 'PATCH', `/api/online/sources/${primarySource.id}`, { enabled: true })).status, 200)
    passed({ case: phase.name, revokedOwnedSession: beforeDisable.sessionId })

    phase = { name: 'catalogue-open-sample-explicit-attribution', expectedHttp: false }
    const sample = await call(page, 'POST', '/api/online/sources', { name: '开放样例（未启用）', provider: 'open_catalog',
      api_base_url: 'https://www.bigbuckbunny.org/', enabled: false })
    assert.equal(sample.status, 201, JSON.stringify(sample))
    let listed = (await call(page, 'GET', '/api/online/catalog/sources')).body
    assert.equal(listed.items.some(item => item.id === sample.body.id), false, 'An added open sample remains opt-in')
    assert.equal((await call(page, 'PATCH', `/api/online/sources/${sample.body.id}`, { enabled: true })).status, 200)
    const sampleSearch = await call(page, 'GET', `/api/online/catalog/search?source_id=${sample.body.id}&q=Big&page=1`)
    assert.equal(sampleSearch.status, 200, JSON.stringify(sampleSearch))
    const sampleExternal = sampleSearch.body.items[0]?.external_id
    assert.ok(sampleExternal)
    const sampleDetail = await call(page, 'GET', `/api/online/catalog/detail?source_id=${sample.body.id}&external_id=${encodeURIComponent(sampleExternal)}`)
    assert.equal(sampleDetail.status, 200, JSON.stringify(sampleDetail))
    assert.ok(sampleDetail.body.attribution.some(item => /blender|big.?buck/i.test(item.label + item.url)))
    assert.ok(sampleDetail.body.attribution.some(item => /creativecommons.org\/licenses\/by\/3.0/.test(item.url)))
    assert.equal((await call(page, 'PATCH', `/api/online/sources/${sample.body.id}`, { enabled: false })).status, 200)
    listed = (await call(page, 'GET', '/api/online/catalog/sources')).body
    assert.equal(listed.items.some(item => item.id === sample.body.id), false)
    passed({ case: phase.name, initiallyDisabled: true, attribution: sampleDetail.body.attribution,
      scope: 'Only the fixed local catalogue metadata is read; no public media request or external link navigation' })

    phase = { name: 'catalogue-permission-boundaries', expectedHttp: true }
    const password = 'isolated-p2-protection'
    assert.equal((await call(page, 'POST', '/api/viewing/password', { new_password: password })).status, 200)
    assert.equal((await call(page, 'GET', '/api/online/sources')).status, 403,
      'Configuration remains protected by a management grant')
    assert.equal((await call(page, 'GET', '/api/online/catalog/sources')).status, 200,
      'Normal viewing may browse without a management grant')
    assert.equal((await call(page, 'POST', '/api/viewing/mode', { mode: 'children' })).status, 200)
    for (const apiPath of ['/api/online/catalog/sources',
      `/api/online/catalog/search?source_id=${primarySource.id}&q=合成&page=1`,
      `/api/online/catalog/detail?source_id=${primarySource.id}&external_id=movie-1`]) {
      assert.equal((await call(page, 'GET', apiPath)).status, 403, 'Children cannot obtain online catalogue content')
    }
    assert.equal((await call(page, 'POST', '/api/viewing/mode', { mode: 'normal', password })).status, 200)
    assert.equal((await call(page, 'GET', '/api/online/sources')).status, 200)
    passed({ case: phase.name, scope: 'Real viewing and management HTTP guards; App navigation visibility is checked separately',
      managementRequiredForConfiguration: true, childrenCatalogueDenied: true })
  } finally { await closeContext(context) }
  await backendChildren('after-catalogue')
}

try {
  await mkdir(output, { recursive: true })
  for (const file of ['scripts/smoke_online_p2.mjs', 'scripts/fixtures/OnlineP2Preview.vue',
    'scripts/online_p2/server.py', 'scripts/online_p2/fixtures.py', 'frontend/src/views/Online.vue',
    'frontend/src/components/OnlineSourcesPanel.vue', 'frontend/src/App.vue', 'frontend/src/styles/base.css',
    'frontend/src/components/OnlinePlayerModal.vue',
    'frontend/src/onlinePlayback.js', 'app/online/maccms.py', 'app/online/catalog.py',
    'frontend/src/onlineArtwork.js', 'frontend/src/components/OnlineArtwork.vue', 'frontend/src/onlineCatalog.js',
    'frontend/src/onlineBrowse.js', 'app/online/artwork.py', 'app/online/artwork_cache.py',
    'app/online/artwork_disk.py', 'app/online/image_pool.py',
    'frontend/src/onlineCategories.js', 'frontend/src/onlineRelated.js', 'app/online/related.py', 'app/online/related_matching.py',
    'frontend/src/components/OnlineEpisodes.vue', 'frontend/src/onlineEpisodes.js', 'frontend/src/styles/onlineEpisodes.css',
    'app/online/taxonomy_cache.py', 'app/online/api_pool.py', 'app/online/runtime.py',
    'app/network/stages.py', 'app/network/client.py',
    'app/network/targets.py', 'app/network/transport.py',
    'app/online/providers.py', 'app/online/catalog_models.py', 'app/routers/online_catalog.py',
    'frontend/src/styles/online.css']) {
    report.fingerprints[file] = createHash('sha256').update(await readFile(path.join(root, file))).digest('hex')
  }
  await runGenerator()
  checkStop()
  await buildFixture()
  checkStop()
  await startBackend()
  if (demo) {
    console.log(`在线来源隔离预览：${base}/`)
    console.log('临时数据库、真实MacCMS JSON解析与24秒合成样片。Ctrl+C关闭并清理。')
    while (!stopping) await delay(200)
  } else {
    browser = await chromium.launch({ headless: true, handleSIGINT: false, handleSIGTERM: false,
      args: ['--no-proxy-server', '--renderer-process-limit=2', '--num-raster-threads=1',
        '--autoplay-policy=no-user-gesture-required'] })
    report.browser = { version: browser.version(), executable: chromium.executablePath() }
    await setup()
    if (selectedCase === 'all' || selectedCase === 'hosts') await hostRepair()
    if (selectedCase === 'all' || selectedCase === 'discovery') await discovery()
    if (selectedCase === 'all' || selectedCase === 'loading') await loading()
    if (selectedCase === 'all' || selectedCase === 'image-cache') await imageCache()
    if (selectedCase === 'all' || selectedCase === 'categories') await categoryNavigation()
    if (selectedCase === 'all' || selectedCase === 'related') await relatedRecommendations()
    if (selectedCase === 'all' || selectedCase === 'detail') await detailExperience()
    if (selectedCase === 'all' || selectedCase === 'detail-recovery') await detailRecovery()
    if (selectedCase === 'all' || selectedCase === 'metadata-recovery') await metadataRecovery()
    if (selectedCase === 'all' || selectedCase === 'refresh-strategy') await refreshStrategy()
    if (selectedCase === 'all' || selectedCase === 'matrix') await matrix()
    if (selectedCase === 'all' || selectedCase === 'catalogue') await catalogue()
    report.finalMetrics = (await idle()).metrics
    assert.ok(!report.finalMetrics.origin_calls.some(call => call.app_credentials), 'Application credentials never reach the source')
    assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'api'), 'The API uses its own network-purpose client')
    if (selectedCase === 'metadata-recovery') assert.ok(!report.finalMetrics.client_calls.some(call => call.purpose === 'media'), 'Metadata recovery never requests media')
    else if (!['discovery', 'loading', 'image-cache', 'categories'].includes(selectedCase)) assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'media'), 'Media uses its separate network-purpose client')
    assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'image'), 'Posters use their image-purpose client')
    assert.deepEqual(report.errors, [], 'No browser runtime, Vue or unexpected console errors')
    assert.deepEqual(report.unexpectedRequests, [], 'No foreign origin or local playback/cast API request')
    assert.deepEqual(report.unexpectedResponses, [], 'Clean scenarios have no unexpected HTTP errors')
    await backendChildren('completed-browser-checks')
    report.mediaHashesAfter = await mediaHashes(path.join(work, 'media'))
    assert.deepEqual(report.mediaHashesAfter, report.mediaHashesBefore, 'Serving and playback never modify generated media')
    console.log(`PASS online P2 ${selectedCase}: ${report.checks.length} scenarios`)
  }
} catch (error) {
  report.failedPhase = phase.name
  report.lastMetrics = await metrics().catch(() => null)
  failure = error
} finally {
  for (const context of contexts) { try { await context.close() } catch { /* Browser may already be stopped. */ } }
  try { await browser?.close() } catch { /* Interrupt cleanup continues. */ }
  report.cleanup.browserClosed = true
  killOwned(generator, 'SIGTERM')
  if (backend && !stopped(backend)) {
    killOwned(backend, 'SIGTERM')
    await Promise.race([new Promise(resolve => backend.once('exit', resolve)), delay(4000)])
    if (!stopped(backend)) {
      killOwned(backend, 'SIGKILL')
      await Promise.race([new Promise(resolve => backend.once('exit', resolve)), delay(1000)])
    }
  }
  report.cleanup.backendStopped = stopped(backend)
  await rm(work, { recursive: true, force: true })
  report.cleanup.temporaryRootRemoved = true
  report.backendLog = backendLog
  await mkdir(output, { recursive: true })
  if (failure) report.failure = { message: String(failure), stack: failure.stack || '' }
  const target = path.join(output, `${demo ? 'demo' : selectedCase}-${failure ? 'failure' : 'manifest'}.json`)
  await writeFile(target, JSON.stringify(report, null, 2) + '\n')
  if (selectedCase === 'all' && !demo) await writeFile(path.join(output, failure ? 'failure.json' : 'manifest.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(`证据：${target}`)
  process.removeListener('SIGINT', interrupted)
  process.removeListener('SIGTERM', interrupted)
}
if (failure) throw failure
