// Fixed public HLS diagnostic. Decode/transmux on this browser; no NAS processing.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { appendFile, mkdir, readFile, writeFile } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { assessRelaySamples, canAllowStartupGap } from './online_diagnostics/player_policy.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const args = process.argv.slice(2)
if (args.length === 1 && args[0] === '--help') {
  console.log(`Usage: node scripts/probe_online_hls.mjs
  [--url https://test-streams.mux.dev/test_001/stream.m3u8]
  [--duration 30] [--output output/playwright/online-hls]
  [--max-bytes 268435456] [--max-requests 1200]
  [--relay-url http://127.0.0.1:PORT/TOKEN/media/ID]
  [--metrics-url http://127.0.0.1:PORT/TOKEN/metrics] [--allow-tailscale-relay true]
  [--browser-ws-env ONLINE_HLS_BROWSER_WS] [--shared-relay true]

Allowed sources: HTTPS test-streams.mux.dev, or the fixed Apple bipbop HEVC
or bipbop_4x3 example directory on devstreaming-cdn.apple.com. Child resources stay within
the selected source's allowed origin/path. The lowest compatible AVC/AAC
variant is selected and held fixed; explicitly HEVC/AC3 variants are rejected.
Duration is 30..1800 actual playback seconds at normal speed, without looping.
Startup has a 90-second limit. Seek runs separately after successful playback.
HLS load policies permit two retries, with a separate six-network-error total
budget and no alternative rendition fallback. HTTP 429 uses the same bounded
backoff. Subtitle loading is disabled for this video/audio diagnostic. No proxy switching,
manual restart, fatal media recovery or server transcoding is performed.
Only one nonfatal initial bufferSeekOverHole to at most 0.5s is allowed before
the first presented frame; it is reported as recovered, and skipped time is
excluded from the played ranges. Later or repeated media errors still fail.
Media is streamed by Chromium, never route.fulfill() buffered or explicitly saved.
Byte accounting uses CDP response data events and stops on a breached budget;
small in-flight overshoot is possible. A 30-second pass is not a 30-minute pass.
--url always declares the official source. --relay-url explicitly selects a
temporary token relay instead of client-direct loading, without fallback.
Relay children stay under the same /TOKEN/media/ prefix. Optional metrics are
sampled separately and checked for release within 20 seconds after closing,
including signal cancellation. --shared-relay true defers global release to
the orchestrator after all clients close; individual clients do not own the
other clients' connections. Metric observation gaps are never clean passes.
--browser-ws-env reads a loopback Playwright BrowserServer endpoint only from
that environment variable. The external launcher must clear proxy variables,
use --no-proxy-server and the three disable-background flags, and keep the
existing process limits. This script creates/closes its own isolated context,
then disconnects; it does not terminate the shared BrowserServer.
Use the repository's transient resource limits when running the browser.`)
  process.exit(0)
}
const values = new Map()
for (let index = 0; index < args.length; index += 2) {
  assert.ok(['--url', '--duration', '--output', '--max-bytes', '--max-requests',
    '--relay-url', '--metrics-url', '--allow-tailscale-relay', '--browser-ws-env', '--shared-relay'].includes(args[index])
    && args[index + 1], 'Arguments must be supported --name value pairs; see --help')
  assert.ok(!values.has(args[index]), `Duplicate ${args[index]}`)
  values.set(args[index], args[index + 1])
}
function integer(name, fallback, minimum, maximum) {
  const value = Number(values.get(name) ?? fallback)
  assert.ok(Number.isSafeInteger(value) && value >= minimum && value <= maximum, `Invalid ${name}`)
  return value
}
const source = new URL(values.get('--url') ?? 'https://test-streams.mux.dev/test_001/stream.m3u8')
const applePrefixes = ['/videos/streaming/examples/bipbop_adv_example_hevc/',
  '/videos/streaming/examples/bipbop_4x3/']
const allowedSource = target => !target.username && !target.password && (
  target.origin === 'https://test-streams.mux.dev'
  || (target.origin === 'https://devstreaming-cdn.apple.com' && applePrefixes.some(prefix => target.pathname.startsWith(prefix))))
assert.ok(allowedSource(source) && source.pathname.endsWith('.m3u8') && !source.hash,
  'Only fixed official Mux/Apple HTTPS HLS examples are allowed')
