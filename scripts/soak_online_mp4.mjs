// Isolated public MP4 acceptance: decode on this development browser, never on the NAS.
// node scripts/soak_online_mp4.mjs --direct-url HTTPS --relay-url LOOPBACK --metrics-url LOOPBACK
// A temporary NAS Tailscale address requires --allow-tailscale-relay true.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { appendFile, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../docs/node_modules/playwright/index.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const values = new Map()
const args = process.argv.slice(2)
if (args.length === 1 && args[0] === '--help') {
  console.log(`Usage: node scripts/soak_online_mp4.mjs --direct-url PUBLIC_MP4 --relay-url TEMP_MEDIA_URL
  --metrics-url TEMP_METRICS_URL [--duration 1800] [--sample 15] [--baseline 30]
  [--second-start 600] [--second-duration 300] [--output output/playwright/online-mp4-soak]
  [--allow-tailscale-relay true]

The public source must be an official Blender 360p MP4. Relay/metrics default to
loopback; a temporary Tailscale IPv4 requires the explicit option above. Configure
the separate scripts/online_diagnostics/mp4_relay.py process before running this command.
The browser decodes at normal speed, muted; startup is limited to 90 seconds.
Each main stream must play for the target media AND real seconds without seeking.
This diagnostic does not modify the formal application, or establish TV/audio support.
Use --duration 30 --baseline 3 --sample 2 --second-start 10 --second-duration 8
for a short preflight. Short runs do not satisfy the 30-minute acceptance gate.`)
  process.exit(0)
}
for (let index = 0; index < args.length; index += 2) {
  assert.ok(args[index].startsWith('--') && args[index + 1], 'Arguments must be --name value pairs')
  assert.ok(!values.has(args[index]), `Duplicate argument ${args[index]}`)
  values.set(args[index], args[index + 1])
}
const allowed = ['--direct-url', '--relay-url', '--metrics-url', '--duration', '--sample', '--baseline',
  '--second-start', '--second-duration', '--output', '--allow-loopback-direct', '--allow-tailscale-relay']
for (const key of values.keys()) assert.ok(allowed.includes(key), `Unknown option ${key}`)
const number = (key, fallback, minimum, maximum) => {
  const value = Number(values.get(key) ?? fallback)
  assert.ok(Number.isFinite(value) && value >= minimum && value <= maximum, `Invalid ${key}`)
  return value
}
const duration = number('--duration', 1800, 5, 2100)
const sampleSeconds = number('--sample', 15, 1, 30)
const baselineSeconds = number('--baseline', 30, 0, 120)
const secondStart = number('--second-start', 600, 0, 2100)
const secondDuration = number('--second-duration', 300, 0, 600)
const directURL = new URL(values.get('--direct-url'))
const relayURL = new URL(values.get('--relay-url'))
const loopback = url => url.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(url.hostname)
const tailscale = url => {
  const octets = url.hostname.split('.').map(Number)
  return values.get('--allow-tailscale-relay') === 'true' && url.protocol === 'http:' && octets.length === 4
    && octets.every(part => Number.isInteger(part) && part >= 0 && part <= 255)
    && octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127
}
const diagnosticAddress = url => loopback(url) || tailscale(url)
assert.ok((directURL.protocol === 'https:' && directURL.hostname === 'video.blender.org'
  && /^\/object-storage\/web_videos\/[a-f0-9-]+-360\.mp4$/.test(directURL.pathname))
  || (values.get('--allow-loopback-direct') === 'true' && loopback(directURL)), 'Direct source must be a fixed official Blender 360p MP4 or an explicitly enabled loopback fixture')
assert.ok(diagnosticAddress(relayURL), 'Relay must be loopback or an explicitly enabled Tailscale IPv4 diagnostic address')
assert.ok(!directURL.username && !directURL.password && !relayURL.username && !relayURL.password, 'URL credentials are not accepted')
const metricsURL = values.has('--metrics-url') ? new URL(values.get('--metrics-url')) : null
if (metricsURL) assert.ok(diagnosticAddress(metricsURL) && metricsURL.origin === relayURL.origin
  && !metricsURL.username && !metricsURL.password, 'Metrics must use the same isolated diagnostic relay origin')
