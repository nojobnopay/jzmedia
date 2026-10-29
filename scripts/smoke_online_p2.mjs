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
    console.log('Usage: node scripts/smoke_online_p2.mjs [--demo] [--case matrix|catalogue|discovery|loading|categories|related|detail|hosts|all]')
    process.exit(0)
  } else throw new Error(`Unknown argument: ${value}`)
}
assert.ok(['matrix', 'catalogue', 'discovery', 'loading', 'categories', 'related', 'detail', 'hosts', 'all'].includes(selectedCase), 'Invalid --case')
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
      body: JSON.stringify({ taxonomy, gallery: false, image_delay_ms: 0 }) })
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
      await scenario(page, { next_mode: 'status_error' })
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
        parentQueryRemainsExact: true, customYear: '1988', emptyYear: '1900', pageAndAnchorRestored: true,
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
        related_failure: false, reverse_lines: false, clear_related_cache: true, ...plan }) })
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
      assert.ok(extraCalls.length <= 4, 'One selected detail plus at most three recommendation metadata calls')
      assert.ok(extraCalls.filter(call => call.external_id).length <= 2, 'Cards never trigger per-item detail fan-out')
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

      phase = { name: `related-failure-${width}`, expectedHttp: true }
      await configure({ related_failure: true })
      await openSearch(page, '青山书院第二季', 'academy-s2')
      await finished(page)
      assert.equal(await page.locator('[data-online-entry]').first().isEnabled(), true, 'Failed recommendations cannot disable playback')
      await configure({})
      if (await page.locator('[data-online-related-retry]').count()) await page.locator('[data-online-related-retry]').click()
      else { await returned(page); await detailUi(page, 'academy-s2') }
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
        lateResultsDiscarded: true, failureDoesNotBlockPlayback: true, foregroundPlayPreempts: true,
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
        body: JSON.stringify({ related: false, related_delay_ms: 0, related_failure: false, clear_related_cache: true }) })
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
      // A previous directory may still be valid for sixty seconds. Wait for
      // its current work, then explicitly request the synthetic gallery.
      await until(async () => (await page.locator('.online-results [data-online-artwork-state="loading"]').count()) === 0,
        'Previously visible posters finish before the cold measurement', 25000)
      await idle()
      assert.equal((await call(page, 'POST', '/api/p2/scenario', { clear_artwork_cache: true })).status, 200)
      const before = await metrics(), started = performance.now()
      const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/online/catalog/browse'
        && response.status() === 200)
      await page.locator('[data-online-refresh]').click()
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
      assert.equal(await visibleEntries(page).count(), 50, 'A 123-entry line mounts only one 50-entry group')
      const group = page.locator('[data-online-entry-group]'), order = page.locator('[data-online-entry-order]')
      assert.equal(await group.locator('option').count(), 3)
      await group.selectOption('1')
      assert.equal((await labels(page))[0], '第51集')
      assert.equal(await page.locator('[data-online-entry="第52集"]').isEnabled(), false)
      await group.selectOption('2')
      assert.equal(await visibleEntries(page).count(), 23)
      assert.equal((await labels(page))[0], '番外·特别篇')
      assert.equal((await labels(page)).at(-1), '终章（下）')
      await order.selectOption('desc')
      assert.equal(await group.inputValue(), '2')
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
        sourceEntries: 123, maxMountedEntries: 50, preservesNonstandardLabels: true,
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
  for (const width of [1440, 390]) {
    phase = { name: `hosts-explicit-approval-${width}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    let original = null
    try {
      const source = await call(page, 'GET', `/api/online/sources/${primarySource.id}`)
      assert.equal(source.status, 200, JSON.stringify(source))
      original = source.body
      assert.equal(original.allowed_hosts.includes('media.fixture.invalid'), false)
      await scenario(page, { media_alias: true })
      await selectSource(page)
      await search(page)
      await detailUi(page, 'show-1')
      const blocked = page.locator('[data-online-entry]')
      assert.equal(await blocked.count(), 2)
      assert.equal(await blocked.evaluateAll(elements => elements.every(element => element.disabled)), true,
        'the unapproved media alias makes every episode unavailable before explicit approval')
      const repair = page.locator('[data-online-host-repair]')
      await repair.waitFor()
      const repairTarget = new URL(await repair.getAttribute('href'), base)
      assert.equal(repairTarget.pathname, '/settings')
      assert.equal(repairTarget.searchParams.get('source'), primarySource.id)
      assert.equal(repairTarget.searchParams.get('item'), 'show-1')
      await page.screenshot({ path: path.join(output, `hosts-blocked-${width}.png`), fullPage: true })
      const beforePanel = await metrics()
      const checkPath = `/api/online/sources/${primarySource.id}/host-check`
      const allowPath = `/api/online/sources/${primarySource.id}/allow-hosts`
      const inspectedBefore = beforePanel.api_calls.filter(call => call.path === checkPath).length
      const mediaBefore = beforePanel.origin_calls.filter(call => call.kind === 'media').length
      await repair.click()
      await page.waitForFunction(() => window.__jzOnlineP2Fixture.snapshot().path === '/settings')
      const panel = page.locator('[data-online-host-check]')
      await panel.waitFor()
      assert.equal(await panel.locator('#online-host-item').inputValue(), 'show-1')
      await idle()
      assert.equal((await metrics()).api_calls.filter(call => call.path === checkPath).length, inspectedBefore,
        'following the deep link opens the panel without automatically inspecting any upstream')
      const checked = page.waitForResponse(response => new URL(response.url()).pathname === checkPath
        && response.request().method() === 'POST')
      await panel.getByRole('button', { name: '检查媒体主机', exact: true }).click()
      const checkResponse = await checked
      assert.equal(checkResponse.status(), 200)
      const reportBody = await checkResponse.json()
      assert.deepEqual(reportBody.missing_hosts, ['media.fixture.invalid'])
      assert.equal(JSON.stringify(reportBody).includes('fixture_signature'), false)
      assert.equal(JSON.stringify(reportBody).includes('http://'), false)
      assert.match(reportBody.configuration_fingerprint, /^[a-f0-9]{64}$/)
      const inspected = await metrics()
      assert.equal(inspected.api_calls.filter(call => call.path === checkPath).length, inspectedBefore + 1)
      assert.equal(inspected.origin_calls.filter(call => call.kind === 'media').length, mediaBefore,
        'host inspection reads directory metadata only; it never contacts the proposed media alias')
      assert.deepEqual((await call(page, 'GET', `/api/online/sources/${primarySource.id}`)).body.allowed_hosts,
        original.allowed_hosts, 'reading candidates must not silently trust them')
      const candidate = panel.getByRole('checkbox', { name: 'media.fixture.invalid', exact: true })
      await candidate.waitFor()
      assert.equal(await candidate.isChecked(), true)
      await page.screenshot({ path: path.join(output, `hosts-review-${width}.png`), fullPage: true })
      const applied = page.waitForResponse(response => new URL(response.url()).pathname === allowPath
        && response.request().method() === 'POST')
      await panel.getByRole('button', { name: '加入允许列表', exact: true }).click()
      const applyResponse = await applied
      assert.equal(applyResponse.status(), 200)
      assert.deepEqual(applyResponse.request().postDataJSON(), {
        hosts: ['media.fixture.invalid'], expected_fingerprint: reportBody.configuration_fingerprint,
      })
      const saved = await applyResponse.json()
      assert.deepEqual(saved.allowed_hosts, [...original.allowed_hosts, 'media.fixture.invalid'])
      for (const key of ['id', 'name', 'provider', 'api_base_url', 'provider_options', 'enabled', 'priority']) {
        assert.deepEqual(saved[key], original[key], `approval must preserve ${key}`)
      }
      await panel.getByText('媒体主机已加入允许列表。返回影片详情后会重新读取播放线路。', { exact: true }).waitFor()
      const back = panel.locator('[data-online-host-return]')
      const returnTarget = new URL(await back.getAttribute('href'), base)
      assert.equal(returnTarget.pathname, '/online')
      assert.equal(returnTarget.searchParams.get('source'), primarySource.id)
      assert.equal(returnTarget.searchParams.get('item'), 'show-1')
      await back.click()
      await page.waitForFunction(() => window.__jzOnlineP2Fixture.snapshot().path === '/online')
      await page.locator('[data-online-detail]').waitFor()
      await chooseLine(page, 'HLS直链')
      assert.equal(await page.locator('[data-online-entry]').evaluateAll(elements => elements.every(element => !element.disabled)), true)
      const firstFrameMs = await playEntry(page, '第1集')
      const advanced = await advancing(page)
      assert.equal((await snapshot(page)).transport, 'hls')
      const geometry = await layout(page, width)
      await page.screenshot({ path: path.join(output, `hosts-player-${width}.png`) })
      const released = await closePlayer(page)
      const aliasReads = released.metrics.origin_calls.filter(call => call.kind === 'media' && call.host === 'media.fixture.invalid')
      assert.ok(aliasReads.length >= 2, 'HLS manifests and media resources use only the explicitly approved loopback alias')
      passed({ case: phase.name, initiallyBlockedEpisodes: 2, inspectionOnlyMetadata: true,
        addedHosts: ['media.fixture.invalid'], firstFrameMs, advanced, geometry,
        aliasMediaRequests: aliasReads.length, releaseMs: released.releaseMs })
    } finally {
      // Keep the existing discovery/matrix/catalogue cases' origin and source
      // configuration unchanged when this scenario runs as part of --case all.
      await scenario(page, { media_alias: false }).catch(() => {})
      if (original) await call(page, 'PATCH', `/api/online/sources/${primarySource.id}`, {
        allowed_hosts: original.allowed_hosts,
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
    'frontend/src/onlineArtwork.js', 'frontend/src/onlineBrowse.js', 'app/online/artwork.py', 'app/online/artwork_cache.py',
    'frontend/src/onlineCategories.js', 'frontend/src/onlineRelated.js', 'app/online/related.py', 'app/online/related_matching.py',
    'frontend/src/components/OnlineEpisodes.vue', 'frontend/src/onlineEpisodes.js', 'frontend/src/styles/onlineEpisodes.css',
    'app/online/providers.py', 'app/routers/online_catalog.py']) {
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
    if (selectedCase === 'all' || selectedCase === 'categories') await categoryNavigation()
    if (selectedCase === 'all' || selectedCase === 'related') await relatedRecommendations()
    if (selectedCase === 'all' || selectedCase === 'detail') await detailExperience()
    if (selectedCase === 'all' || selectedCase === 'matrix') await matrix()
    if (selectedCase === 'all' || selectedCase === 'catalogue') await catalogue()
    report.finalMetrics = (await idle()).metrics
    assert.ok(!report.finalMetrics.origin_calls.some(call => call.app_credentials), 'Application credentials never reach the source')
    assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'api'), 'The API uses its own network-purpose client')
    if (!['discovery', 'loading', 'categories'].includes(selectedCase)) assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'media'), 'Media uses its separate network-purpose client')
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
