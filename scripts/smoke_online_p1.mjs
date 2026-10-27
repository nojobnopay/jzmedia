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
    console.log('Usage: node scripts/smoke_online_p1.mjs [--demo] [--case matrix|lifecycle|failures|all]')
    process.exit(0)
  } else throw new Error(`Unknown argument: ${value}`)
}
assert.ok(['matrix', 'lifecycle', 'failures', 'all'].includes(selectedCase), 'Invalid --case')
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-online-p1-'))
const output = path.join(root, 'output/playwright/online-p1')
const dist = path.join(work, 'dist')
const entryId = 'virtual:jzmedia-online-p1'
const report = { generatedAt: new Date().toISOString(), case: selectedCase,
  demo,
  scope: 'Formal online gateway and OnlinePlayerModal with synthetic media and a temporary database. Desktop Chromium viewport/touch emulation; no real phone/TV, public source or human audio acceptance.',
  fingerprints: {}, checks: [], errors: [], unexpectedRequests: [], unexpectedResponses: [],
  expectedFailures: { responses: [], console: [] }, abortedRequests: [], backendChildrenChecks: [],
  nativeHls: { advertised: null, verified: false, reason: 'not checked' }, cleanup: {} }
let backend = null, generator = null, browser = null, backendLog = '', failure = null
let base = '', stopping = false, phase = { name: 'setup', expectedHttp: false }
const contexts = new Set()
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))
const stopped = child => !child || child.exitCode !== null || child.signalCode !== null
function passed(check) { report.checks.push(check); console.log(`PASS online P1 ${check.case}`) }

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
  const response = await fetch(`${base}/api/p1/metrics`, { signal: AbortSignal.timeout(2000) })
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
      && value.registry.reserved_bytes === 0 && value.pending_resolutions === 0 ? value : false
  }, message)
  assert.deepEqual(value.local_rows, { movies: 0, tv_episodes: 0, playback_progress: 0 })
  return { releaseMs: Math.round(performance.now() - started), metrics: value }
}
async function scenario(page, plan) {
  const response = await page.evaluate(async plan => {
    const response = await fetch('/api/p1/scenario', { method: 'POST',
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
  generator = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_p1.server', '--root', work, '--generate-media'],
    { cwd: root, env: { ...process.env, PYTHONUNBUFFERED: '1' }, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  generator.stdout.on('data', data => { log = (log + data.toString()).slice(-10000) })
  generator.stderr.on('data', data => { log = (log + data.toString()).slice(-10000) })
  const code = await new Promise((resolve, reject) => { generator.once('error', reject); generator.once('exit', resolve) })
  assert.equal(code, 0, `Synthetic generation failed: ${log}`)
  report.mediaHashesBefore = await mediaHashes(path.join(work, 'media'))
}
async function buildFixture() {
  const source = `
import { createApp } from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/tokens.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/base.css'))};
import Preview from ${JSON.stringify(path.join(root, 'scripts/fixtures/OnlineP1Preview.vue'))};
createApp(Preview).mount('#app');`
  const result = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    publicDir: false, cacheDir: path.join(work, 'vite-cache'), logLevel: 'error',
    define: { 'import.meta.env.DEV': 'true' }, worker: { format: 'es' },
    resolve: { alias: { vue: path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'),
      'hls.js': path.join(root, 'frontend/node_modules/hls.js/dist/hls.mjs') } },
    plugins: [vue(), { name: 'online-p1-preview', resolveId: id => id === entryId ? '\0' + entryId : null,
      load: id => id === '\0' + entryId ? source : null }],
    build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false,
      rollupOptions: { input: { preview: entryId } } } })
  const entry = result.output.find(item => item.type === 'chunk' && item.isEntry).fileName
  const css = result.output.filter(item => item.type === 'asset' && item.fileName.endsWith('.css')).map(item => item.fileName)
  await writeFile(path.join(dist, 'index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width,initial-scale=1"><title>jzmedia · 在线播放器隔离验证</title>'
    + '<link rel="icon" href="data:,">' + css.map(file => `<link rel="stylesheet" href="/${file}">`).join('')
    + `</head><body><div id="app"></div><script type="module" src="/${entry}"></script></body></html>`)
}
async function startBackend() {
  const port = await freePort()
  base = `http://127.0.0.1:${port}`
  backend = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_p1.server', '--root', work,
    '--port', String(port), '--static-dir', dist],
  { cwd: root, env: { ...process.env, PYTHONUNBUFFERED: '1' }, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  backend.stdout.on('data', data => { backendLog = (backendLog + data.toString()).slice(-16000) })
  backend.stderr.on('data', data => { backendLog = (backendLog + data.toString()).slice(-16000) })
  await until(async () => {
    if (stopped(backend)) throw new Error(`Fixture backend exited: ${backendLog}`)
    return (await fetch(`${base}/api/p1/health`, { signal: AbortSignal.timeout(1000) })).ok
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
  await page.waitForFunction(() => window.__jzOnlineFixture?.snapshot().ready)
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
      label: button.getAttribute('aria-label') || button.textContent.trim(), width: rect.width, height: rect.height } }) }))
  assert.equal(state.overflow, false, `${width}px has no horizontal overflow`)
  if (width <= 700) assert.deepEqual(state.buttons.filter(button => button.width < 43.5 || button.height < 43.5), [],
    'Every visible phone control retains a 44px touch target')
  return state
}
async function matrix() {
  for (const width of [1440, 390]) for (const transport of ['mp4', 'hls']) {
    phase = { name: `matrix-${width}-${transport}`, expectedHttp: false }
    const { context, page } = await newPage(width)
    try {
      const key = `movie-${transport}`
      await layout(page, width)
      const firstFrameMs = await open(page, key)
      const progress = await advancing(page)
      const initial = await snapshot(page)
      assert.equal(initial.transport, transport)
      assert.equal(initial.processing, 'passthrough')
      assert.equal(initial.engine, transport === 'mp4' ? 'mp4' : 'hls')
      const paused = await pause(page)
      await delay(300)
      assert.ok(Math.abs((await readPlayer(page)).time - paused.time) < .2, 'Pause stops the actual clock')
      await page.getByRole('button', { name: '快进 10 秒', exact: true }).click()
      await page.waitForFunction(time => document.querySelector('video')?.currentTime >= time + 9.4, paused.time)
      const seek = await readPlayer(page)
      const speed = page.getByRole('button', { name: /^播放速度 .* 倍$/ })
      await speed.click()
      await page.getByRole('button', { name: /^1\.5\s*×$/ }).click()
      await page.waitForFunction(() => document.querySelector('video')?.playbackRate === 1.5)
      await page.keyboard.press('Escape')
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label')?.startsWith('播放速度'))
      await page.getByRole('button', { name: '音量调节', exact: true }).click()
      await page.getByRole('button', { name: '静音', exact: true }).click()
      await page.waitForFunction(() => document.querySelector('video')?.muted === true)
      await page.keyboard.press('Escape')
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '音量调节')
      await page.getByRole('button', { name: '全屏', exact: true }).click()
      await page.waitForFunction(() => !!document.fullscreenElement)
      await layout(page, width)
      await page.getByRole('button', { name: '退出全屏', exact: true }).click()
      await page.waitForFunction(() => !document.fullscreenElement)
      await page.getByRole('dialog').getByRole('button', { name: '播放', exact: true }).click()
      await advancing(page, seek.time)
      const savedAt = (await pause(page)).time
      await layout(page, width)
      await page.screenshot({ path: path.join(output, `player-${width}-${transport}.png`) })
      const release = await closePlayer(page, { focusKey: key })
      await open(page, key)
      await page.waitForFunction(value => document.querySelector('video')?.currentTime >= value - .7, savedAt)
      const resumed = await readPlayer(page)
      assert.ok(resumed.time >= savedAt - .7, 'Reopening resumes this identity on its original timeline')
      await closePlayer(page, { keyboard: true, focusKey: key })
      await backendChildren(phase.name)
      passed({ case: phase.name, firstFrameMs, progress, initial, paused, seek, savedAt, resumed,
        releaseMs: release.releaseMs, passed: ['decoded frames', 'clock', 'pause', 'absolute seek', 'rate', 'mute',
          'fullscreen', 'menu/focus', 'close release', 'resume', 'Escape', 'layout'] })
    } finally { await closeContext(context) }
  }
  phase = { name: 'matrix-320-short', expectedHttp: false }
  const narrow = await newPage(320)
  try {
    const firstFrameMs = await open(narrow.page, 'movie-mp4')
    const progress = await advancing(narrow.page)
    await pause(narrow.page)
    const geometry = await layout(narrow.page, 320)
    await narrow.page.screenshot({ path: path.join(output, 'player-320-mp4.png') })
    await closePlayer(narrow.page)
    passed({ case: phase.name, firstFrameMs, progress, geometry })
  } finally { await closeContext(narrow.context) }
  phase = { name: 'native-hls-capability', expectedHttp: false }
  const capability = await newPage()
  const native = await capability.page.evaluate(() => !!document.createElement('video').canPlayType('application/vnd.apple.mpegurl'))
  await closeContext(capability.context)
  report.nativeHls.advertised = native
  if (!native) report.nativeHls.reason = 'Installed Chromium does not advertise native HLS; no native success is simulated.'
  else {
    const nativePage = await newPage(1440, { init: () => {
      Object.defineProperty(window, 'MediaSource', { configurable: true, value: undefined })
      Object.defineProperty(window, 'ManagedMediaSource', { configurable: true, value: undefined })
    Object.defineProperty(window, 'WebKitMediaSource', { configurable: true, value: undefined })
    } })
    try {
      await open(nativePage.page, 'movie-hls')
      await advancing(nativePage.page)
      assert.equal((await snapshot(nativePage.page)).engine, 'native-hls')
      report.nativeHls = { advertised: true, verified: true, reason: 'Real native HLS decoded frames with MSE disabled.' }
      await closePlayer(nativePage.page)
    } finally { await closeContext(nativePage.context) }
  }
}
async function lifecycle() {
  phase = { name: 'lifecycle-identities', expectedHttp: false }
  const { context, page } = await newPage()
  try {
    await open(page, 'movie-mp4')
    await advancing(page)
    const old = await snapshot(page)
    await page.evaluate(() => window.__jzOnlineFixture.select('episode-2'))
    await loaded(page)
    const changed = await advancing(page)
    const next = await snapshot(page)
    assert.equal(next.identity.kind, 'episode')
    assert.notEqual(next.sessionId, old.sessionId)
    assert.ok(changed.after.time < 6, 'A different episode starts its own timeline')
    await until(async () => (await metrics()).registry.sessions === 1, 'Identity change releases the prior session')
    await closePlayer(page)
    passed({ case: phase.name, old, next, changed })

    for (const action of ['close', 'replace']) {
      phase = { name: `lifecycle-delayed-${action}`, expectedHttp: false }
      await scenario(page, { delay_ms: 600 })
      await page.locator('[data-online-item="movie-mp4"]').click()
      await until(async () => (await metrics()).pending_resolutions > 0, 'Delayed provider must actually be in flight')
      if (action === 'close') {
        await page.getByRole('button', { name: '关闭播放器', exact: true }).click()
        const release = await idle('Aborted POST must not leave an unreachable session after delayed resolution')
        passed({ case: phase.name, releaseMs: release.releaseMs, metrics: release.metrics.registry })
      } else {
        await page.evaluate(() => window.__jzOnlineFixture.select('episode-2'))
        await loaded(page)
        await until(async () => {
          const value = await metrics()
          return value.pending_resolutions === 0 && value.registry.sessions === 1
        }, 'Only the new identity may own a session after the delayed POST finishes')
        const current = await snapshot(page)
        assert.equal(current.identity.kind, 'episode')
        await closePlayer(page)
        passed({ case: phase.name, current })
      }
    }

    phase = { name: 'lifecycle-revoked-mp4-session-renewal', expectedHttp: true }
    await open(page, 'movie-mp4')
    await advancing(page)
    const beforePause = await pause(page)
    await page.getByRole('button', { name: '快进 10 秒', exact: true }).click()
    await page.waitForFunction(value => document.querySelector('video')?.currentTime >= value + 9.4, beforePause.time)
    const pausedBeforeExpiry = await readPlayer(page)
    const expired = await snapshot(page)
    const metricsBeforeExpiry = await metrics()
    assert.ok(expired.sessionId && pausedBeforeExpiry.paused && pausedBeforeExpiry.frames > 0)
    await scenario(page, { expire_sessions: true })
    await idle('The scenario must really revoke the formal MP4 session before the browser error notification')
    const notification = await page.locator('video').evaluate(video => {
      const previous = Object.getOwnPropertyDescriptor(video, 'error')
      try {
        Object.defineProperty(video, 'error', { configurable: true, get: () => ({ code: 2 }) })
        // A short MP4 may already be completely buffered. Only the browser's
        // notification is simulated; its HEAD diagnosis uses the real route.
        video.dispatchEvent(new Event('error'))
        return { simulatedBrowserNotification: true, code: 2 }
      } finally {
        if (previous) Object.defineProperty(video, 'error', previous)
        else delete video.error
      }
    })
    await page.waitForFunction(sid => {
      const current = window.__jzOnlinePlayer?.snapshot(), video = document.querySelector('video')
      return current?.sessionId && current.sessionId !== sid && !current.booting && video?.readyState >= 2 && video.paused
    }, expired.sessionId)
    const renewed = await snapshot(page)
    const retained = await readPlayer(page)
    assert.equal(retained.paused, true, 'Automatic online renewal preserves the pause state')
    assert.ok(Math.abs(retained.time - pausedBeforeExpiry.time) < .8, 'Automatic online renewal preserves the absolute media position')
    const metricsAfterRenewal = await metrics()
    assert.equal(metricsAfterRenewal.created_sessions.length - metricsBeforeExpiry.created_sessions.length, 1,
      'A revoked MP4 session is rebuilt only once')
    assert.ok(metricsAfterRenewal.api_calls.some(call => call.method === 'HEAD' && call.status === 404
      && call.path === `/api/online/sessions/${expired.sessionId}/media`), 'The real formal HEAD route must report the old session as missing')
    await page.getByRole('dialog').getByRole('button', { name: '播放', exact: true }).click()
    const decodedAfterRenewal = await advancing(page, retained.time)
    await closePlayer(page)
    const closedAfterRenewal = await metrics()
    assert.equal(closedAfterRenewal.created_sessions.length - metricsBeforeExpiry.created_sessions.length, 1,
      'Resumed decoding and close must not trigger another rebuild')
    passed({ case: phase.name, scope: 'Real gateway revocation plus simulated browser network-error notification; not a naturally observed public-source expiration.',
      notification, pausedBeforeExpiry, expired, renewed, retained, decodedAfterRenewal, rebuiltSessions: 1,
      closedRegistry: closedAfterRenewal.registry })

    phase = { name: 'lifecycle-synthetic-pagehide-pageshow', expectedHttp: false }
    await open(page, 'movie-mp4')
    await advancing(page)
    const before = await pause(page)
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })))
    await idle('Synthetic persisted pagehide must stop the current stream')
    const detached = await snapshot(page)
    assert.equal(detached.activeHls, false)
    assert.equal(detached.sessionId, null)
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })))
    await page.waitForFunction(() => {
      const state = window.__jzOnlinePlayer?.snapshot(), video = document.querySelector('video')
      return state?.sessionId && !state.booting && video?.readyState >= 2 && video.paused
    })
    const restored = await readPlayer(page)
    assert.ok(Math.abs(restored.time - before.time) < .8, 'Persisted restore preserves paused absolute position')
    await closePlayer(page)
    passed({ case: phase.name, synthetic: true, before, detached, restored })

    phase = { name: 'lifecycle-identity-change-while-away', expectedHttp: false }
    await open(page, 'movie-mp4')
    await pause(page)
    await page.getByRole('button', { name: '快进 10 秒', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('video')?.currentTime > 9)
    const previousIdentity = await readPlayer(page)
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })))
    await idle('Pagehide must release the old identity before changing the paused page')
    await page.evaluate(() => window.__jzOnlineFixture.select('episode-1', 1.25))
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })))
    await loaded(page)
    const newIdentity = await readPlayer(page)
    assert.ok(newIdentity.time >= .75 && newIdentity.time < 5, 'A new identity uses its own 1.25s initial position')
    assert.ok(Math.abs(newIdentity.time - previousIdentity.time) > 5, 'The prior identity snapshot must not restore into the new item')
    assert.equal(await page.evaluate(() => window.__jzOnlineFixture.snapshot().activeKey), 'episode-1')
    await closePlayer(page)
    passed({ case: phase.name, synthetic: true, previousIdentity, newIdentity })

    phase = { name: 'lifecycle-real-navigation', expectedHttp: false }
    await open(page, 'movie-hls')
    await advancing(page)
    await page.goto('about:blank')
    const navigated = await idle('Actual document navigation must release the online session')
    passed({ case: phase.name, releaseMs: navigated.releaseMs })
  } finally {
    report.lifecycleLastState = await snapshot(page).catch(() => null)
    report.lifecycleLastMedia = await readPlayer(page).catch(() => null)
    await metrics().catch(() => {})
    await closeContext(context)
  }

  phase = { name: 'lifecycle-two-owners', expectedHttp: false }
  const first = await newPage(), second = await newPage()
  try {
    await open(first.page, 'movie-mp4')
    await advancing(first.page)
    const active = await snapshot(first.page)
    phase.expectedHttp = true
    const denied = await second.page.evaluate(async sid => {
      const media = await fetch(`/api/online/sessions/${sid}/media`, { method: 'HEAD' })
      const closed = await fetch(`/api/online/sessions/${sid}`, { method: 'DELETE',
        headers: { 'X-Api-Token': localStorage.getItem('jzmedia.token') || '' } })
      return { read: media.status, close: closed.status }
    }, active.sessionId)
    assert.deepEqual(denied, { read: 404, close: 404 })
    phase.expectedHttp = false
    await advancing(first.page)
    assert.equal((await metrics()).registry.sessions, 1)
    await closePlayer(first.page)
    passed({ case: phase.name, denied, onlyOneVideoDecoded: true })
  } finally { await closeContext(second.context); await closeContext(first.context) }
  await backendChildren('after-lifecycle')
}
async function failures() {
  for (const mode of ['provider_error', 'unsupported_mp4', 'bad_hls', 'missing_media']) {
    phase = { name: `failure-${mode}`, expectedHttp: true }
    const { context, page } = await newPage()
    try {
      const before = await metrics()
      await scenario(page, { next_mode: mode })
      await page.locator('[data-online-item="movie-mp4"]').click()
      await until(async () => await page.getByRole('alert').count() || (await snapshot(page))?.firstFrame,
        'A controlled source failure must settle to a retryable error or bounded online recovery', 45000)
      const failed = await snapshot(page)
      const alert = await page.getByRole('alert').count()
      if (alert) {
        assert.ok(await page.getByRole('button', { name: '重新加载', exact: true }).isVisible())
        await page.getByRole('button', { name: '重新加载', exact: true }).click()
        await loaded(page)
        await advancing(page)
      } else {
        assert.equal(mode, 'missing_media', 'Only a missing media response may recover online automatically')
        await advancing(page)
      }
      const after = await metrics()
      assert.ok(after.created_sessions.length - before.created_sessions.length <= 3,
        'Failure/retry must not create an unbounded number of sessions')
      await page.screenshot({ path: path.join(output, `failure-${mode}-recovered.png`) })
      await closePlayer(page)
      passed({ case: phase.name, settled: alert ? 'explicit error and retry' : 'bounded online recovery',
        failed, retried: true, created: after.created_sessions.length - before.created_sessions.length })
    } finally { await closeContext(context) }
  }
  phase = { name: 'failure-no-hls-capability', expectedHttp: true }
  const unsupported = await newPage(390, { init: () => {
    Object.defineProperty(window, 'MediaSource', { configurable: true, value: undefined })
    Object.defineProperty(window, 'ManagedMediaSource', { configurable: true, value: undefined })
    Object.defineProperty(window, 'WebKitMediaSource', { configurable: true, value: undefined })
    const original = HTMLMediaElement.prototype.canPlayType
    HTMLMediaElement.prototype.canPlayType = function(type) {
      return /mpegurl/i.test(type) ? '' : original.call(this, type)
    }
  } })
  try {
    await unsupported.page.locator('[data-online-item="movie-hls"]').click()
    await unsupported.page.getByRole('alert').waitFor()
    assert.match(await unsupported.page.getByRole('alert').innerText(), /无法播放|其他来源|其他设备/)
    assert.equal((await snapshot(unsupported.page)).activeHls, false)
    await closePlayer(unsupported.page)
    passed({ case: phase.name, simulatedCapabilityAbsence: true, nativeSuccessClaimed: false })
  } finally { await closeContext(unsupported.context) }

  phase = { name: 'failure-gesture-required', expectedHttp: false }
  const gesture = await newPage(390, { init: () => {
    const original = HTMLMediaElement.prototype.play
    window.__jzOnlineAllowPlayback = false
    HTMLMediaElement.prototype.play = function(...args) {
      if (!window.__jzOnlineAllowPlayback) { this.pause(); return Promise.reject(new DOMException('Synthetic autoplay denial', 'NotAllowedError')) }
      return original.apply(this, args)
    }
  } })
  try {
    await gesture.page.locator('[data-online-item="movie-mp4"]').click()
    await gesture.page.getByRole('dialog').getByRole('button', { name: '开始播放', exact: true }).waitFor()
    assert.equal((await readPlayer(gesture.page)).paused, true)
    await gesture.page.evaluate(() => { window.__jzOnlineAllowPlayback = true })
    await gesture.page.getByRole('dialog').getByRole('button', { name: '开始播放', exact: true }).click()
    await advancing(gesture.page)
    await closePlayer(gesture.page)
    passed({ case: phase.name, simulatedNotAllowedError: true, actualGesturePlaybackDecoded: true })
  } finally { await closeContext(gesture.context) }
  await backendChildren('after-failures')
}