const output = path.resolve(root, values.get('--output') ?? 'output/playwright/online-mp4-soak')
assert.ok(output.startsWith(path.join(root, 'output/playwright/')), 'Evidence must be under output/playwright/')
await mkdir(output, { recursive: true })
const journal = path.join(output, 'samples.jsonl')
await writeFile(journal, '')
const pendingDelays = new Set()
const delay = milliseconds => new Promise(resolve => {
  const finish = () => { clearTimeout(timer); pendingDelays.delete(finish); resolve() }
  const timer = setTimeout(finish, Math.max(0, milliseconds))
  pendingDelays.add(finish)
})
const digest = value => createHash('sha256').update(value).digest('hex')
const redact = value => {
  let result = String(value)
  for (const url of [relayURL, metricsURL].filter(Boolean)) {
    result = result.replaceAll(url.href, '[diagnostic-url]')
    for (const segment of url.pathname.split('/').filter(segment => segment.length >= 16)) result = result.replaceAll(segment, '[token]')
    for (const parameter of url.searchParams.values()) if (parameter) result = result.replaceAll(parameter, '[parameter]')
  }
  return result
}
const tracks = []
const notes = []
const metricsSamples = []
let browser, stopped = false, stopReason = null, soakStarted = null, lastRecord = null
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
  stopped = true
  stopReason = signal
  for (const finish of pendingDelays) finish()
})
const report = {
  startedAt: new Date().toISOString(), status: 'running',
  scope: 'Development Chromium decodes public MP4 in real time. Muted, no manual listening, no TV/mobile/hardware-decoder claim. NAS relays bytes only. No explicit media download or recording.',
  configuration: { durationSeconds: duration, sampleSeconds, baselineSeconds, secondStart, secondDuration,
    applicationProxy: 'browser proxy environment cleared and Chromium --no-proxy-server; transparent network routing uninspected',
    directSource: { host: directURL.hostname, urlSha256: digest(directURL.href) }, relayURLSha256: digest(relayURL.href),
    metricsConfigured: Boolean(metricsURL), rate: 1, automaticRetries: 0,
    acceptance: 'Each main stream advances at least target media seconds over at least target real seconds, no fatal error/seek/rate change, maximum observed no-progress interval <= 30 seconds, waiting <= 1% of target or 2 seconds. Up to 30 extra wall seconds are allowed for measured buffering/polling.' },
  scriptSha256: digest(await readFile(fileURLToPath(import.meta.url))), samples: 0, tracks: [], notes,
}

async function metrics() {
  if (!metricsURL) return null
  try {
    const response = await fetch(metricsURL, { signal: AbortSignal.timeout(3000) })
    if (!response.ok) return { fetchError: `HTTP_${response.status}` }
    return await response.json()
  } catch (error) { return { fetchError: error.name } }
}

async function record(phase, snapshots = []) {
  const row = { timestamp: new Date().toISOString(), phase,
    elapsedSeconds: soakStarted ? (performance.now() - soakStarted) / 1000 : null,
    tracks: snapshots, nas: await metrics() }
  await appendFile(journal, `${JSON.stringify(row)}\n`)
  if (metricsURL) metricsSamples.push({ phase, value: row.nas })
  lastRecord = row
  report.samples += 1
  console.log(JSON.stringify({ phase, elapsedSeconds: row.elapsedSeconds,
    tracks: snapshots.map(item => ({ name: item.name, time: item.currentTime,
      advanced: item.mediaAdvanced, frames: item.decodedFrames, error: item.error, closed: item.closed })) }))
  return row
}

