import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { deferred, flush } from './helpers/renderHarness.js'
import { useCasting } from '../src/useCasting.js'
import * as mp4Playback from '../src/mp4Chunks.js'

function eventTarget() {
  const handlers = new Map()
  return { handlers,
    addEventListener(name, fn) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(fn) },
    removeEventListener(name, fn) { handlers.get(name)?.delete(fn) },
    dispatch(name, event = {}) { for (const fn of handlers.get(name) || []) fn(event) } }
}

async function player(t, { slowCreation = false, p5 = false, blocked = false, versions = [], sessionPlan = null,
  realCasting = false, direct = false, initialCast = null, mp4 = false, clearCastPositionOnStop = false } = {}) {
  const mounted = [], beforeUnmount = [], unmounted = [], refs = [], requests = [], emitted = [], timers = new Map()
  const copied = []
  const saving = deferred(), creation = deferred(), page = eventTarget()
  const castCreation = deferred()
  let castingController, castReadBroken = false, castCurrent = initialCast
  const globals = new Map(['window', 'document', 'localStorage', 'location'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
  Object.defineProperty(globalThis, 'window', { configurable: true, value: page })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { ...eventTarget(), visibilityState: 'visible' } })
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null, setItem() {} } })
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'https://example.test' } })
  let timerId = 0, nextSid = 0, pingStatus = 200, pingResult = { running: true, complete: false }, pendingPing = null
  let clipboardAllowed = true
  t.mock.method(globalThis, 'setInterval', (fn, milliseconds) => { const id = ++timerId; timers.set(id, { fn, milliseconds }); return id })
  t.mock.method(globalThis, 'clearInterval', id => timers.delete(id))
  const video = Vue.markRaw({ ...eventTarget(), currentTime: 0, paused: true, ended: false, readyState: 4,
    buffered: { length: 0 }, videoWidth: 0, playbackRate: 1, volume: 1, muted: false, autoplay: true, src: '',
    querySelectorAll: () => [],
    pause() { this.paused = true; this.dispatch('pause') },
    play() { this.paused = false; this.dispatch('play'); return Promise.resolve() },
    removeAttribute(name) { if (name === 'src') this.src = '' }, load() { this.currentTime = 0 },
  })
  const makePlan = body => ({ seg: 'fmp4', vcopy: !p5,
    height: body?.auto_max_height || (p5 ? 1080 : 720), ...(p5 ? { color_pipeline: 'dovi_p5_sdr', encoder_backend: 'vaapi' } : {}) })
  const api = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body)
    requests.push({ path, options, body })
    if (path.startsWith('/api/cast/current')) {
      if (castReadBroken) throw new TypeError('Failed to fetch')
      return { session: castCurrent }
    }
    if (path === '/api/cast/sessions') return castCreation.promise
    if (path.startsWith('/api/cast/sessions/') && path.endsWith('/control')) return {
      ...castingController.session.value, active: false, state: 'STOPPED', resume_pending: false, resume_target: null,
      ...(clearCastPositionOnStop ? { position: null, position_known: false, last_position: null } : {}) }
    if (path.endsWith('/decide')) return { method: blocked ? 'blocked' : (direct ? 'direct' : p5 ? 'video_transcode' : 'remux'), plan: makePlan(body),
      direct_url: '/api/direct/synthetic.mkv', reasons: p5 ? [blocked ? 'dovi_p5_unavailable' : 'dovi_p5_sdr'] : [],
      media: { duration: 1200, width: p5 ? 3840 : 1280, height: p5 ? 2160 : 720, dv_profile: p5 ? 5 : 0, audio: [], subs: [] } }
    if (path === '/api/stream/versions') return { versions }
    if (path.endsWith('/sessions')) {
      const session = { session_id: `lease-${++nextSid}`, method: p5 ? 'video_transcode' : 'remux', plan: sessionPlan || (mp4 ? { seg: 'mp4' } : makePlan(body)),
        media_start: body.start, initial_time: 0, playlist_url: `/session-${nextSid}.m3u8`,
        ...(mp4 ? { media_url: '/synthetic-segment.mp4', clip_duration: 1200 } : {}) }
      if (nextSid === (slowCreation === true ? 1 : slowCreation)) return creation.promise
      return session
    }
    if (path.endsWith('/ping')) {
      if (pingStatus === 404) throw Object.assign(new Error('expired'), { status: 404 })
      if (pendingPing) { const next = pendingPing; pendingPing = null; return next.promise }
      return pingResult
    }
    if (options.method === 'DELETE') return { closed: true }
    if (options.method === 'POST' && path.includes('/progress')) return saving.promise
    if (path.includes('/progress')) return { position: 112, duration: 1200 }
    return {}
  }
  const component = await loadSfc(new URL('../src/components/PlayerModal.vue', import.meta.url), {
    vue: { ...Vue, ref(value) { const result = Vue.ref(value); refs.push(result); return result },
      onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => beforeUnmount.push(fn), onUnmounted: fn => unmounted.push(fn) },
    '../api.js': { api },
    ...(mp4 ? { '../mp4Chunks.js': { ...mp4Playback, loadMp4Blob: async () => ({ url: 'blob:synthetic-cast-chunk', bytes: 3 }) } } : {}),
    '../clipboard.js': { copyText: async text => { copied.push(text); return clipboardAllowed } },
    '../caps.js': { getCaps: async () => ({ native_hls: true }), probeStrings: async () => [], withProbes: value => value, playbackSupport: () => ({}) },
    '../useFocusTrap.js': { useFocusTrap: () => {} },
    '../usePlaybackPreviews.js': { usePlaybackPreviews: () => ({ manifest: Vue.ref(null), busy: Vue.ref(false),
      ready: Vue.ref(false), status: Vue.ref('') }) },
    '../useCasting.js': { useCasting: options => {
      castingController = realCasting ? useCasting({ ...options, request: api })
        : { open: Vue.ref(false), active: Vue.ref(false), session: Vue.ref(null),
          busy: Vue.ref(false), restore: async () => {}, dispose: () => {} }
      return castingController
    } },
    '../viewing.js': { registerViewingPlaybackCleanup: () => () => {} },
  })
  const scope = Vue.effectScope()
  let render
  scope.run(() => { render = component.setup({ versionId: 1, kind: 'movie', title: '合成影片', preview: false },
    { expose() {}, emit: (name, payload) => emitted.push(payload ? { name, payload } : name) }) })
  refs[1].value = video
  let closed = false
  function close() {
    if (closed) return
    closed = true
    for (const fn of beforeUnmount) fn()
    for (const fn of unmounted) fn()
    scope.stop()
  }
  t.after(() => {
    close()
    saving.resolve({})
    for (const [name, descriptor] of globals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else delete globalThis[name]
    }
  })
  for (const fn of mounted) fn()
  await flush()
  function nodes() {
    const result = []
    function visit(node) {
      if (!node || typeof node !== 'object') return
      result.push(node)
      if (Array.isArray(node.children)) node.children.forEach(visit)
      else if (typeof node.children?.default === 'function') node.children.default().forEach(visit)
    }
    visit(render({}, []))
    return result
  }
  return { video, page, requests, emitted, saving, creation, close, nodes, copied,
    casting: castingController, castCreation,
    setCastReadback: (value, broken = false) => { castCurrent = value; castReadBroken = broken },
    heartbeatResult: result => { pingResult = result },
    holdNextHeartbeat: () => { pendingPing = deferred(); return pendingPing },
    denyClipboard: () => { clipboardAllowed = false },
    expire: () => { pingStatus = 404 },
    tick: async () => { for (const timer of [...timers.values()]) if (timer.milliseconds === 10000) timer.fn(); await flush() },
  }
}

