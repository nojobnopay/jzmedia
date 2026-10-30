// Run under the repository's verified 2 GiB / 2 CPU / no-swap guard:
// node scripts/smoke_online_range.mjs
// Real OnlinePlayerModal + production gateway + a paced loopback origin only.
// Generation finishes before serving; no production DB, Docker, NAS or public source.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { backendChildren, delay, freePort, killOwned as kill, sha256 as sha, stopOwned, stopped,
  waitForFixtureIdle, waitUntil } from './online_fixture/browser.mjs'

if (process.argv.slice(2).includes('--help')) {
  console.log('Usage: node scripts/smoke_online_range.mjs (isolated 90-second MP4, 150 KiB/s origin; use resource guard)')
  process.exit(0)
}
assert.equal(process.argv.length, 2, 'No runtime target or arbitrary media URL is accepted')
const root = fileURLToPath(new URL('../', import.meta.url))
const work = await mkdtemp(path.join(os.tmpdir(), 'jzmedia-online-catalog-range-'))
const output = path.join(root, 'output/playwright/online-range')
const dist = path.join(work, 'dist')
const rate = 150 * 1024
const report = { generatedAt: new Date().toISOString(), synthetic: true,
  scope: 'Real Chromium decoding and natural 1x playback past the video packet at byte 8 MiB. No seek is permitted during that phase. Source aggregate throughput is 150 KiB/s. Separate backward/forward seeks follow. No actual device, public-network or human sound acceptance.',
  fingerprints: {}, checks: [], children: [], responses: [], errors: [], samples: [], cleanup: {} }
let generator = null, backend = null, browser = null, context = null, page = null
let base = '', backendLog = '', stopping = false, failure = null, phase = 'setup'
const mediaResponses = []
function interrupt() {
  stopping = true
  kill(generator)
  kill(backend)
  void browser?.close().catch(() => {})
}
function checkStop() { if (stopping) throw new Error('Range verification interrupted or exceeded 165 seconds') }
process.on('SIGINT', interrupt)
process.on('SIGTERM', interrupt)
const overall = setTimeout(interrupt, 165000)