async function startTrack(name, sourceURL) {
  const context = await browser.newContext({ viewport: { width: 800, height: 600 }, serviceWorkers: 'block' })
  const track = { name, context, sourceURL, page: await context.newPage(), closed: false, startupError: null,
    initialized: false, acceptanceStartedAt: null,
    requests: 0, responses: {}, requestFailures: {}, unexpectedRequests: 0, pageErrors: 0,
    responseDiagnostics: [], responseTasks: [],
    startedAt: performance.now(), startSnapshot: null, finalSnapshot: null, lastProgressAt: null,
    maximumNoProgressSeconds: 0, previousTime: null, seek: null }
  tracks.push(track)
  await context.route('**/*', route => {
    if (route.request().url() === sourceURL.href) { track.requests += 1; return route.continue() }
    track.unexpectedRequests += 1
    return route.abort()
  })
  track.page.on('response', response => {
    track.responses[response.status()] = (track.responses[response.status()] ?? 0) + 1
    if (response.status() < 400 || track.responseTasks.length >= 8) return
    track.responseTasks.push((async () => {
      const headers = response.headers(), diagnostic = { status: response.status(), headers: {} }
      for (const key of ['server', 'via', 'x-cache', 'content-type', 'content-length']) {
        if (headers[key]) diagnostic.headers[key] = redact(headers[key]).slice(0, 256)
      }
      diagnostic.peer = await response.serverAddr().catch(() => null)
      // Response.body buffers a complete body: never invoke it for an unbounded response.
      const length = Number(headers['content-length'])
      if (Number.isSafeInteger(length) && length >= 0 && length <= 4096) {
        try { diagnostic.bodyPrefix = redact((await response.body()).subarray(0, 256).toString('utf8')) }
        catch (error) { diagnostic.bodyReadError = error.name }
      } else diagnostic.bodySkipped = 'missing_or_large_content_length'
      track.responseDiagnostics.push(diagnostic)
    })().catch(error => { track.responseDiagnostics.push({ status: response.status(), diagnosticError: error.name }) }))
  })
  track.page.on('requestfailed', request => {
    const reason = request.failure()?.errorText || 'unknown'
    track.requestFailures[reason] = (track.requestFailures[reason] ?? 0) + 1
  })
  track.page.on('pageerror', () => { track.pageErrors += 1 })
  await track.page.setContent('<!doctype html><html lang="en"><meta charset="utf-8"><title>jzmedia MP4 playback observation</title><body><h1>Public MP4 playback observation</h1><video controls muted playsinline width="640" height="360"></video></body></html>')
  await track.page.evaluate(url => {
    const video = document.querySelector('video')
    const state = window.__jzMp4Soak = { events: {}, waitingSince: null, waitingMs: 0, firstFrameMs: null,
      initialAt: performance.now(), playRejected: null, eventLog: [] }
    for (const name of ['loadstart', 'loadedmetadata', 'loadeddata', 'canplay', 'playing', 'waiting',
      'stalled', 'pause', 'ended', 'error', 'seeking', 'seeked', 'ratechange', 'emptied']) {
      video.addEventListener(name, () => {
        state.events[name] = (state.events[name] || 0) + 1
        state.eventLog.push({ name, at: (performance.now() - state.initialAt) / 1000, time: video.currentTime })
        if (state.eventLog.length > 200) state.eventLog.shift()
        if (name === 'waiting' && state.waitingSince === null) state.waitingSince = performance.now()
        if (name === 'playing' && state.waitingSince !== null) {
          state.waitingMs += performance.now() - state.waitingSince
          state.waitingSince = null
        }
      })
    }
    if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(() => { state.firstFrameMs = performance.now() - state.initialAt })
    video.src = url
    video.play().catch(error => { state.playRejected = error.name })
  }, sourceURL.href)
  track.initialized = true
  try {
    await track.page.waitForFunction(() => {
      const v = document.querySelector('video')
      if (v.error || window.__jzMp4Soak.playRejected) return true
      return v.currentTime >= 0.5 && v.videoWidth > 0 && (v.webkitDecodedFrameCount || v.getVideoPlaybackQuality?.().totalVideoFrames) > 0
    }, null, { timeout: 90000 })
    const initial = await snapshot(track)
    if (initial.error || initial.playRejected) track.startupError = initial.error || initial.playRejected
    else {
      track.startSnapshot = initial
      track.acceptanceStartedAt = performance.now()
      track.lastProgressAt = track.acceptanceStartedAt
      track.maximumNoProgressSeconds = 0
    }
  } catch (error) { track.startupError = error.name }
  return track
}