const relay = values.has('--relay-url') ? new URL(values.get('--relay-url')) : null
const metricsURL = values.has('--metrics-url') ? new URL(values.get('--metrics-url')) : null
const diagnosticHost = target => {
  if (target.protocol !== 'http:' || target.username || target.password || target.search || target.hash) return false
  if (['127.0.0.1', '[::1]'].includes(target.hostname)) return true
  const octets = target.hostname.split('.').map(Number)
  return values.get('--allow-tailscale-relay') === 'true' && octets.length === 4
    && octets.every(part => Number.isInteger(part) && part >= 0 && part <= 255)
    && octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127
}
let relayPrefix = null, relayToken = null
if (relay) {
  const match = /^\/([A-Za-z0-9_-]{32,128})\/media\/[A-Za-z0-9_-]+$/.exec(relay.pathname)
  assert.ok(diagnosticHost(relay) && match, 'Relay must use an isolated loopback/Tailscale token media URL')
  relayToken = match[1]
  relayPrefix = `/${relayToken}/media/`
}
if (metricsURL) assert.ok(relay && diagnosticHost(metricsURL) && metricsURL.origin === relay.origin
  && metricsURL.pathname === `/${relayToken}/metrics`, 'Metrics must share the relay origin and token')
const sharedRelay = values.get('--shared-relay') === 'true'
assert.ok(!values.has('--shared-relay') || values.get('--shared-relay') === 'true'
  && metricsURL, '--shared-relay true requires a metrics URL')
const deliveryURL = relay || source
const allowedMedia = target => relay
  ? target.origin === relay.origin && diagnosticHost(target) && target.pathname.startsWith(relayPrefix)
  : target.origin === source.origin && allowedSource(target)
const browserWsEnv = values.get('--browser-ws-env')
let browserWsEndpoint = null
if (browserWsEnv) {
  assert.ok(/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(browserWsEnv), 'Invalid browser endpoint environment variable name')
  try { browserWsEndpoint = new URL(process.env[browserWsEnv]) }
  catch { throw new Error('Browser endpoint environment variable is missing or invalid') }
  assert.ok(browserWsEndpoint.protocol === 'ws:' && ['127.0.0.1', '[::1]'].includes(browserWsEndpoint.hostname)
    && browserWsEndpoint.port && !browserWsEndpoint.username && !browserWsEndpoint.password && !browserWsEndpoint.hash,
  'Shared browser endpoint must be an explicit loopback ws URL without credentials')
}
const duration = integer('--duration', 30, 30, 1800)
const maxBytes = integer('--max-bytes', 256 * 1024 ** 2, 1024 ** 2, 1024 ** 3)
const maxRequests = integer('--max-requests', 1200, 10, 10000)
const output = path.resolve(root, values.get('--output') ?? 'output/playwright/online-hls')
assert.ok(output.startsWith(path.join(root, 'output/playwright/')), 'Evidence must be under output/playwright/')
await mkdir(output, { recursive: true })
const journal = path.join(output, 'samples.jsonl')
await writeFile(journal, '')
const hash = value => createHash('sha256').update(value).digest('hex')
const hlsSource = await readFile(path.join(root, 'frontend/node_modules/hls.js/dist/hls.min.js'), 'utf8')
const network = { requests: 0, responses: {}, requestFailures: {}, unexpectedRequests: 0,
  receivedBodyBytes: 0, receivedEncodedBytes: 0, finishedEncodedBytes: 0, budgetExceeded: null }