test('actual PlayerModal page exit releases before a hanging progress save and bfcache restores paused position', async t => {
  const ui = await player(t)
  assert.equal(ui.video.src, '/session-1.m3u8')
  ui.video.currentTime = 25.25
  ui.video.pause()
  await flush()
  ui.page.dispatch('pagehide', { persisted: true })
  const deleted = ui.requests.filter(request => request.options.method === 'DELETE')
  assert.deepEqual(deleted.map(request => request.path), ['/api/stream/sessions/lease-1'])
  assert.equal(deleted[0].options.keepalive, true)
  assert.equal(ui.video.src, '')
  ui.page.dispatch('pageshow', { persisted: true })
  await flush()
  const created = ui.requests.filter(request => request.path.endsWith('/sessions'))
  assert.equal(created.length, 2)
  assert.equal(created[1].body.start, 137)
  assert.equal(ui.video.paused, true)
  assert.equal(ui.video.autoplay, false, 'Native autoplay must not override a restored pause')
  assert.equal(ui.video.src, '/session-2.m3u8')
  ui.saving.resolve({})
  await flush()
  const saved = ui.requests.filter(request => request.options.method === 'POST' && request.path.includes('/progress'))
  assert.equal(saved.at(-1).body.position, 137.25, 'Detaching the media must not replace the final source position with zero')
})