async function snapshot(track) {
  if (track.closed) return { ...track.finalSnapshot, closed: true }
  if (!track.initialized) return { name: track.name, currentTime: 0, mediaAdvanced: 0,
    decodedFrames: 0, error: null, initializing: true }
  const state = await track.page.evaluate(() => {
    const video = document.querySelector('video'), state = window.__jzMp4Soak
    const quality = video.getVideoPlaybackQuality?.()
    return { currentTime: video.currentTime, duration: Number.isFinite(video.duration) ? video.duration : null,
      decodedFrames: video.webkitDecodedFrameCount || quality?.totalVideoFrames || 0,
      totalFrames: quality?.totalVideoFrames || 0, droppedFrames: quality?.droppedVideoFrames || 0,
      width: video.videoWidth, height: video.videoHeight, readyState: video.readyState, networkState: video.networkState,
      paused: video.paused, ended: video.ended, rate: video.playbackRate, error: video.error?.code || null,
      waitingSeconds: (state.waitingMs + (state.waitingSince === null ? 0 : performance.now() - state.waitingSince)) / 1000,
      events: { ...state.events }, eventLog: [...state.eventLog], firstFrameMs: state.firstFrameMs,
      playRejected: state.playRejected, buffered: Array.from({ length: video.buffered.length }, (_, index) => [video.buffered.start(index), video.buffered.end(index)]),
      played: Array.from({ length: video.played.length }, (_, index) => [video.played.start(index), video.played.end(index)]) }
  })
  const now = performance.now()
  if (track.previousTime === null || state.currentTime > track.previousTime + 0.05) track.lastProgressAt = now
  if (track.lastProgressAt !== null) track.maximumNoProgressSeconds = Math.max(track.maximumNoProgressSeconds, (now - track.lastProgressAt) / 1000)
  track.previousTime = state.currentTime
  return { name: track.name, ...state, mediaAdvanced: track.startSnapshot ? state.currentTime - track.startSnapshot.currentTime : 0,
    observedWallSeconds: (now - (track.acceptanceStartedAt ?? track.startedAt)) / 1000,
    startupWallSeconds: track.acceptanceStartedAt === null ? null : (track.acceptanceStartedAt - track.startedAt) / 1000,
    maximumNoProgressSeconds: track.maximumNoProgressSeconds,
    requestCount: track.requests, responses: { ...track.responses }, requestFailures: { ...track.requestFailures },
    responseDiagnostics: [...track.responseDiagnostics],
    unexpectedRequests: track.unexpectedRequests, pageErrors: track.pageErrors }
}

async function startObserved(specifications, phase) {
  let settled = false
  const pending = Promise.all(specifications.map(([name, url]) => startTrack(name, url)))
  const finished = pending.then(() => { settled = true }, () => { settled = true })
  while (!settled && !stopped) {
    await record(phase, await Promise.all(tracks.map(snapshot)))
    await Promise.race([finished, delay(sampleSeconds * 1000)])
  }
  return await pending
}

async function closeTrack(track) {
  if (track.closed) return
  track.finalSnapshot = await snapshot(track)
  await track.page.evaluate(() => { const v = document.querySelector('video'); v.pause(); v.removeAttribute('src'); v.load() }).catch(() => {})
  await track.context.close()
  await Promise.allSettled(track.responseTasks)
  track.closed = true
}

function acceptance(track, target) {
  const state = track.finalSnapshot
  if (!state || !track.startSnapshot) return { passed: false, reasons: ['startup_failed'], startupError: track.startupError }
  const reasons = []
  if (state.mediaAdvanced < target) reasons.push('insufficient_media_seconds')
  if (state.observedWallSeconds < target) reasons.push('insufficient_real_seconds')
  if (state.error || state.playRejected || state.events.error) reasons.push('media_error')
  if (state.paused || state.ended) reasons.push('unexpected_pause_or_end')
  if (state.rate !== 1 || state.events.ratechange) reasons.push('playback_rate_changed')
  if (state.events.seeking || state.events.seeked) reasons.push('seek_during_soak')
  if (state.decodedFrames <= track.startSnapshot.decodedFrames || !state.width) reasons.push('no_decoded_frames')
  if (state.maximumNoProgressSeconds > 30) reasons.push('long_no_progress_interval')
  if (state.waitingSeconds - track.startSnapshot.waitingSeconds > Math.max(2, target * 0.01)) reasons.push('excessive_waiting')
  if (state.unexpectedRequests || state.pageErrors) reasons.push('unexpected_request_or_page_error')
  if (stopReason) reasons.push('interrupted')
  return { passed: reasons.length === 0, reasons, targetSeconds: target }
}