const report = { startedAt: new Date().toISOString(), status: 'running',
  scope: 'Public HLS decoded/transmuxed in development Chromium. Muted; no manual sound, TV, mobile or hardware decode claim. Optional relay metrics are separate observations, not a general NAS capacity claim.',
  configuration: { sourceOrigin: source.origin, sourceSha256: hash(source.href), durationSeconds: duration,
    delivery: relay ? 'explicit_nas_relay' : 'client_direct', relaySha256: relay ? hash(relay.href) : null,
    metricsConfigured: Boolean(metricsURL), sharedRelay,
    startupRecovery: { allowed: 'one nonfatal pre-first-frame bufferSeekOverHole', maxSeekToSeconds: 0.5,
      maxRecoveries: 1, nudgeMaxRetry: 1 },
    browserOwnership: browserWsEndpoint ? 'shared_connection_own_context_only' : 'owned_browser',
    browserWsEnv: browserWsEnv || null, browserWsSha256: browserWsEndpoint ? hash(browserWsEndpoint.href) : null,
    maxBytes, maxRequests, proxy: browserWsEndpoint
      ? 'external launcher owns environment/resource limits; browser command-line flags verified; transparent routing uninspected'
      : 'environment cleared; --no-proxy-server; transparent network routing uninspected',
    variant: 'lowest compatible declared AVC/AAC bitrate, fixed; unlabelled media playlists require actual decode validation', rate: 1, loop: false,
    buffer: { maxBufferLength: 20, maxMaxBufferLength: 30, maxBufferSize: 30 * 1024 ** 2 },
    retry: { policyErrorRetries: 2, policyTimeoutRetries: 2, totalNetworkErrorBudget: 6,
      retryDelayMs: 500, maxRetryDelayMs: 1000, retry429: true, alternativeVariantsRetained: 0 },
    subtitles: 'disabled for this video/audio diagnostic',
    acceptance: 'At least target continuous played-range seconds and real wall seconds from the first presented frame; wall freezes at natural end. Numeric tolerance 0.001s, no loop/acceleration/artificial seek. Frames advance, no fatal/rejected media error; one bounded initial alignment is labelled recovered. Max no-progress <=30s, waiting <=max(2s,1% target). Seek is separate; completed VOD can be sought backwards.' },
  scriptSha256: hash(await readFile(fileURLToPath(import.meta.url))), hlsSha256: hash(hlsSource),
  policySha256: hash(await readFile(new URL('./online_diagnostics/player_policy.mjs', import.meta.url))), network,
  startup: null, playback: null, seek: null, cleanup: null, nas: null, pageErrors: 0, consoleErrors: 0 }
const nasSamples = []
let browser, context, page, cdp, contextClosed = false, stopped = false, stopReason = null, timers = new Set()
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
  stopped = true; stopReason = signal
  for (const finish of timers) finish()
})
const delay = ms => new Promise(resolve => {
  const finish = () => { clearTimeout(timer); timers.delete(finish); resolve() }
  const timer = setTimeout(finish, ms); timers.add(finish)
})
const trackedRequests = new Set()
async function readMetrics(phase, timeoutMs = 3000) {
  if (!metricsURL) return null
  const value = await new Promise(resolve => {
    let done = false, deadline
    const finish = data => { if (!done) { done = true; clearTimeout(deadline); resolve(data) } }
    // Standard node:http connects directly; it does not inherit an outbound proxy.
    const request = http.get(metricsURL, { timeout: timeoutMs }, response => {
      let bytes = 0
      const chunks = []
      if (response.statusCode !== 200) {
        response.destroy(); finish({ fetchError: `HTTP_${response.statusCode}` }); return
      }
      response.on('data', chunk => {
        bytes += chunk.length
        if (bytes > 65536) { response.destroy(); finish({ fetchError: 'metrics_too_large' }); return }
        chunks.push(chunk)
      })
      response.on('end', () => {
        try { finish(JSON.parse(Buffer.concat(chunks).toString('utf8'))) }
        catch { finish({ fetchError: 'invalid_metrics_json' }) }
      })
      response.on('error', error => finish({ fetchError: error.name }))
    })
    request.on('timeout', () => { request.destroy(); finish({ fetchError: 'TimeoutError' }) })
    request.on('error', error => finish({ fetchError: error.name }))
    deadline = setTimeout(() => { request.destroy(); finish({ fetchError: 'TimeoutError' }) }, timeoutMs)
  })
  nasSamples.push({ phase, at: new Date().toISOString(), value })
  return value
}
function assessNas() {
  return metricsURL ? assessRelaySamples(nasSamples, { sharedRelay }) : { observed: false, passed: null, classification: 'not_configured' }
}
function checkBudget() {
  if (network.budgetExceeded) return
  if (network.requests > maxRequests) network.budgetExceeded = 'request_budget'
  if (Math.max(network.receivedBodyBytes, network.receivedEncodedBytes, network.finishedEncodedBytes) > maxBytes) {
    network.budgetExceeded = 'byte_budget'
  }
  if (network.budgetExceeded) void cdp?.send('Page.stopLoading').catch(() => {})
}
function hasHttpError() {
  return Object.keys(network.responses).some(status => Number(status) >= 400)
}
async function snapshot() {
  return page.evaluate(() => {
    const v = document.querySelector('video'), state = window.__jzHlsProbe
    const quality = v.getVideoPlaybackQuality?.()
    return { time: v.currentTime, duration: Number.isFinite(v.duration) ? v.duration : null,
      width: v.videoWidth, height: v.videoHeight, readyState: v.readyState, paused: v.paused, ended: v.ended,
      rate: v.playbackRate, mediaError: v.error?.code || null, playRejected: state.playRejected,
      decodedFrames: v.webkitDecodedFrameCount || quality?.totalVideoFrames || 0,
      presentedFrames: state.presentedFrames, droppedFrames: quality?.droppedVideoFrames || 0,
      playedSeconds: Array.from({ length: v.played.length }, (_, i) => v.played.end(i) - v.played.start(i)).reduce((a, b) => a + b, 0),
      buffered: Array.from({ length: v.buffered.length }, (_, i) => [v.buffered.start(i), v.buffered.end(i)]),
      firstFrameMs: state.firstFrameMs, firstFrameMediaTime: state.firstFrameMediaTime,
      presentedWallSeconds: state.firstFrameAt === null ? 0 : ((state.endedAt ?? performance.now()) - state.firstFrameAt) / 1000,
      waitingSeconds: (state.waitingMs + (state.waitingAt === null ? 0 : performance.now() - state.waitingAt)) / 1000,
      waitingAtFirstFrame: state.waitingAtFirstFrame,
      maxNoProgressSeconds: state.maxNoProgressSeconds, events: { ...state.events },
      hlsEvents: { ...state.hlsEvents }, errors: [...state.errors], fatal: state.fatal,
      selectedLevel: state.selectedLevel, levels: state.levels, live: state.live,
      actualLevelEvents: [...state.actualLevelEvents], fragmentTypes: { ...state.fragmentTypes },
      networkErrorCount: state.networkErrorCount, hlsMediaErrorCount: state.hlsMediaErrorCount,
      rejectedMediaErrorCount: state.rejectedMediaErrorCount, startupRecoveries: [...state.startupRecoveries],
      explicitStartLoadCalls: state.explicitStartLoadCalls,
      hlsVersion: Hls.version, stopped: state.stopped }
  })
}
async function sample(phase) {
  const state = await snapshot()
  const nas = await readMetrics(phase)
  await appendFile(journal, `${JSON.stringify({ at: new Date().toISOString(), phase, state, network, nas })}\n`)
  console.log(JSON.stringify({ phase, time: state.time, playedSeconds: state.playedSeconds,
    frames: state.decodedFrames, presented: state.presentedFrames, fatal: state.fatal,
    bytes: network.receivedBodyBytes, requests: network.requests }))
  return state
}

