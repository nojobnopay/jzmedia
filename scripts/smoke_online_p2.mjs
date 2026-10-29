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
    console.log('Usage: node scripts/smoke_online_p2.mjs [--demo] [--case matrix|catalogue|discovery|hosts|all]')
    process.exit(0)
  } else throw new Error(`Unknown argument: ${value}`)
}
assert.ok(['matrix', 'catalogue', 'discovery', 'hosts', 'all'].includes(selectedCase), 'Invalid --case')
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
      && value.catalogue_active === 0 && value.catalogue_owners === 0 ? value : false
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
  await select.waitFor()
  await select.selectOption(source.id)
}
async function search(page, keyword = '合成', { results = true } = {}) {
  const form = page.locator('[data-online-search]')
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
      await page.waitForFunction(() => [...document.querySelectorAll('.online-results img')]
        .filter(image => image.complete && image.naturalWidth > 0).length >= 2)
      assert.equal(await page.locator('.online-connection').evaluate(element => element.open), false)
      const geometry = await layout(page, width)
      const images = await page.locator('.online-results img').evaluateAll(elements => elements.map(image => ({
        url: new URL(image.src).pathname, decoded: image.complete && image.naturalWidth > 0,
      })))
      assert.ok(images.length >= 2 && images.every(image => image.decoded && image.url.startsWith('/api/online/catalog/artwork/')))
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
    await search(page, '合成', { results: false })
    await page.getByRole('button', { name: '重新加载', exact: true }).waitFor()
    await page.getByRole('button', { name: '重新加载', exact: true }).click()
    await page.locator('[data-online-result]').first().waitFor()
    passed({ case: phase.name, explicitErrorThenRetry: true })

    phase = { name: 'catalogue-cancel-late-search', expectedHttp: false }
    await scenario(page, { delay_ms: 900 })
    await search(page, '合成', { results: false })
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
    if (selectedCase === 'all' || selectedCase === 'matrix') await matrix()
    if (selectedCase === 'all' || selectedCase === 'catalogue') await catalogue()
    report.finalMetrics = (await idle()).metrics
    assert.ok(!report.finalMetrics.origin_calls.some(call => call.app_credentials), 'Application credentials never reach the source')
    assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'api'), 'The API uses its own network-purpose client')
    if (selectedCase !== 'discovery') assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'media'), 'Media uses its separate network-purpose client')
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