function nasAcceptance() {
  if (!metricsURL) return { observed: false, passed: null }
  const required = ['active', 'peak_active', 'bytes_sent', 'upstream_bytes', 'upstream_errors', 'retries',
    'rejected_busy', 'rejected_budget', 'cpu_seconds', 'rss_kib', 'max_rss_kib', 'uptime_seconds', 'process_write_bytes']
  const reasons = [], missing = new Set(), phases = {}
  for (const sample of metricsSamples) {
    for (const key of required) if (typeof sample.value?.[key] !== 'number') missing.add(key)
    if (!Array.isArray(sample.value?.child_pids)) missing.add('child_pids')
  }
  if (missing.size) reasons.push('missing_or_failed_metrics')
  const first = metricsSamples[0]?.value, last = metricsSamples.at(-1)?.value
  if (last?.active !== 0) reasons.push('connections_not_released')
  if (last?.peak_active > 2) reasons.push('concurrency_limit_exceeded')
  if (!(last?.bytes_sent > first?.bytes_sent)) reasons.push('no_relayed_bytes')
  if (last?.upstream_errors > first?.upstream_errors) reasons.push('upstream_errors')
  if (last?.rejected_busy > first?.rejected_busy) reasons.push('requests_rejected_busy')
  if (last?.rejected_budget > first?.rejected_budget) reasons.push('byte_budget_exhausted')
  if (last?.process_write_bytes !== first?.process_write_bytes) reasons.push('process_storage_writes')
  if (metricsSamples.some(sample => sample.value?.child_pids?.length)) reasons.push('relay_child_processes')
  for (let index = 1; index < metricsSamples.length; index += 1) {
    const sample = metricsSamples[index], prior = metricsSamples[index - 1]
    if (sample.phase !== prior.phase || !sample.value || !prior.value) continue
    const seconds = sample.value.uptime_seconds - prior.value.uptime_seconds
    if (!(seconds > 0)) continue
    const stats = phases[sample.phase] ??= { intervals: 0, seconds: 0, cpuSeconds: 0, maxCpuPercentOneCore: 0,
      maxRssKiB: 0, upstreamBytes: 0, bytesSent: 0 }
    const cpu = sample.value.cpu_seconds - prior.value.cpu_seconds
    stats.intervals += 1
    stats.seconds += seconds
    stats.cpuSeconds += cpu
    stats.maxCpuPercentOneCore = Math.max(stats.maxCpuPercentOneCore, cpu / seconds * 100)
    stats.maxRssKiB = Math.max(stats.maxRssKiB, sample.value.rss_kib)
    stats.upstreamBytes += sample.value.upstream_bytes - prior.value.upstream_bytes
    stats.bytesSent += sample.value.bytes_sent - prior.value.bytes_sent
  }
  for (const stats of Object.values(phases)) stats.meanCpuPercentOneCore = stats.cpuSeconds / stats.seconds * 100
  return { observed: true, passed: reasons.length === 0, reasons, missingFields: [...missing],
    description: 'Checks instrumentation, bounded concurrency, zero storage writes/child processes, upstream errors and connection cleanup. CPU/RSS are measured observations; no universal NAS performance threshold is inferred.',
    phases, first, last }
}