try {
  if (metricsURL) await appendFile(journal, `${JSON.stringify({ at: new Date().toISOString(), phase: 'baseline', nas: await readMetrics('baseline') })}\n`)
  const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(http|https|all|no)_proxy$/i.test(key)))
  environment.NO_PROXY = '*'
  const requiredFlags = ['--no-proxy-server', '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows']
  browser = browserWsEndpoint
    ? await chromium.connect(browserWsEndpoint.href, { timeout: 15000 })
    : await chromium.launch({ headless: true, env: environment, args: requiredFlags })
  report.browserVersion = browser.version()
  context = await browser.newContext({ viewport: { width: 800, height: 600 }, serviceWorkers: 'block' })
  page = await context.newPage()
  cdp = await context.newCDPSession(page)
  if (browserWsEndpoint) {
    const commandLine = await cdp.send('Browser.getBrowserCommandLine')
    report.browserLaunchAudit = Object.fromEntries(requiredFlags.map(flag => [flag, commandLine.arguments.includes(flag)]))
    assert.ok(Object.values(report.browserLaunchAudit).every(Boolean), 'Shared browser is missing required direct/background flags')
  }
  await cdp.send('Network.enable')
  cdp.on('Network.requestWillBeSent', event => {
    if (allowedMedia(new URL(event.request.url))) trackedRequests.add(event.requestId)
  })
  cdp.on('Network.dataReceived', event => {
    if (!trackedRequests.has(event.requestId)) return
    network.receivedBodyBytes += event.dataLength
    network.receivedEncodedBytes += event.encodedDataLength
    checkBudget()
  })
  cdp.on('Network.loadingFinished', event => {
    if (!trackedRequests.delete(event.requestId)) return
    network.finishedEncodedBytes += event.encodedDataLength
    checkBudget()
  })
  cdp.on('Network.loadingFailed', event => { trackedRequests.delete(event.requestId) })
  await context.route('**/*', route => {
    const target = new URL(route.request().url())
    if (target.protocol === 'blob:') return route.continue()
    if (!allowedMedia(target)) {
      network.unexpectedRequests += 1; return route.abort()
    }
    network.requests += 1; checkBudget()
    return network.budgetExceeded || stopped ? route.abort() : route.continue()
  })
  page.on('response', response => { network.responses[response.status()] = (network.responses[response.status()] || 0) + 1 })
  page.on('requestfailed', request => {
    const reason = request.failure()?.errorText || 'unknown'
    network.requestFailures[reason] = (network.requestFailures[reason] || 0) + 1
  })
  page.on('pageerror', () => { report.pageErrors += 1 })
  page.on('console', event => { if (event.type() === 'error') report.consoleErrors += 1 })
  await page.setContent('<!doctype html><html><meta charset="utf-8"><title>jzmedia public HLS diagnostic</title><video controls muted playsinline width="640" height="360"></video></html>')
  await page.addScriptTag({ content: hlsSource })
  await page.addScriptTag({ content: `window.__jzHlsCanAllowStartupGap = ${canAllowStartupGap.toString()}` })
  await page.evaluate(url => {
    const video = document.querySelector('video')
    const state = window.__jzHlsProbe = { initialAt: performance.now(), firstFrameMs: null, firstFrameAt: null,
      firstFrameMediaTime: null, endedAt: null, waitingAtFirstFrame: null, presentedFrames: 0,
      events: {}, hlsEvents: {}, errors: [], fatal: null, playRejected: null, waitingAt: null, waitingMs: 0,
      levels: [], selectedLevel: null, live: null, maxNoProgressSeconds: 0, monitorStarted: false,
      selectionLocked: false, actualLevelEvents: [], fragmentTypes: {}, networkErrorCount: 0,
      hlsMediaErrorCount: 0, rejectedMediaErrorCount: 0, startupRecoveries: [], explicitStartLoadCalls: 0,
      previousTime: 0, lastProgressAt: performance.now(), stopped: false }
    for (const event of ['loadstart', 'loadedmetadata', 'playing', 'waiting', 'stalled', 'pause', 'ended', 'error', 'seeking', 'seeked', 'ratechange']) {
      video.addEventListener(event, () => {
        state.events[event] = (state.events[event] || 0) + 1
        if (event === 'waiting' && state.waitingAt === null) state.waitingAt = performance.now()
        if (event === 'playing' && state.waitingAt !== null) {
          state.waitingMs += performance.now() - state.waitingAt; state.waitingAt = null
        }
        if (event === 'ended') state.endedAt = performance.now()
      })
    }
    const frame = (_now, metadata) => {
      state.presentedFrames += 1
      if (state.firstFrameAt === null) {
        state.firstFrameAt = performance.now()
        state.firstFrameMs = state.firstFrameAt - state.initialAt
        state.firstFrameMediaTime = metadata.mediaTime
        state.waitingAtFirstFrame = (state.waitingMs + (state.waitingAt === null ? 0 : state.firstFrameAt - state.waitingAt)) / 1000
        state.monitorStarted = true; state.previousTime = video.currentTime
        state.lastProgressAt = state.firstFrameAt; state.maxNoProgressSeconds = 0
      }
      if (!state.stopped) video.requestVideoFrameCallback(frame)
    }
    if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(frame)
    state.monitor = setInterval(() => {
      if (!state.monitorStarted) return
      const now = performance.now()
      state.maxNoProgressSeconds = Math.max(state.maxNoProgressSeconds, (now - state.lastProgressAt) / 1000)
      if (video.currentTime > state.previousTime + 0.05) state.lastProgressAt = now
      state.previousTime = video.currentTime
    }, 250)
    if (!Hls.isSupported()) { state.fatal = 'hls_js_not_supported'; return }
    const retry = () => ({ maxNumRetry: 2, retryDelayMs: 500, maxRetryDelayMs: 1000,
      shouldRetry: (_config, retryCount, _isTimeout, response, defaultRetry) =>
        retryCount < 2 && (defaultRetry || response?.code === 429) })
    const policy = () => ({ default: { maxTimeToFirstByteMs: 10000, maxLoadTimeMs: 30000,
      timeoutRetry: retry(), errorRetry: retry() } })
    const hls = window.__jzHlsInstance = new Hls({ autoStartLoad: false, testBandwidth: false, maxBufferLength: 20,
      maxMaxBufferLength: 30, maxBufferSize: 30 * 1024 ** 2, backBufferLength: 10,
      nudgeMaxRetry: 1,
      preserveManualLevelOnError: true, manifestLoadPolicy: policy(), playlistLoadPolicy: policy(),
      fragLoadPolicy: policy(), keyLoadPolicy: policy() })
    for (const event of [Hls.Events.MANIFEST_LOADING, Hls.Events.MANIFEST_LOADED, Hls.Events.LEVEL_LOADING,
      Hls.Events.LEVEL_LOADED, Hls.Events.FRAG_LOADING, Hls.Events.FRAG_LOADED, Hls.Events.FRAG_BUFFERED,
      Hls.Events.LEVEL_SWITCHED, Hls.Events.AUDIO_TRACK_LOADING, Hls.Events.AUDIO_TRACK_LOADED,
      Hls.Events.SUBTITLE_TRACK_LOADING]) hls.on(event, () => { state.hlsEvents[event] = (state.hlsEvents[event] || 0) + 1 })
    hls.on(Hls.Events.FRAG_LOADING, (_event, data) => {
      const type = data.frag.type
      state.fragmentTypes[type] = (state.fragmentTypes[type] || 0) + 1
    })
    for (const event of [Hls.Events.LEVEL_LOADING, Hls.Events.LEVEL_SWITCHING, Hls.Events.LEVEL_SWITCHED]) {
      hls.on(event, (_event, data) => {
        if (state.actualLevelEvents.length < 256) state.actualLevelEvents.push({ event, level: data.level,
          bitrate: hls.levels[data.level]?.bitrate ?? null, atSeconds: (performance.now() - state.initialAt) / 1000 })
        if (state.selectionLocked && data.level !== 0) {
          state.fatal = 'unexpected_variant_switch'; hls.stopLoad()
        }
      })
    }
    hls.on(Hls.Events.ERROR, (_event, data) => {
      const jump = /^fragment loaded with buffer holes, seeking from ([0-9.e+-]+) to ([0-9.e+-]+)$/.exec(data.reason || '')
      const observation = { type: data.type, detail: data.details, fatal: data.fatal,
        code: data.response?.code ?? null, atSeconds: (performance.now() - state.initialAt) / 1000,
        beforeFirstFrame: state.firstFrameAt === null, priorRecoveries: state.startupRecoveries.length,
        mediaTime: video.currentTime, seekFrom: jump ? Number(jump[1]) : null,
        seekTo: jump ? Number(jump[2]) : null }
      const startupRecoveryAllowed = window.__jzHlsCanAllowStartupGap(observation)
      if (state.errors.length < 256) state.errors.push({ ...observation, startupRecoveryAllowed })
      if (startupRecoveryAllowed) state.startupRecoveries.push({ ...observation,
        jumpSeconds: observation.seekTo - observation.seekFrom })
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR && data.details !== 'aborted') {
        state.networkErrorCount += 1
        if (state.networkErrorCount >= 6) { state.fatal = 'total_network_error_budget'; hls.stopLoad() }
      }
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        state.hlsMediaErrorCount += 1
        if (!startupRecoveryAllowed) { state.rejectedMediaErrorCount += 1; hls.stopLoad() }
      }
      if (data.fatal) { state.fatal = data.details; hls.stopLoad() }
    })
    hls.on(Hls.Events.LEVEL_LOADED, (_event, data) => { state.live = data.details.live })
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      state.levels = hls.levels.map((level, index) => ({ index, bitrate: level.bitrate,
        width: level.width, height: level.height, videoCodec: level.videoCodec, audioCodec: level.audioCodec }))
      if (!state.levels.length) { state.fatal = 'no_hls_levels'; return }
      const compatible = state.levels.filter(level => {
        if (!level.videoCodec && level.audioCodec && !(level.width > 0 && level.height > 0)) return false
        if (level.videoCodec && !/^avc[13]\./i.test(level.videoCodec)) return false
        if (level.audioCodec && !/^mp4a\.40\./i.test(level.audioCodec)) return false
        const codecs = [level.videoCodec, level.audioCodec].filter(Boolean)
        return !codecs.length || MediaSource.isTypeSupported(`video/mp4; codecs="${codecs.join(',')}"`)
      })
      const declared = compatible.filter(level => level.videoCodec && level.audioCodec)
      const candidates = declared.length ? declared : compatible
      if (!candidates.length) { state.fatal = 'no_compatible_avc_aac_variant'; hls.stopLoad(); return }
      const selected = candidates.reduce((lowest, current) => current.bitrate < lowest.bitrate ? current : lowest)
      state.selectedLevel = { ...selected, effectiveIndex: 0 }
      // preserveManualLevelOnError prevents ABR mode changes but does not itself
      // prevent ErrorController from trying other renditions. Keep only this
      // selected rendition through the public API, before starting any loads.
      for (let index = hls.levels.length - 1; index >= 0; index--) {
        if (index !== selected.index) hls.removeLevel(index)
      }
      state.selectionLocked = true
      hls.subtitleDisplay = false; hls.subtitleTrack = -1
      hls.startLevel = 0; hls.loadLevel = 0; hls.autoLevelCapping = 0
      state.explicitStartLoadCalls += 1
      hls.startLoad(0)
      video.play().catch(error => { state.playRejected = error.name })
    })
    hls.attachMedia(video)
    hls.loadSource(url)
  }, deliveryURL.href)

  const startupAt = performance.now()
  let state
  do {
    state = await sample('startup')
    if (state.decodedFrames > 0 && state.presentedFrames > 0 && state.time >= 0.5 && state.width) break
    if (state.fatal || state.mediaError || state.rejectedMediaErrorCount || state.playRejected || hasHttpError() || network.budgetExceeded || stopped) break
    await delay(1000)
  } while (performance.now() - startupAt < 90000)
  report.startup = state
  const started = state.decodedFrames > 0 && state.presentedFrames > 0 && state.time >= 0.5 && state.width > 0
  if (!started) report.failure = 'startup_not_completed'
  if (started && !stopped) {
    const baseline = await snapshot(), observedAt = performance.now()
    const tolerance = 0.001 // Millisecond numeric tolerance, smaller than a video frame.
    do {
      state = await sample('playback')
      if (state.fatal || state.mediaError || state.rejectedMediaErrorCount || state.playRejected || state.ended || state.paused
        || (state.events.seeking || 0) > (baseline.events.seeking || 0)
        || (state.events.seeked || 0) > (baseline.events.seeked || 0)
        || hasHttpError() || network.budgetExceeded || stopped) break
      if (state.playedSeconds + tolerance >= duration && state.presentedWallSeconds + tolerance >= duration) break
      await delay(1000)
    } while (performance.now() - observedAt < (duration + 30) * 1000)
    const wallSeconds = state.presentedWallSeconds
    const reasons = []
    if (state.playedSeconds + tolerance < duration || wallSeconds + tolerance < duration) reasons.push('insufficient_playback_duration')
    if (state.decodedFrames <= baseline.decodedFrames || state.presentedFrames <= baseline.presentedFrames) reasons.push('frames_did_not_advance')
    if (state.fatal || state.mediaError || state.playRejected) reasons.push('fatal_or_media_error')
    if (state.rejectedMediaErrorCount) reasons.push('hls_media_error')
    if (hasHttpError()) reasons.push('unexpected_http_error')
    if ((state.events.seeking || 0) > (baseline.events.seeking || 0)
      || (state.events.seeked || 0) > (baseline.events.seeked || 0)) reasons.push('unexpected_seek_during_playback')
    if (state.rate !== 1 || (state.events.ratechange || 0) > (baseline.events.ratechange || 0)) reasons.push('rate_changed')
    if (state.maxNoProgressSeconds > 30 || state.waitingSeconds - state.waitingAtFirstFrame > Math.max(2, duration * 0.01)) reasons.push('excessive_stall')
    const naturalEndAtTarget = state.ended && state.playedSeconds + tolerance >= duration && wallSeconds + tolerance >= duration
    if ((state.paused || state.ended) && !naturalEndAtTarget || stopped) reasons.push('unexpected_stop')
    if (network.budgetExceeded || network.unexpectedRequests || report.pageErrors) reasons.push('network_budget_or_unexpected_request')
    report.playback = { passed: reasons.length === 0, reasons, wallSeconds,
      classification: reasons.length ? 'failed' : state.startupRecoveries.length ? 'recovered' : 'clean',
      startupRecoveries: state.startupRecoveries,
      playedSeconds: state.playedSeconds, naturalEndAtTarget, numericToleranceSeconds: tolerance, baseline, final: state }
    if (report.playback.passed && !stopped) {
      const target = state.duration === null ? null : state.ended ? Math.max(0, state.duration - 30) : Math.min(state.duration - 10, state.time + 20)
      if (state.live || target === null || Math.abs(target - state.time) <= 1) report.seek = { passed: false, reason: 'no_vod_seek_target' }
      else {
        const beforeFrames = state.decodedFrames
        await page.evaluate(time => {
          const video = document.querySelector('video'); video.currentTime = time
          if (video.paused) void video.play()
        }, target)
        const seekAt = performance.now()
        do {
          state = await sample('seek')
          if (state.time > target + 0.5 && state.decodedFrames > beforeFrames && !state.paused && !state.mediaError) break
          if (state.fatal || state.mediaError || state.rejectedMediaErrorCount || hasHttpError() || network.budgetExceeded || stopped) break
          await delay(500)
        } while (performance.now() - seekAt < 30000)
        report.seek = { passed: state.time > target + 0.5 && state.decodedFrames > beforeFrames
          && !state.paused && !state.fatal && !state.mediaError && !state.rejectedMediaErrorCount && !hasHttpError() && !network.budgetExceeded && !stopped,
        target, actual: state.time, wallSeconds: (performance.now() - seekAt) / 1000 }
      }
    }
  }
  report.status = report.playback?.passed && report.seek?.passed && !stopped ? 'passed' : 'failed'
} catch (error) {
  report.status = 'failed'
  report.failure = error.name
  report.failureDetail = String(error.message).replaceAll(browserWsEndpoint?.href || '<no-endpoint>', '<browser-endpoint>')
    .replaceAll(relayToken || '<no-token>', '<redacted>')
    .replace(/(?:https?|wss?):[^\s"'<>]+/g, '<url>').slice(0, 1200)
} finally {
  const requestsBefore = network.requests
  if (page) {
    await page.evaluate(() => {
      if (window.__jzHlsProbe) { window.__jzHlsProbe.stopped = true; clearInterval(window.__jzHlsProbe.monitor) }
      window.__jzHlsInstance?.destroy(); window.__jzHlsInstance = null
      const video = document.querySelector('video')
      if (video) { video.pause(); video.removeAttribute('src'); video.load() }
    }).catch(() => {})
  }
  if (context) {
    await context.close().then(() => { contextClosed = true }).catch(() => {})
  } else contextClosed = true
  // Playwright connect() owns a connection and its contexts, not BrowserServer.
  // Browser.close() on this connection disconnects after clearing only those
  // contexts; it does not send the remote BrowserServer.close() command.
  await browser?.close().catch(() => {})
  report.cleanup = { browserClosed: browserWsEndpoint ? null : !browser?.isConnected(),
    browserConnectionClosed: !browser?.isConnected(), ownContextClosed: contextClosed,
    sharedBrowserShutdownRequested: false, requestsAfterDestroy: network.requests - requestsBefore,
    httpErrorResponses: Object.fromEntries(Object.entries(network.responses).filter(([status]) => Number(status) >= 400)) }
  if (!report.cleanup.browserConnectionClosed || !report.cleanup.ownContextClosed
    || report.cleanup.requestsAfterDestroy || hasHttpError() || network.budgetExceeded || network.unexpectedRequests) report.status = 'failed'
  report.browserStatus = report.status
  if (metricsURL) {
    const cleanupDeadline = performance.now() + 20000
    let metrics
    do {
      const remaining = cleanupDeadline - performance.now()
      if (remaining <= 0) break
      metrics = await readMetrics('after_close', Math.min(3000, remaining))
      await appendFile(journal, `${JSON.stringify({ at: new Date().toISOString(), phase: 'after_close', nas: metrics })}\n`)
      if (!metrics.fetchError && Number.isFinite(metrics.active) && Number.isFinite(metrics.queued)
        && (metrics.active === 0 && metrics.queued === 0 || sharedRelay)) break
      // Cleanup has its own bounded wait, even after SIGINT/SIGTERM.
      await new Promise(resolve => setTimeout(resolve, Math.min(1000, Math.max(0, cleanupDeadline - performance.now()))))
    } while (performance.now() < cleanupDeadline)
  }
  report.nas = assessNas()
  if (report.nas.passed === false) report.status = 'failed'
  report.rawStatus = report.status
  const cancelled = stopReason === 'SIGINT' || stopReason === 'SIGTERM'
  report.resultClass = cancelled ? 'cancelled' : report.status !== 'passed' ? 'failed'
    : report.nas.classification === 'observation_gaps' ? 'observation_incomplete'
      : report.nas.classification === 'awaiting_shared_release' ? 'awaiting_shared_release'
        : report.playback?.classification === 'recovered' ? 'recovered_pass' : 'clean_pass'
  if (cancelled) report.status = 'cancelled'
  report.stopReason = stopReason
  report.finishedAt = new Date().toISOString()
  await writeFile(path.join(output, 'summary.json'), `${JSON.stringify(report, null, 2)}\n`)
  console.log(JSON.stringify({ status: report.status, report: path.relative(root, path.join(output, 'summary.json')) }))
  if (report.status !== 'passed') process.exitCode = 1
}