for (const exit of ['pagehide', 'unmount']) {
  test(`actual PlayerModal first ${exit} saves the final source position without prior pause or timeupdate`, async t => {
    const ui = await player(t)
    ui.video.currentTime = 40.5
    assert.equal(ui.requests.filter(request => request.options.method === 'POST' && request.path.includes('/progress')).length, 0)
    if (exit === 'pagehide') ui.page.dispatch('pagehide')
    else ui.close()
    assert.equal(ui.requests.filter(request => request.options.method === 'DELETE').length, 1)
    await flush()
    const saved = ui.requests.filter(request => request.options.method === 'POST' && request.path.includes('/progress'))
    assert.equal(saved.length, 1)
    assert.equal(saved[0].body.position, 152.5)
    assert.equal(saved[0].options.keepalive, true)
  })
}

test('actual PlayerModal closes a late startup lease after pagehide and never mounts its media', async t => {
  const ui = await player(t, { slowCreation: true })
  const creation = ui.requests.find(request => request.path.endsWith('/sessions'))
  assert.ok(creation)
  ui.page.dispatch('pagehide')
  assert.equal(creation.options.signal.aborted, true)
  ui.creation.resolve({ session_id: 'late', plan: { seg: 'fmp4' }, playlist_url: '/late.m3u8' })
  await flush()
  const releases = ui.requests.filter(request => request.options.method === 'DELETE')
  assert.deepEqual(releases.map(request => request.path), ['/api/stream/sessions/late'])
  assert.equal(releases[0].options.keepalive, true)
  assert.equal(ui.video.src, '')
  assert.equal(ui.requests.filter(request => request.path.endsWith('/ping')).length, 0)
})

test('actual PlayerModal releases and stops heartbeats at whole-film end before progress is saved', async t => {
  const ui = await player(t)
  ui.video.currentTime = 1088
  ui.video.ended = true
  ui.video.dispatch('ended')
  assert.equal(ui.requests.filter(request => request.options.method === 'DELETE').length, 1)
  const count = ui.requests.filter(request => request.path.endsWith('/ping')).length
  await ui.tick()
  assert.equal(ui.requests.filter(request => request.path.endsWith('/ping')).length, count)
  ui.saving.resolve({})
  await flush()
  assert.deepEqual(ui.emitted, ['watched', 'ended'])
})

test('actual PlayerModal rebuilds expired paused lease from source position and stops repeated failure', async t => {
  const ui = await player(t)
  ui.video.currentTime = 40
  ui.video.pause()
  await flush()
  ui.expire()
  await ui.tick()
  await flush()
  const sessions = ui.requests.filter(request => request.path.endsWith('/sessions'))
  assert.equal(sessions.length, 2)
  assert.equal(sessions[1].body.start, 152)
  assert.equal(ui.video.paused, true)
  assert.equal(ui.video.autoplay, false)
  await ui.tick()
  await flush()
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 2)
})

const button = (ui, label) => ui.nodes().find(node => typeof node.children?.default === 'function'
  && node.children.default().some(child => child.children === label))

test('P5 automatic downgrade preserves the absolute fractional position, pause and playback rate', async t => {
  const ui = await player(t, { p5: true })
  ui.video.currentTime = 40.25
  ui.video.playbackRate = 1.5
  ui.video.dispatch('ratechange')
  const tracks = ui.nodes().find(node => node.props?.onAudioChange)
  tracks.props.onAudioChange(1)
  ui.video.pause()
  await flush()
  ui.heartbeatResult({ running: true, recommended_height: 720 })
  await ui.tick()
  const sessions = ui.requests.filter(request => request.path.endsWith('/sessions'))
  assert.equal(sessions.length, 2)
  assert.equal('auto_max_height' in sessions[0].body, false)
  assert.equal(sessions[1].body.auto_max_height, 720)
  assert.equal(sessions[1].body.quality, 'auto')
  assert.equal(sessions[1].body.start, 152.25)
  assert.equal(sessions[1].body.audio, 1)
  assert.equal(ui.video.paused, true)
  assert.equal(ui.video.playbackRate, 1.5)
  assert.deepEqual(ui.requests.filter(request => request.options.method === 'DELETE').map(request => request.path),
    ['/api/stream/sessions/lease-1'])
  await ui.tick()
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 2)
  ui.heartbeatResult({ failed: true, failure_code: 'dovi_too_slow', failure_reason: '720p 转换仍无法实时播放' })
  await ui.tick()
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 2)
  assert.equal(ui.video.src, '')
})