try {
  const browserEnvironment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(http|https|all|no)_proxy$/i.test(key)))
  browserEnvironment.NO_PROXY = '*'
  browser = await chromium.launch({ headless: true, env: browserEnvironment, args: ['--no-proxy-server', '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'] })
  report.browserVersion = browser.version()
  const baselineAt = performance.now()
  do {
    await record('baseline')
    if ((performance.now() - baselineAt) / 1000 >= baselineSeconds || stopped) break
    await delay(Math.min(sampleSeconds * 1000, baselineSeconds * 1000 - (performance.now() - baselineAt)))
  } while (!stopped)
  if (!stopped) await startObserved([['direct', directURL], ['nas-relay', relayURL]], 'startup')
  soakStarted = performance.now()
  // Anchor both main streams at the common start after startup; startup remains separately recorded.
  for (const track of tracks) {
    if (!track.startupError) {
      track.acceptanceStartedAt = soakStarted
      track.lastProgressAt = soakStarted
      track.maximumNoProgressSeconds = 0
      track.startSnapshot = await snapshot(track)
    }
  }
  let second = null
  while (!stopped && (performance.now() - soakStarted) / 1000 <= duration + 30) {
    const elapsed = (performance.now() - soakStarted) / 1000
    if (!second && secondDuration > 0 && secondStart < duration && elapsed >= secondStart) {
      const started = await startObserved([['nas-second', relayURL]], 'second_startup')
      second = started[0]
      await record('second_started', [await snapshot(second)])
      if (second.startupError) await closeTrack(second)
    }
    if (second && !second.closed && second.startSnapshot) {
      const secondState = await snapshot(second)
      if (secondState.mediaAdvanced >= secondDuration || secondState.error
        || secondState.observedWallSeconds >= secondDuration + 90) await closeTrack(second)
    }
    const snapshots = await Promise.all(tracks.map(snapshot))
    await record(second && !second.closed ? 'two_nas_clients' : 'one_nas_client', snapshots)
    const main = snapshots.filter(state => state.name !== 'nas-second')
    if (tracks.filter(track => track.name !== 'nas-second').every(track => track.startupError)) break
    if (elapsed >= duration && main.every(state => state.mediaAdvanced >= duration || state.error || state.paused)) break
    if (main.length && main.every(state => state.error || state.playRejected)) break
    await delay(Math.max(0, Math.min(sampleSeconds, duration + 30 - elapsed) * 1000))
  }
  for (const track of tracks) {
    if (!track.closed) track.finalSnapshot = await snapshot(track)
    const result = acceptance(track, track.name === 'nas-second' ? secondDuration : duration)
    // Seek is deliberately outside the no-seek acceptance interval.
    if (!stopped && track.name !== 'nas-second' && !track.closed && result.passed) {
      const target = Math.min(track.finalSnapshot.duration - 10, track.finalSnapshot.currentTime + 20)
      try {
        await track.page.evaluate(time => { document.querySelector('video').currentTime = time }, target)
        await track.page.waitForFunction(time => {
          const video = document.querySelector('video')
          return !video.seeking && !video.paused && !video.error && video.currentTime > time + 0.5
        }, target, { timeout: 30000 })
        track.seek = { passed: true, target, after: (await snapshot(track)).currentTime }
      } catch (error) { track.seek = { passed: false, target, error: error.name } }
    }
    const finalBeforeSeek = track.finalSnapshot
    await closeTrack(track)
    track.finalSnapshot = finalBeforeSeek
    report.tracks.push({ name: track.name, startupError: track.startupError, startup: track.startSnapshot,
      final: track.finalSnapshot, acceptance: result, seekAfterSoak: track.seek })
  }
  await record('closed', await Promise.all(tracks.map(snapshot)))
  const closingAt = performance.now()
  do {
    await delay(2000)
    report.afterClose = await record('after_close', await Promise.all(tracks.map(snapshot)))
  } while (!stopped && metricsURL && report.afterClose.nas?.active > 0 && performance.now() - closingAt < 20000)
  report.nas = nasAcceptance()
  report.status = report.tracks.length >= 2 && report.tracks.every(track => track.acceptance.passed
    && track.seekAfterSoak?.passed !== false) && report.nas.passed !== false && !stopped ? 'passed' : 'failed'
} catch (error) {
  report.status = 'failed'
  report.failure = { name: error.name, message: redact(error.message) }
  process.exitCode = 1
} finally {
  for (const track of tracks) {
    if (!track.closed) await closeTrack(track).catch(() => {})
  }
  if (browser) await browser.close()
  report.finishedAt = new Date().toISOString()
  report.stopReason = stopReason
  report.lastSampleAt = lastRecord?.timestamp ?? null
  if (!report.afterClose) report.afterClose = await record('cleanup')
  if (!report.nas) report.nas = nasAcceptance()
  await writeFile(path.join(output, 'summary.json'), `${JSON.stringify(report, null, 2)}\n`)
  console.log(JSON.stringify({ status: report.status, report: path.relative(root, path.join(output, 'summary.json')) }))
  if (report.status !== 'passed') process.exitCode = 1
}
