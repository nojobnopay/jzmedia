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
    console.log('Usage: node scripts/smoke_online_p3.mjs [--demo] [--case state|autoplay|all]')
    process.exit(0)
  } else throw new Error(`Unknown argument: ${value}`)
}
assert.ok(['state', 'autoplay', 'all'].includes(selectedCase), 'Invalid --case')
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-online-p2-p3-'))
const output = path.join(root, 'output/playwright/online-p3')
const dist = path.join(work, 'dist')
const entryId = 'virtual:jzmedia-online-p3'
const report = { generatedAt: new Date().toISOString(), case: selectedCase,
  demo,
  scope: 'Production online state APIs, real Online page and actual media-end continuation, with the guarded P2 MacCMS/media fixture and temporary database. Desktop Chromium viewport/touch emulation; no real phone/TV, public source or human audio acceptance.',
  fingerprints: {}, checks: [], errors: [], unexpectedRequests: [], unexpectedResponses: [],
  expectedFailures: { responses: [], console: [] }, abortedRequests: [], backendChildrenChecks: [],
  cleanup: {} }
let backend = null, generator = null, browser = null, backendLog = '', failure = null
let base = '', stopping = false, phase = { name: 'setup', expectedHttp: false }
const contexts = new Set()
const claims = new WeakMap()
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))
const stopped = child => !child || child.exitCode !== null || child.signalCode !== null
function passed(check) { report.checks.push(check); console.log(`PASS online P3 ${check.case}`) }

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
    return value.registry.sessions === 0 && value.registry.pending_sessions === 0 && value.registry.active_requests === 0
      && value.registry.reserved_bytes === 0 && value.registry.resources === 0 && value.registry.resource_url_bytes === 0
      && value.pending_requests === 0
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
  generator = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_p3.server', '--root', work, '--generate-media'],
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
import Preview from ${JSON.stringify(path.join(root, 'scripts/fixtures/OnlineP3Preview.vue'))};
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
    plugins: [vue(), { name: 'online-p3-preview', resolveId: id => id === entryId ? '\0' + entryId : null,
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
  backend = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_p3.server', '--root', work,
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
  claims.set(page, [])
  page.on('response', async response => {
    if (new URL(response.url()).pathname === '/api/online/state/progress/claim' && response.ok()) {
      try { claims.get(page).push(await response.json()) } catch { /* Page closed after response. */ }
    }
  })
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
async function closePlayer(page, { keyboard = false, focusKey = null, waitIdle = true } = {}) {
  if (keyboard) await page.keyboard.press('Escape')
  else await page.getByRole('dialog').getByRole('button', { name: '关闭播放器', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.equal(await page.locator('video').count(), 0, 'Closed player detaches its video element')
  assert.equal(await page.evaluate(() => window.__jzOnlinePlayer || null), null)
  if (focusKey) await page.waitForFunction(key => document.activeElement?.dataset.onlineItem === key, focusKey)
  return waitIdle ? idle() : null
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
async function call(page, method, apiPath, body, extraHeaders = {}) {
  return page.evaluate(async ({ method, apiPath, body, extraHeaders }) => {
    const response = await fetch(apiPath, { method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Api-Token': localStorage.getItem('jzmedia.token') || '', ...extraHeaders },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    const text = await response.text()
    let value
    try { value = JSON.parse(text) } catch { value = text }
    return { status: response.status, body: value }
  }, { method, apiPath, body, extraHeaders })
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
  await form.getByLabel('启用此来源', { exact: true }).check()
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
    assert.deepEqual(disabled.body, { enabled: false, items: [] })
    primarySource = await createSourceUi(page, '隔离合成MacCMS')
    await page.getByRole('button', { name: '网络 ' + primarySource.name, exact: true }).click()
    const network = page.locator('[data-online-network-form]')
    for (const label of ['来源默认', 'API 查询', '媒体取流']) await network.getByLabel(label, { exact: true }).selectOption('direct')
    await network.getByRole('button', { name: '保存网络规则', exact: true }).click()
    await network.waitFor({ state: 'hidden' })
    const policy = await call(page, 'GET', `/api/online/sources/${primarySource.id}/network`)
    assert.equal(policy.status, 200, JSON.stringify(policy))
    for (const key of ['default', 'api', 'media']) assert.equal(policy.body.policy[key].mode, 'direct')
    await page.screenshot({ path: path.join(output, 'source-management-1440.png') })
    const publicSources = await call(page, 'GET', '/api/online/catalog/sources')
    assertPrivateCatalogue(publicSources.body)
    assert.equal(publicSources.body.items.length, 1)
    passed({ case: phase.name, sourceId: primarySource.id, explicitlyEnabled: true, networkPolicy: policy.body,
      scope: 'Source creation, feature enable and explicit API/media direct rules via the real management UI' })
  } finally { await closeContext(context) }
}

function stateHeaders(scope) { return { 'X-Online-State-Scope': scope } }
async function itemState(page, identity) {
  const params = new URLSearchParams()
  if (identity.episode_id) params.set('episode_id', identity.episode_id)
  if (identity.line_id) params.set('line_id', identity.line_id)
  const response = await call(page, 'GET', `/api/online/state/items/${identity.item_id}?${params}`)
  assert.equal(response.status, 200, JSON.stringify(response))
  return response.body
}
async function library(page, section = 'history') {
  const response = await call(page, 'GET', `/api/online/state/library?section=${section}&limit=20&offset=0`)
  assert.equal(response.status, 200, JSON.stringify(response))
  assertPrivateCatalogue(response.body)
  return response.body
}
async function showLibrary(page, section) {
  await page.locator(`[data-online-library-section="${section}"]`).click()
}
async function reload(page) {
  await page.reload()
  await page.waitForFunction(() => window.__jzOnlineP2Fixture?.snapshot().ready)
}
async function showMovie(page, externalId = 'movie-1') {
  await selectSource(page)
  await search(page)
  await detailUi(page, externalId)
}
async function savePause(page, identity, minimum) {
  return until(async () => {
    const value = await itemState(page, identity)
    return value.selected_progress?.paused && value.selected_progress.position >= minimum ? value.selected_progress : false
  }, 'The real progress API must persist this paused original-timeline position')
}
async function seekActual(page, position) {
  await page.locator('video').evaluate((video, position) => { video.currentTime = position }, position)
  await page.waitForFunction(position => {
    const video = document.querySelector('video')
    return video && !video.seeking && Math.abs(video.currentTime - position) < .7
  }, position)
}
async function readyClaim(page, afterRevision = 0) {
  return until(() => claims.get(page)?.find(value => value.writer_revision > afterRevision),
    'Real first-frame playback must obtain a server writer claim')
}
async function stateScenarios() {
  phase = { name: 'state-favorite-and-persistent-resume', expectedHttp: false }
  const { context, page } = await newPage()
  let movie, mp4Identity, mp4Position, hlsIdentity
  try {
    await showMovie(page)
    const detail = page.locator('[data-online-detail]')
    const result = (await call(page, 'GET', `/api/online/catalog/detail?source_id=${primarySource.id}&external_id=movie-1`)).body
    movie = result.item
    await detail.locator(`[data-online-favorite="${movie.id}"]`).click()
    await until(async () => (await library(page, 'favorites')).items.some(row => row.item.id === movie.id && row.favorite),
      'A favorite chosen in the real UI must persist')
    await reload(page)
    await showLibrary(page, 'favorites')
    const favoriteRow = page.locator(`[data-online-state-item="${movie.id}"]`)
    await favoriteRow.waitFor()
    await favoriteRow.locator(`[data-online-library-detail="${movie.id}"]`).click()
    await page.locator('[data-online-detail]').waitFor()
    await chooseLine(page, 'MP4直链')
    await playEntry(page)
    await advancing(page)
    mp4Identity = (await snapshot(page)).identity
    await readyClaim(page)
    await pause(page)
    await page.getByRole('dialog').getByRole('button', { name: '快进 10 秒', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('video')?.currentTime >= 10)
    mp4Position = (await readPlayer(page)).time
    await savePause(page, mp4Identity, mp4Position - .8)
    await closePlayer(page)
    await reload(page)
    await showLibrary(page, 'continue')
    await page.locator(`[data-online-resume="${movie.id}"]`).click()
    await loaded(page)
    const resumed = await readPlayer(page)
    assert.ok(resumed.time >= mp4Position - .8, 'Refreshing the page restores server-side position rather than only component memory')
    assert.equal((await snapshot(page)).identity.line_id, mp4Identity.line_id)
    await page.screenshot({ path: path.join(output, 'persistent-resume-1440.png') })
    await closePlayer(page)
    passed({ case: phase.name, itemId: movie.id, mp4Position, resumed, favoriteSurvivesReload: true })

    phase = { name: 'state-health-observes-without-probing', expectedHttp: false }
    const beforeHealth = (await metrics()).origin_calls.length
    await page.getByRole('button', { name: '刷新状态', exact: true }).click()
    await page.locator('[data-online-health]').filter({ hasText: '查询通过 · 直连' }).waitFor()
    await page.locator('[data-online-health]').filter({ hasText: '取流通过 · 直连' }).waitFor()
    const observed = await call(page, 'GET', `/api/online/catalog/health?source_id=${primarySource.id}`)
    assert.equal(observed.status, 200)
    assert.equal(observed.body.purposes.api.state, 'success')
    assert.equal(observed.body.purposes.media.state, 'success')
    assert.equal(observed.body.purposes.api.mode, 'direct')
    assert.equal(observed.body.purposes.media.mode, 'direct')
    assert.equal((await metrics()).origin_calls.length, beforeHealth, 'Refreshing health only reads observations and never probes the origin')
    passed({ case: phase.name, observed: observed.body, extraOriginRequests: 0 })

    phase = { name: 'state-line-progress-isolation', expectedHttp: false }
    await chooseLine(page, 'HLS直链')
    await playEntry(page)
    await advancing(page)
    hlsIdentity = (await snapshot(page)).identity
    assert.notEqual(hlsIdentity.line_id, mp4Identity.line_id)
    const hlsPaused = await pause(page)
    assert.ok(hlsPaused.time < 6, 'A previously unplayed line must not inherit the MP4 position')
    await savePause(page, hlsIdentity, Math.max(0, hlsPaused.time - .8))
    await closePlayer(page)
    const mp4 = await itemState(page, mp4Identity), hlsState = await itemState(page, hlsIdentity)
    assert.ok(mp4.selected_progress.position >= mp4Position - .8)
    assert.ok(hlsState.selected_progress.position < 6)
    await reload(page)
    await showLibrary(page, 'continue')
    await page.locator(`[data-online-resume="${movie.id}"]`).click()
    await loaded(page)
    assert.equal((await snapshot(page)).identity.line_id, hlsIdentity.line_id)
    await closePlayer(page)
    await chooseLine(page, 'MP4直链')
    await playEntry(page)
    await loaded(page)
    assert.ok((await readPlayer(page)).time >= mp4Position - .8)
    await closePlayer(page)
    passed({ case: phase.name, mp4: mp4.selected_progress, hls: hlsState.selected_progress })

    phase = { name: 'state-device-isolation', expectedHttp: true }
    const stranger = await newPage(390)
    try {
      assert.equal((await library(stranger.page, 'favorites')).total, 0)
      assert.equal((await library(stranger.page, 'continue')).total, 0)
      const own = await itemState(stranger.page, mp4Identity)
      assert.equal(own.favorite, false)
      assert.equal(own.selected_progress, null)
      const claim = claims.get(page)[0]
      const denied = await call(stranger.page, 'POST', '/api/online/state/progress', {
        writer_id: claim.writer_id, writer_revision: claim.writer_revision,
        seq: 999, position: 1, duration: 24, paused: true, completed: false,
      }, stateHeaders(own.scope_token))
      assert.ok([403, 404, 409].includes(denied.status), 'Another real device cannot write a stolen progress writer')
      assert.ok((await itemState(page, mp4Identity)).selected_progress.position >= mp4Position - .8)
      passed({ case: phase.name, deniedStatus: denied.status, otherDeviceFavorites: 0, otherDeviceContinue: 0 })
    } finally { await closeContext(stranger.context) }

    phase = { name: 'state-stale-tab-writer-conflict', expectedHttp: true }
    await chooseLine(page, 'MP4直链')
    const oldCount = claims.get(page).length
    await playEntry(page)
    await until(() => claims.get(page).length > oldCount, 'The first tab owns a current writer')
    const oldClaim = claims.get(page).at(-1)
    await pause(page)
    const other = await context.newPage()
    trackPage(other)
    try {
      await other.goto(base)
      await other.waitForFunction(() => window.__jzOnlineP2Fixture?.snapshot().ready)
      await showMovie(other)
      await chooseLine(other, 'MP4直链')
      await playEntry(other)
      await readyClaim(other, oldClaim.writer_revision)
      const freshClaim = claims.get(other).at(-1)
      assert.ok(freshClaim.writer_revision > oldClaim.writer_revision)
      const fresh = await pause(other)
      const denied = await call(page, 'POST', '/api/online/state/progress', {
        writer_id: oldClaim.writer_id, writer_revision: oldClaim.writer_revision,
        seq: 9999, position: 1, duration: 24, paused: true, completed: false,
      }, stateHeaders(oldClaim.scope_token))
      assert.equal(denied.status, 409)
      assert.equal(denied.body.code, 'online_progress_writer_conflict')
      await closePlayer(page, { waitIdle: false })
      const saved = await savePause(other, mp4Identity, fresh.time - .8)
      assert.ok(saved.position > 5, 'A stale closing tab cannot replace newer playback with its old position')
      await closePlayer(other)
      passed({ case: phase.name, previousRevision: oldClaim.writer_revision, newRevision: freshClaim.writer_revision,
        conflict: denied.body.code, retainedPosition: saved.position })
    } finally { await other.close() }

    phase = { name: 'state-disabled-source-retention-and-delete', expectedHttp: false }
    assert.equal((await call(page, 'PATCH', `/api/online/sources/${primarySource.id}`, { enabled: false })).status, 200)
    await reload(page)
    await showLibrary(page, 'favorites')
    const retained = await library(page, 'favorites')
    const row = retained.items.find(row => row.item.id === movie.id)
    assert.ok(row && row.favorite && !row.available)
    const unavailable = page.locator(`[data-online-state-item="${movie.id}"]`)
    await unavailable.waitFor()
    await unavailable.locator(`[data-online-favorite="${movie.id}"]`).click()
    await until(async () => (await library(page, 'favorites')).total === 0, 'Disabled-source favorites can still be removed')
    await showLibrary(page, 'history')
    await page.locator(`[data-online-history-delete="${movie.id}"]`).click()
    await until(async () => !(await library(page, 'history')).items.some(row => row.item.id === movie.id),
      'Disabled-source history can be removed without fetching the provider')
    assert.equal((await call(page, 'PATCH', `/api/online/sources/${primarySource.id}`, { enabled: true })).status, 200)
    passed({ case: phase.name, preservedWhileDisabled: true, removableWhileDisabled: true })

    phase = { name: 'state-private-normal-children-isolation', expectedHttp: true }
    await reload(page)
    await showMovie(page)
    await page.locator('[data-online-detail]').locator(`[data-online-favorite="${movie.id}"]`).click()
    await until(async () => (await library(page, 'favorites')).items.some(row => row.item.id === movie.id),
      'Normal favorite must be committed before the protection revision changes')
    const normalScope = (await library(page, 'favorites')).scope_token
    const password = 'isolated-p3-protection'
    assert.equal((await call(page, 'POST', '/api/viewing/password', { new_password: password })).status, 200)
    assert.equal((await call(page, 'POST', '/api/viewing/mode', { mode: 'private', password })).status, 200)
    const staleIntent = await call(page, 'PUT', `/api/online/state/items/${movie.id}/favorite`, { favorite: false }, stateHeaders(normalScope))
    assert.equal(staleIntent.status, 409, 'A normal-mode intent must not be reissued into private state')
    await reload(page)
    assert.equal((await library(page, 'favorites')).total, 0)
    await showMovie(page, 'show-1')
    const show = (await call(page, 'GET', `/api/online/catalog/detail?source_id=${primarySource.id}&external_id=show-1`)).body.item
    await page.locator('[data-online-detail]').locator(`[data-online-favorite="${show.id}"]`).click()
    await until(async () => (await library(page, 'favorites')).items.some(row => row.item.id === show.id), 'Private favorite is stored separately')
    assert.equal((await call(page, 'POST', '/api/viewing/mode', { mode: 'normal' })).status, 200)
    await reload(page)
    const normal = await library(page, 'favorites')
    assert.deepEqual(normal.items.map(row => row.item.id), [movie.id])
    assert.equal((await call(page, 'POST', '/api/viewing/mode', { mode: 'children' })).status, 200)
    await reload(page)
    assert.equal(await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('link', { name: '在线', exact: true }).count(), 0)
    assert.equal((await call(page, 'GET', '/api/online/state/library?section=favorites')).status, 403)
    assert.equal((await call(page, 'POST', '/api/viewing/mode', { mode: 'normal', password })).status, 200)
    await reload(page)
    await showLibrary(page, 'favorites')
    await page.screenshot({ path: path.join(output, 'favorites-1440.png') })
    passed({ case: phase.name, normalFavorite: movie.id, privateFavorite: show.id, staleScopeDenied: staleIntent.status,
      childrenHaveNoOnlineNavigation: true })
  } finally { await closeContext(context) }
  phase = { name: 'state-phone-layout', expectedHttp: false }
  for (const width of [390, 320]) {
    const phone = await newPage(width)
    try {
      await showMovie(phone.page)
      const item = (await call(phone.page, 'GET', `/api/online/catalog/detail?source_id=${primarySource.id}&external_id=movie-1`)).body.item
      await phone.page.locator('[data-online-detail]').locator(`[data-online-favorite="${item.id}"]`).click()
      await showLibrary(phone.page, 'favorites')
      await phone.page.locator(`[data-online-state-item="${item.id}"]`).waitFor()
      const geometry = await layout(phone.page, width)
      await phone.page.screenshot({ path: path.join(output, `favorites-${width}.png`) })
      passed({ case: `state-phone-${width}`, geometry })
    } finally { await closeContext(phone.context) }
  }
  await backendChildren('after-state')
}

async function captureActualEnd(page) {
  await page.locator('video').evaluate(video => {
    window.__jzP3NativeEnds ||= []
    video.addEventListener('ended', event => {
      window.__jzP3NativeEnds.push({ trusted: event.isTrusted, time: video.currentTime, duration: video.duration,
        frames: video.getVideoPlaybackQuality?.().totalVideoFrames || 0,
        identity: window.__jzOnlinePlayer?.snapshot().identity })
    }, { capture: true, once: true })
  })
}
async function nearEndAndPlay(page) {
  await captureActualEnd(page)
  await pause(page)
  await page.locator('video').evaluate(video => { video.playbackRate = 4; video.currentTime = video.duration - .9 })
  await page.waitForFunction(() => document.querySelector('video') && !document.querySelector('video').seeking)
  await page.getByRole('dialog').getByRole('button', { name: '播放', exact: true }).click()
}
async function actualEnds(page, minimum = 1) {
  await page.waitForFunction(minimum => (window.__jzP3NativeEnds?.length || 0) >= minimum, minimum)
  const values = await page.evaluate(() => window.__jzP3NativeEnds)
  assert.ok(values.every(value => value.trusted && value.frames > 0 && value.time >= value.duration - .2),
    'Continuation evidence must come from real native ended events after decoded frames, never dispatched events')
  return values
}
async function autoplayScenarios() {
  phase = { name: 'autoplay-default-off-and-completion', expectedHttp: false }
  const { context, page } = await newPage(390)
  try {
    await showMovie(page, 'show-1')
    await chooseLine(page, 'MP4直链')
    const autoplay = page.locator('[data-online-autoplay]')
    assert.equal(await autoplay.isChecked(), false, 'Automatic continuation is opt-in for this browser and mode')
    await playEntry(page, '第1集')
    const firstIdentity = (await snapshot(page)).identity
    await readyClaim(page)
    await pause(page)
    await seekActual(page, 22.4)
    await delay(300)
    assert.equal((await snapshot(page)).identity.episode_id, firstIdentity.episode_id)
    assert.equal((await itemState(page, firstIdentity)).selected_progress.completed, false,
      'A high watched percentage must not mark natural completion or advance the episode')
    const createdBefore = (await metrics()).created_sessions.length
    await nearEndAndPlay(page)
    const defaultEnds = await actualEnds(page)
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
    await idle()
    assert.equal((await metrics()).created_sessions.length, createdBefore)
    await until(async () => (await itemState(page, firstIdentity)).selected_progress?.completed,
      'Only actual ended should persist completion')
    passed({ case: phase.name, firstIdentity, defaultEnds, extraSessions: 0 })

    phase = { name: 'autoplay-real-ended-same-line-next-and-last-stop', expectedHttp: false }
    await autoplay.check()
    await playEntry(page, '第1集')
    await loaded(page)
    const replay = await snapshot(page)
    assert.equal(replay.identity.episode_id, firstIdentity.episode_id)
    await nearEndAndPlay(page)
    await actualEnds(page, 2)
    await page.waitForFunction(episodeId => {
      const state = window.__jzOnlinePlayer?.snapshot()
      return state?.identity.episode_id && state.identity.episode_id !== episodeId && state.firstFrame
    }, firstIdentity.episode_id)
    const second = await snapshot(page), secondClock = await advancing(page)
    assert.equal(second.identity.source_id, firstIdentity.source_id)
    assert.equal(second.identity.item_id, firstIdentity.item_id)
    assert.equal(second.identity.line_id, firstIdentity.line_id)
    assert.ok(secondClock.after.time < 6, 'The next episode starts its own original timeline')
    const createdAtSecond = (await metrics()).created_sessions.length
    await nearEndAndPlay(page)
    const ends = await actualEnds(page, 3)
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
    await idle()
    assert.equal((await metrics()).created_sessions.length, createdAtSecond, 'The last episode never loops to the first')
    await page.screenshot({ path: path.join(output, 'autoplay-last-episode-390.png') })
    passed({ case: phase.name, first: firstIdentity, second: second.identity, secondClock, nativeEnds: ends,
      lastEpisodeStopped: true })

    phase = { name: 'autoplay-cancel-pending-next', expectedHttp: false }
    await playEntry(page, '第1集')
    await loaded(page)
    await scenario(page, { delay_ms: 1500 })
    const before = (await metrics()).created_sessions.length
    await nearEndAndPlay(page)
    await actualEnds(page, 4)
    await page.locator('[data-online-next-loading]').waitFor()
    await page.getByRole('button', { name: '取消连续播放', exact: true }).click()
    await idle('Cancel must stop the real delayed next-detail request without creating another media session')
    assert.equal((await metrics()).created_sessions.length, before)
    assert.equal(await page.locator('video').count(), 0)
    passed({ case: phase.name, createdAfterCancellation: 0, noVideoElement: true })

    phase = { name: 'autoplay-navigate-away-cancels-next', expectedHttp: false }
    await autoplay.check()
    await playEntry(page, '第1集')
    await loaded(page)
    await scenario(page, { delay_ms: 1500 })
    const beforeLeave = (await metrics()).created_sessions.length
    await nearEndAndPlay(page)
    await actualEnds(page, 5)
    await page.locator('[data-online-next-loading]').waitFor()
    await navigate(page, '/settings')
    await idle('Leaving the real online route must cancel pending continuation')
    assert.equal((await metrics()).created_sessions.length, beforeLeave)
    passed({ case: phase.name, createdAfterRouteLeave: 0 })
  } finally { await closeContext(context) }
  await backendChildren('after-autoplay')
}

try {
  await mkdir(output, { recursive: true })
  for (const file of ['scripts/smoke_online_p3.mjs', 'scripts/fixtures/OnlineP3Preview.vue', 'scripts/fixtures/OnlineP2Preview.vue',
    'scripts/online_p3/server.py', 'scripts/online_p3/fixtures.py',
    'app/online/state.py', 'app/online/state_models.py', 'app/store/online_state.py',
    'app/store/online_state_schema.py', 'app/routers/online_state.py',
    'scripts/online_p2/server.py', 'scripts/online_p2/fixtures.py', 'frontend/src/views/Online.vue',
    'frontend/src/components/OnlineSourcesPanel.vue', 'frontend/src/App.vue', 'frontend/src/styles/base.css',
    'frontend/src/components/OnlinePlayerModal.vue', 'frontend/src/styles/online.css', 'frontend/src/api.js',
    'frontend/src/onlinePlayback.js', 'frontend/src/onlineState.js', 'frontend/src/onlineCatalog.js',
    'app/online/health.py', 'app/online/health_models.py', 'app/routers/online_health.py', 'app/online/gateway.py',
    'app/online/maccms.py', 'app/online/catalog.py',
    'app/online/providers.py', 'app/routers/online_catalog.py']) {
    report.fingerprints[file] = createHash('sha256').update(await readFile(path.join(root, file))).digest('hex')
  }
  await runGenerator()
  checkStop()
  await buildFixture()
  checkStop()
  await startBackend()
  if (demo) {
    console.log(`在线状态隔离预览：${base}/`)
    console.log('临时数据库、真实状态API和MacCMS、24秒合成样片。自动连播须真实播到结尾。Ctrl+C关闭并清理。')
    while (!stopping) await delay(200)
  } else {
    browser = await chromium.launch({ headless: true, handleSIGINT: false, handleSIGTERM: false,
      args: ['--no-proxy-server', '--renderer-process-limit=2', '--num-raster-threads=1',
        '--autoplay-policy=no-user-gesture-required'] })
    report.browser = { version: browser.version(), executable: chromium.executablePath() }
    await setup()
    if (selectedCase === 'all' || selectedCase === 'state') await stateScenarios()
    if (selectedCase === 'all' || selectedCase === 'autoplay') await autoplayScenarios()
    report.finalMetrics = (await idle()).metrics
    assert.ok(!report.finalMetrics.origin_calls.some(call => call.app_credentials), 'Application credentials never reach the source')
    assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'api'), 'The API uses its own network-purpose client')
    assert.ok(report.finalMetrics.client_calls.some(call => call.purpose === 'media'), 'Media uses its separate network-purpose client')
    assert.deepEqual(report.errors, [], 'No browser runtime, Vue or unexpected console errors')
    assert.deepEqual(report.unexpectedRequests, [], 'No foreign origin or local playback/cast API request')
    assert.deepEqual(report.unexpectedResponses, [], 'Clean scenarios have no unexpected HTTP errors')
    await backendChildren('completed-browser-checks')
    report.mediaHashesAfter = await mediaHashes(path.join(work, 'media'))
    assert.deepEqual(report.mediaHashesAfter, report.mediaHashesBefore, 'Serving and playback never modify generated media')
    console.log(`PASS online P3 ${selectedCase}: ${report.checks.length} scenarios`)
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