test('P5 metadata failure stops playback and exposes direct copying without automatic retry or settings navigation', async t => {
  const ui = await player(t, { p5: true })
  ui.heartbeatResult({ failed: true, failure_code: 'dovi_metadata_invalid', failure_reason: '杜比颜色元数据无效' })
  await ui.tick()
  assert.equal(ui.video.src, '')
  assert.equal(ui.requests.filter(request => request.options.method === 'DELETE').length, 1)
  const copy = button(ui, '复制直链')
  assert.ok(copy, 'A failed P5 stream must have a direct action in its error card')
  await copy.props.onClick()
  assert.deepEqual(ui.copied, ['https://example.test/api/direct/synthetic.mkv'])
  assert.equal(button(ui, '重新加载'), undefined)
  ui.expire()
  await ui.tick()
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 1)
})

test('blocked P5 copying offers a selectable URL and the existing compatible version replacement', async t => {
  const ui = await player(t, { p5: true, blocked: true,
    versions: [{ version_id: 2, playable: true, method: 'direct', file_path: '/synthetic/compatible.mp4' }] })
  ui.denyClipboard()
  await button(ui, '复制直链').props.onClick()
  const field = ui.nodes().find(node => node.type === 'input' && node.props['aria-label'] === '播放直链')
  assert.equal(field.props.value, 'https://example.test/api/direct/synthetic.mkv')
  const version = button(ui, '播放兼容版本')
  assert.ok(version)
  version.props.onClick()
  assert.deepEqual(ui.emitted.at(-1), { name: 'version-change',
    payload: { id: 2, kind: 'movie', label: 'compatible.mp4' } })
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 0)
})

test('a late P5 downgrade heartbeat after exit cannot create another session', async t => {
  const ui = await player(t, { p5: true })
  const pending = ui.holdNextHeartbeat()
  const ticking = ui.tick()
  ui.page.dispatch('pagehide')
  pending.resolve({ recommended_height: 720 })
  await ticking
  await flush()
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 1)
  assert.equal(ui.video.src, '')
})

test('a P5 downgrade creation arriving after exit releases its own lease and never mounts media', async t => {
  const ui = await player(t, { p5: true, slowCreation: 2 })
  ui.heartbeatResult({ recommended_height: 720 })
  await ui.tick()
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 2)
  ui.page.dispatch('pagehide')
  ui.creation.resolve({ session_id: 'late-downgrade', plan: { seg: 'fmp4', color_pipeline: 'dovi_p5_sdr', encoder_backend: 'vaapi', height: 720 },
    playlist_url: '/late-downgrade.m3u8' })
  await flush()
  assert.deepEqual(ui.requests.filter(request => request.options.method === 'DELETE').map(request => request.path),
    ['/api/stream/sessions/lease-1', '/api/stream/sessions/late-downgrade'])
  assert.equal(ui.video.src, '')
})

test('P5 never mounts an MP4 or software-transcode fallback returned by a stale server', async t => {
  const ui = await player(t, { p5: true, sessionPlan: { seg: 'mp4', encoder_backend: 'libx264', height: 720 } })
  assert.equal(ui.video.src, '')
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 1)
  assert.deepEqual(ui.requests.filter(request => request.options.method === 'DELETE').map(request => request.path),
    ['/api/stream/sessions/lease-1'])
  assert.ok(button(ui, '复制直链'))
  await ui.tick()
  assert.equal(ui.requests.filter(request => request.path.endsWith('/sessions')).length, 1)
})