function until(predicate, message, seconds = 20) {
  return waitUntil(predicate, message, { timeout: seconds * 1000, interval: 100, checkStop })
}
async function get(apiPath) {
  const response = await fetch(base + apiPath, { signal: AbortSignal.timeout(3000) })
  assert.equal(response.status, 200)
  return response.json()
}
async function noBackendChildren(label) {
  const children = await backendChildren(backend)
  report.children.push({ label, children })
  assert.deepEqual(children, [], 'The serving gateway must never launch FFmpeg/ffprobe or another child')
}
async function generate() {
  let log = ''
  generator = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_fixture.catalog', '--root', work, '--generate-range-media'],
    { cwd: root, env: { ...process.env, PYTHONUNBUFFERED: '1' }, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  for (const stream of [generator.stdout, generator.stderr]) stream.on('data', data => { log = (log + data).slice(-10000) })
  const code = await new Promise((resolve, reject) => { generator.once('error', reject); generator.once('exit', resolve) })
  assert.equal(code, 0, `Long fixture generation failed: ${log}`)
  assert.equal(stopped(generator), true)
}
async function buildPlayer() {
  const entryId = 'virtual:online-range-player'
  const source = `
import {createApp,h,onMounted,ref} from ${JSON.stringify(path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/tokens.css'))};
import ${JSON.stringify(path.join(root, 'frontend/src/styles/base.css'))};
import OnlinePlayerModal from ${JSON.stringify(path.join(root, 'frontend/src/components/OnlinePlayerModal.vue'))};
import JzButton from ${JSON.stringify(path.join(root, 'frontend/src/components/JzButton.vue'))};
import {api,setToken} from ${JSON.stringify(path.join(root, 'frontend/src/api.js'))};
import {refreshViewing} from ${JSON.stringify(path.join(root, 'frontend/src/viewing.js'))};
createApp({setup(){
  const ready=ref(false),opened=ref(false),identity=ref(null),error=ref('');
  const info=window.__rangeHarness={ready:false,error:'',closed:false};
  onMounted(async()=>{try{
    const bootstrap=await api('/api/fixture/bootstrap');setToken(bootstrap.api_token);await refreshViewing();
    await api('/api/online/settings',{method:'PATCH',body:JSON.stringify({enabled:true})});
    const source=await api('/api/online/sources',{method:'POST',body:JSON.stringify({name:'隔离长MP4',provider:'maccms',enabled:true,api_base_url:bootstrap.api_base_url,allowed_hosts:bootstrap.allowed_hosts})});
    const detail=await api('/api/online/catalog/detail?source_id='+source.id+'&external_id=movie-1');
    const entry=detail.lines.flatMap(line=>line.entries).find(entry=>entry.transport==='mp4'&&entry.supported);
    if(!entry)throw new Error('Isolated MP4 catalogue entry missing');identity.value=entry.identity;
    ready.value=info.ready=true;
  }catch(problem){error.value=info.error=problem.message;}});
  return()=>h('main',{style:{padding:'24px'}},[
    error.value?h('p',{role:'alert'},error.value):null,
    h(JzButton,{disabled:!ready.value,onClick:()=>{opened.value=true;}},()=> '播放跨范围样片'),
    opened.value?h(OnlinePlayerModal,{onlineIdentity:identity.value,title:'跨范围合成 MP4',onClose:()=>{opened.value=false;info.closed=true;}}):null
  ]);
}}).mount('#app');`
  const built = await build({ root: path.join(root, 'frontend'), configFile: false, envFile: false,
    publicDir: path.join(root, 'frontend/public'), cacheDir: path.join(work, 'vite-cache'), logLevel: 'error',
    define: { 'import.meta.env.DEV': 'true' }, worker: { format: 'es' },
    resolve: { alias: { vue: path.join(root, 'frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js'),
      'hls.js': path.join(root, 'frontend/node_modules/hls.js/dist/hls.mjs') } },
    plugins: [vue(), { name: 'online-range', resolveId: id => id === entryId ? '\0' + entryId : null,
      load: id => id === '\0' + entryId ? source : null }],
    build: { outDir: dist, emptyOutDir: true, reportCompressedSize: false, rollupOptions: { input: { range: entryId } } } })
  const entry = built.output.find(item => item.type === 'chunk' && item.isEntry).fileName
  const css = built.output.filter(item => item.type === 'asset' && item.fileName.endsWith('.css')).map(item => item.fileName)
  await writeFile(path.join(dist, 'index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<link rel="icon" href="data:,">' + css.map(file => `<link rel="stylesheet" href="/${file}">`).join('')
    + `</head><body><div id="app"></div><script type="module" src="/${entry}"></script></body></html>`)
}
async function serve() {
  const port = await freePort()
  base = `http://127.0.0.1:${port}`
  backend = spawn(path.join(root, '.venv/bin/python'), ['-m', 'scripts.online_fixture.catalog', '--root', work,
    '--port', String(port), '--static-dir', dist, '--throttle-bytes-per-second', String(rate)],
  { cwd: root, env: { ...process.env, PYTHONUNBUFFERED: '1' }, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  for (const stream of [backend.stdout, backend.stderr]) stream.on('data', data => { backendLog = (backendLog + data).slice(-20000) })
  await until(async () => !stopped(backend) && (await get('/api/fixture/health')).ok, 'Paced isolated server must become ready')
  await noBackendChildren('ready-after-generator-exit')
}
async function sample() {
  return page.locator('video').evaluate(video => ({ wall: performance.now(), time: video.currentTime, paused: video.paused,
    readyState: video.readyState, error: video.error?.code || null, rate: video.playbackRate, ended: video.ended,
    frames: video.getVideoPlaybackQuality?.().totalVideoFrames || 0,
    presentedFrames: window.__jzOnlinePlayer?.snapshot().presentedFrames || 0,
    buffered: Array.from({ length: video.buffered.length }, (_, index) => [video.buffered.start(index), video.buffered.end(index)]) }))
}
async function observeNatural(target, first) {
  const deadline = Date.now() + 110000
  while (Date.now() < deadline) {
    checkStop()
    const value = await sample()
    report.samples.push(value)
    assert.equal(value.error, null)
    assert.equal(value.rate, 1, 'Natural playback uses the actual 1x media clock')
    const observation = await page.evaluate(() => window.__rangeObservation)
    const disruptive = observation.events.filter(event => event.phase === 'natural' && event.afterPlaying
      && ['waiting', 'stalled', 'error', 'seeking'].includes(event.type) && event.time > 1)
    assert.deepEqual(disruptive, [], 'Natural playback must neither rebuffer/stall nor seek across bounded HTTP ranges')
    // Decoder and real video-frame callback observations have independent
    // scheduling. Crossing the byte-boundary timestamp must not end the sample
    // before both existing frame requirements have actually been observed.
    if (value.time >= target && value.frames > first.frames + 1500
      && value.presentedFrames > first.presentedFrames + 1500) return value
    if (value.ended) throw new Error('Natural playback ended before crossing the byte boundary with the required 1500 decoded and presented frame increments')
    if (report.samples.length === 35 || report.samples.length === 65) await noBackendChildren(`natural-${Math.floor(value.time)}s`)
    await delay(1000)
  }
  throw new Error('Natural 1x playback did not cross the old byte boundary with the required decoded and presented frames within 110 seconds')
}
function validateOriginBytes(value, original) {
  const calls = value.origin_calls.filter(call => call.kind === 'media' && call.method === 'GET' && call.path === '/files/ep-1/movie.mp4')
  const complete = calls.filter(call => call.complete)
  assert.ok(complete.length >= 9, 'Large MP4 must complete multiple bounded requests past eight windows')
  let covered = 0
  for (const call of complete.sort((a, b) => a.start - b.start)) {
    assert.equal(call.bytes_sent, call.end - call.start + 1)
    assert.equal(call.sha256, sha(original.subarray(call.start, call.end + 1)), 'Origin transmitted exact immutable bytes')
    assert.ok(call.bytes_sent <= 1024 * 1024)
    if (call.start <= covered) covered = Math.max(covered, call.end + 1)
  }
  assert.ok(covered > 8 * 1024**2, 'Natural requests provide contiguous media bytes through the old 8 MiB boundary')
  assert.ok(complete.some(call => call.start >= 8 * 1024**2), 'Browser itself requests the later file windows')
  return { completeRequests: complete.length, contiguousBytes: covered, requests: calls }
}

try {
  await mkdir(output, { recursive: true })
  for (const file of ['scripts/smoke_online_range.mjs', 'scripts/online_fixture/catalog.py', 'scripts/online_fixture/catalog_origin.py',
    'scripts/online_fixture/browser.mjs', 'scripts/online_fixture/common.py', 'scripts/online_fixture/media.py',
    'scripts/online_fixture/player_origin.py',
    'frontend/src/components/OnlinePlayerModal.vue', 'frontend/src/onlinePlayback.js', 'app/online/gateway.py']) {
    report.fingerprints[file] = sha(await readFile(path.join(root, file)))
  }
  await generate()
  checkStop()
  const original = await readFile(path.join(work, 'media/ep-1/movie.mp4'))
  report.mediaSha256Before = sha(original)
  await buildPlayer()
  await serve()
  const info = report.fixture = await get('/api/fixture/range-info')
  assert.equal(info.throttle_bytes_per_second, rate)
  assert.ok(info.bytes > 8 * 1024**2 && info.duration_seconds === 90 && info.average_bits_per_second <= 1000000)
  assert.ok(info.natural_target_seconds > info.boundary_video_time_seconds)
  assert.equal(report.mediaSha256Before, info.sha256)
  browser = await chromium.launch({ headless: true, handleSIGINT: false, handleSIGTERM: false,
    args: ['--no-proxy-server', '--renderer-process-limit=2', '--num-raster-threads=1', '--autoplay-policy=no-user-gesture-required'] })
  report.browserVersion = browser.version()
  context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: 'block' })
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (!['data:', 'blob:'].includes(url.protocol) && url.origin !== base) {
      report.errors.push({ type: 'foreign-request', url: url.origin }); return route.abort()
    }
    if (/^\/api\/(stream|cast)(?:\/|$)/.test(url.pathname)) {
      report.errors.push({ type: 'local-playback-api', path: url.pathname }); return route.abort()
    }
    return route.continue()
  })
  await context.addInitScript(() => {
    const seen = new WeakSet(), observation = window.__rangeObservation = { phase: 'natural', events: [] }
    const bind = video => {
      if (seen.has(video)) return
      seen.add(video); video.muted = true
      let played = false
      for (const type of ['playing', 'waiting', 'stalled', 'error', 'seeking', 'seeked', 'ended']) {
        video.addEventListener(type, () => {
          observation.events.push({ type, phase: observation.phase, time: video.currentTime,
            afterPlaying: played, wall: performance.now(), readyState: video.readyState })
          if (type === 'playing') played = true
        })
      }
    }
    new MutationObserver(() => document.querySelectorAll('video').forEach(bind)).observe(document, { childList: true, subtree: true })
  })
  page = await context.newPage()
  page.setDefaultTimeout(20000)
  page.on('pageerror', error => report.errors.push({ phase, type: 'pageerror', message: String(error) }))
  page.on('console', message => { if (message.type() === 'error') report.errors.push({ phase, type: 'console', message: message.text() }) })
  page.on('response', response => {
    const url = new URL(response.url())
    if (response.status() >= 400) report.errors.push({ phase, type: 'http', status: response.status(), path: url.pathname })
    if (/^\/api\/online\/sessions\/[^/]+\/media$/.test(url.pathname)) {
      const record = { phase, status: response.status(), range: response.request().headers().range || null,
        contentRange: response.headers()['content-range'], contentLength: response.headers()['content-length'] }
      report.responses.push(record)
      mediaResponses.push(response.finished().then(error => { record.finished = !error; record.failure = error ? String(error) : null }))
    }
  })
  await page.goto(base)
  await page.waitForFunction(() => window.__rangeHarness?.ready || window.__rangeHarness?.error)
  assert.equal(await page.evaluate(() => window.__rangeHarness.error), '')
  await page.getByRole('button', { name: '播放跨范围样片', exact: true }).click()
  await page.waitForFunction(() => window.__jzOnlinePlayer?.snapshot().firstFrame && document.querySelector('video')?.videoWidth > 0)
  phase = 'natural'
  const first = report.firstFrame = await sample()
  console.log('Range fixture decoded first frame; checking natural 1x playback through byte 8 MiB')
  const end = report.naturalEnd = await observeNatural(info.natural_target_seconds, first)
  assert.ok(end.frames > first.frames + 1500, 'Actual decoded frames advance through the long natural phase')
  assert.ok(end.presentedFrames > first.presentedFrames + 1500, 'Real frame callbacks advance through the natural phase')
  report.naturalMetrics = await get('/api/fixture/metrics')
  report.naturalByteContinuity = validateOriginBytes(report.naturalMetrics, original)
  report.observationBeforeSeeks = await page.evaluate(() => window.__rangeObservation)
  report.checks.push('natural playback crosses encoded packet beyond 8 MiB without rebuffer, stall, error or seek')
  await noBackendChildren('after-natural-boundary')
  phase = 'explicit-seeks'
  await page.evaluate(() => { window.__rangeObservation.phase = 'explicit-seeks' })
  for (const target of [8, 85]) {
    const before = await sample()
    await page.locator('video').evaluate((video, target) => { video.currentTime = target }, target)
    await page.waitForFunction(target => {
      const video = document.querySelector('video')
      return video && !video.seeking && !video.paused && video.currentTime > target + .4 && !video.error
    }, target)
    const after = await sample()
    assert.ok(after.frames > before.frames)
    report.checks.push(`separate explicit seek to ${target}s resumes real frame and clock progress`)
  }
  await page.screenshot({ path: path.join(output, 'completed.png') })
  phase = 'close'
  await page.getByRole('button', { name: '关闭播放器', exact: true }).click()
  await page.waitForFunction(() => window.__rangeHarness.closed && !document.querySelector('video'))
  report.finalMetrics = (await waitForFixtureIdle(() => get('/api/fixture/metrics'), 'catalog', {
    message: 'Close must release sessions, origin reads and all reservations', checkStop, timeout: 20000 })).metrics
  assert.deepEqual(report.finalMetrics.local_rows, { movies: 0, tv_episodes: 0, playback_progress: 0 })
  assert.ok(!report.finalMetrics.origin_calls.some(call => call.app_credentials))
  await noBackendChildren('after-close')
  report.mediaSha256After = sha(await readFile(path.join(work, 'media/ep-1/movie.mp4')))
  assert.equal(report.mediaSha256After, report.mediaSha256Before)
  await Promise.all(mediaResponses)
  assert.deepEqual(report.errors, [], 'No runtime errors, unexpected HTTP or local/foreign playback path')
  console.log('PASS real browser bounded MP4: natural playback beyond 8 MiB, separate seeks, cleanup and immutable bytes')
} catch (error) {
  failure = error
  report.failedPhase = phase
  report.lastMedia = await sample().catch(() => null)
  report.observation = page ? await page.evaluate(() => window.__rangeObservation).catch(() => null) : null
  report.lastMetrics = base ? await get('/api/fixture/metrics').catch(() => null) : null
  if (page) await page.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {})
} finally {
  clearTimeout(overall)
  try { await context?.close() } catch { /* Continue cancellation cleanup. */ }
  try { await browser?.close() } catch { /* Continue cancellation cleanup. */ }
  report.cleanup.browserClosed = true
  report.cleanup.generatorStopped = await stopOwned(generator, { termMs: 2000 })
  report.cleanup.backendStopped = await stopOwned(backend, { termMs: 2000 })
  await rm(work, { recursive: true, force: true })
  report.cleanup.temporaryRootRemoved = true
  report.backendLog = backendLog
  if (failure) report.failure = { message: String(failure), stack: failure.stack || '' }
  await mkdir(output, { recursive: true })
  await writeFile(path.join(output, failure ? 'failure.json' : 'manifest.json'), JSON.stringify(report, null, 2) + '\n')
  process.removeListener('SIGINT', interrupt)
  process.removeListener('SIGTERM', interrupt)
}
if (failure) throw failure