try {
  await mkdir(output, { recursive: true })
  for (const file of ['scripts/smoke_online_p1.mjs', 'scripts/fixtures/OnlineP1Preview.vue',
    'scripts/online_p1/server.py', 'scripts/online_p1/fixtures.py', 'frontend/src/components/OnlinePlayerModal.vue',
    'frontend/src/onlinePlayback.js', 'frontend/src/onlinePlayer.css', 'frontend/src/playbackLifecycle.js',
    'app/online/gateway.py', 'app/online/service.py', 'app/routers/online_playback.py']) {
    report.fingerprints[file] = createHash('sha256').update(await readFile(path.join(root, file))).digest('hex')
  }
  // Media generation, frontend build and browser checks run in this order.
  await runGenerator()
  checkStop()
  await buildFixture()
  checkStop()
  await startBackend()
  if (demo) {
    console.log(`在线播放器隔离预览：${base}/`)
    console.log('临时数据库与24秒合成样片；仅回环监听。Ctrl+C关闭并清理。')
    while (!stopping) await delay(200)
  } else {
    browser = await chromium.launch({ headless: true, handleSIGINT: false, handleSIGTERM: false,
      args: ['--no-proxy-server', '--renderer-process-limit=2', '--num-raster-threads=1',
        '--autoplay-policy=no-user-gesture-required'] })
    report.browser = { version: browser.version(), executable: chromium.executablePath() }
    if (selectedCase === 'all' || selectedCase === 'matrix') await matrix()
    if (selectedCase === 'all' || selectedCase === 'lifecycle') await lifecycle()
    if (selectedCase === 'all' || selectedCase === 'failures') await failures()
    report.finalMetrics = (await idle()).metrics
    assert.ok(!report.finalMetrics.origin_calls.some(call => call.app_credentials), 'Application credentials never reach the source')
    assert.deepEqual(report.errors, [], 'No browser runtime, Vue or unexpected console errors')
    assert.deepEqual(report.unexpectedRequests, [], 'No foreign origin or local playback/cast API request')
    assert.deepEqual(report.unexpectedResponses, [], 'Clean scenarios have no unexpected HTTP errors')
    await backendChildren('completed-browser-checks')
    report.mediaHashesAfter = await mediaHashes(path.join(work, 'media'))
    assert.deepEqual(report.mediaHashesAfter, report.mediaHashesBefore, 'Serving and playback never modify generated media')
    console.log(`PASS online P1 ${selectedCase}: ${report.checks.length} scenarios`)
  }
} catch (error) {
  report.failedPhase = phase.name
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