test('actual PlayerModal freezes the HLS absolute position across a delayed save and cast creation', async t => {
  const ui = await player(t, { realCasting: true })
  ui.video.currentTime = 25.25
  ui.casting.selected.value = 'tv'
  const work = ui.casting.play()
  assert.equal(ui.video.paused, true)
  assert.equal(ui.video.autoplay, false)
  assert.equal(ui.requests.filter(request => request.path === '/api/cast/sessions').length, 0)
  ui.video.currentTime = 80
  ui.video.dispatch('timeupdate')
  ui.saving.resolve({})
  await flush()
  const cast = ui.requests.find(request => request.path === '/api/cast/sessions')
  assert.equal(cast.body.position, 137.25, 'HLS source offset112 plus currentTime25.25 was frozen at click')
  const saved = ui.requests.filter(request => request.path.includes('/progress') && request.options.method === 'POST')
  assert.equal(saved.at(-1).body.position, 137.25)
  ui.castCreation.resolve({ id: 'remote', active: true, state: 'TRANSITIONING', device: { name: '合成电视' } })
  await work
  assert.equal(ui.video.src, '')
  assert.equal(ui.video.paused, true)
})

for (const paused of [false, true]) {
  test(`failed cast restores the same source position and ${paused ? 'paused' : 'playing'} intent`, async t => {
    const ui = await player(t, { realCasting: true })
    ui.video.currentTime = 25.25
    if (paused) ui.video.pause()
    ui.casting.selected.value = 'tv'
    const work = ui.casting.play()
    ui.saving.resolve({})
    await flush()
    ui.castCreation.reject(new Error('电视拒绝起播'))
    await work
    await flush()
    const sessions = ui.requests.filter(request => request.path.startsWith('/api/stream/') && request.path.endsWith('/sessions'))
    assert.equal(sessions.length, 2)
    assert.equal(sessions[1].body.start, 137, 'Ordinary stream startup keeps its existing whole-second seek precision')
    assert.equal(ui.video.paused, paused)
    assert.equal(ui.video.autoplay, !paused)
    assert.match(ui.casting.error.value, /电视拒绝起播/)
  })
}

test('actual PlayerModal captures the direct-file timeline without adding a previous HLS/resume offset', async t => {
  const ui = await player(t, { realCasting: true, direct: true })
  ui.video.currentTime = 35.5
  ui.casting.selected.value = 'tv'
  const work = ui.casting.play()
  ui.saving.resolve({})
  await flush()
  assert.equal(ui.requests.find(request => request.path === '/api/cast/sessions').body.position, 35.5)
  ui.castCreation.reject(new Error('模拟失败'))
  await work
  await flush()
})

test('actual MP4 chunk casting preserves the source offset instead of sending the chunk-local clock', async t => {
  const ui = await player(t, { realCasting: true, mp4: true })
  assert.equal(ui.video.src, 'blob:synthetic-cast-chunk')
  ui.video.currentTime = 25.25
  ui.casting.selected.value = 'tv'
  const work = ui.casting.play()
  ui.saving.resolve({})
  await flush()
  assert.equal(ui.requests.find(request => request.path === '/api/cast/sessions').body.position, 137.25)
  ui.castCreation.reject(new Error('模拟失败'))
  await work
  await flush()
})

test('casting during a pending stream seek uses the selected target and late local creation cannot mount', async t => {
  const ui = await player(t, { realCasting: true, slowCreation: 2 })
  const seek = ui.nodes().find(node => node.props?.onInput && node.props?.onCommit)
  seek.props.onInput({ target: { value: '700' } })
  seek.props.onCommit({ target: { value: '700' } })
  await flush()
  ui.casting.selected.value = 'tv'
  const work = ui.casting.play()
  ui.saving.resolve({})
  await flush()
  assert.equal(ui.requests.find(request => request.path === '/api/cast/sessions').body.position, 700)
  ui.castCreation.resolve({ id: 'remote', active: true, state: 'TRANSITIONING', device: { name: '合成电视' } })
  await work
  ui.creation.resolve({ session_id: 'stale-seek', plan: { seg: 'fmp4' }, playlist_url: '/stale.m3u8' })
  await flush()
  assert.equal(ui.video.src, '')
  assert.ok(ui.requests.some(request => request.options.method === 'DELETE' && request.path.endsWith('/stale-seek')))
})

test('page exit during handoff preparation prevents TV creation and a failed cast never resumes an exited phone', async t => {
  const ui = await player(t, { realCasting: true })
  ui.video.currentTime = 18
  ui.casting.selected.value = 'tv'
  const work = ui.casting.play()
  ui.page.dispatch('pagehide')
  ui.saving.resolve({})
  await work
  await flush()
  assert.equal(ui.requests.filter(request => request.path === '/api/cast/sessions').length, 0)
  assert.equal(ui.video.src, '')
  assert.equal(ui.video.paused, true)
  assert.equal(ui.requests.filter(request => request.path.startsWith('/api/stream/') && request.path.endsWith('/sessions')).length, 1)
})

test('a creation failure after page exit leaves the released phone paused', async t => {
  const ui = await player(t, { realCasting: true })
  ui.video.currentTime = 18
  ui.casting.selected.value = 'tv'
  const work = ui.casting.play()
  ui.saving.resolve({})
  await flush()
  ui.page.dispatch('pagehide')
  ui.castCreation.reject(new Error('迟到的接收器错误'))
  await work
  await flush()
  assert.equal(ui.video.src, '')
  assert.equal(ui.video.paused, true)
  assert.equal(ui.requests.filter(request => request.path.startsWith('/api/stream/') && request.path.endsWith('/sessions')).length, 1)
})

test('an unknown creation plus unreadable state keeps the real phone held until absence is confirmed', async t => {
  const ui = await player(t, { realCasting: true })
  ui.video.currentTime = 25.25
  ui.casting.selected.value = 'tv'
  const work = ui.casting.play()
  ui.saving.resolve({})
  await flush()
  ui.setCastReadback(null, true)
  ui.castCreation.reject(new TypeError('Failed to fetch'))
  await work
  await flush()
  assert.equal(ui.casting.uncertain.value, true)
  assert.equal(ui.video.src, '')
  assert.equal(ui.video.paused, true)
  assert.equal(ui.requests.filter(request => request.path.startsWith('/api/stream/') && request.path.endsWith('/sessions')).length, 1)
  ui.setCastReadback(null)
  await ui.casting.reconcile()
  await flush()
  const streams = ui.requests.filter(request => request.path.startsWith('/api/stream/') && request.path.endsWith('/sessions'))
  assert.equal(streams.length, 2)
  assert.equal(streams[1].body.start, 137)
  assert.equal(ui.video.paused, false)
  assert.equal(ui.casting.uncertain.value, false)
  assert.equal(ui.requests.filter(request => request.path === '/api/cast/sessions').length, 1)
})

for (const lastPosition of [null, 125]) {
  test(`returning a pending receiver to the phone uses ${lastPosition == null ? 'the saved requested fallback' : 'the real last TV position'}`, async t => {
    const ui = await player(t, { realCasting: true, initialCast: { id: 'pending', active: true,
      state: 'TRANSITIONING', resume_pending: true, resume_target: 145,
      position: 0, position_known: false, last_position: lastPosition, device: { name: '电视' } } })
    ui.casting.open.value = true
    const panel = ui.nodes().find(node => node.props?.onReturnPhone)
    await panel.props.onReturnPhone()
    await flush()
    const stream = ui.requests.find(request => request.path.startsWith('/api/stream/') && request.path.endsWith('/sessions'))
    assert.equal(stream.body.start, lastPosition ?? 145)
    assert.equal(ui.casting.session.value, null)
  })

  test(`restored pending App cancellation retains ${lastPosition == null ? 'its requested fallback' : 'real TV progress ahead of the request'} for a later phone return`, async t => {
    const ui = await player(t, { realCasting: true, clearCastPositionOnStop: true, initialCast: { id: 'pending-app', active: true,
      receiver_type: 'jzmedia_tv', state: 'WAITING_FOR_APP', resume_pending: true, resume_target: 145,
      position: null, position_known: false, last_position: lastPosition, device: { name: '电视 App' } } })
    assert.equal(ui.video.src, '')
    assert.equal(await ui.casting.control('stop'), true)
    assert.equal(ui.casting.session.value.resume_target, null)
    assert.equal(ui.casting.session.value.last_position, null)
    ui.casting.open.value = true
    const panel = ui.nodes().find(node => node.props?.onReturnPhone)
    await panel.props.onReturnPhone()
    await flush()
    const stream = ui.requests.find(request => request.path.startsWith('/api/stream/') && request.path.endsWith('/sessions'))
    assert.equal(stream.body.start, lastPosition ?? 145)
    assert.equal(ui.requests.filter(request => request.path.endsWith('/control')).length, 1, 'Returning after cancellation does not repeat Stop')
  })
}
