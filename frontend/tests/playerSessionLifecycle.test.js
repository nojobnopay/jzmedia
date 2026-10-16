import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { deferred, flush } from './helpers/renderHarness.js'

function eventTarget() {
  const handlers = new Map()
  return { handlers,
    addEventListener(name, fn) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(fn) },
    removeEventListener(name, fn) { handlers.get(name)?.delete(fn) },
    dispatch(name, event = {}) { for (const fn of handlers.get(name) || []) fn(event) } }
}

async function player(t, { slowCreation = false } = {}) {
  const mounted = [], beforeUnmount = [], unmounted = [], refs = [], requests = [], emitted = [], timers = new Map()
  const saving = deferred(), creation = deferred(), page = eventTarget()
  const globals = new Map(['window', 'document', 'localStorage'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
  Object.defineProperty(globalThis, 'window', { configurable: true, value: page })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { ...eventTarget(), visibilityState: 'visible' } })
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null, setItem() {} } })
  let timerId = 0, nextSid = 0, pingStatus = 200
  t.mock.method(globalThis, 'setInterval', (fn, milliseconds) => { const id = ++timerId; timers.set(id, { fn, milliseconds }); return id })
  t.mock.method(globalThis, 'clearInterval', id => timers.delete(id))
  const video = Vue.markRaw({ ...eventTarget(), currentTime: 0, paused: true, ended: false, readyState: 4,
    buffered: { length: 0 }, videoWidth: 0, playbackRate: 1, volume: 1, muted: false, autoplay: true, src: '',
    querySelectorAll: () => [],
    pause() { this.paused = true; this.dispatch('pause') },
    play() { this.paused = false; this.dispatch('play'); return Promise.resolve() },
    removeAttribute(name) { if (name === 'src') this.src = '' }, load() { this.currentTime = 0 },
  })
  const plan = { seg: 'fmp4', vcopy: true, height: 720 }
  const api = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body)
    requests.push({ path, options, body })
    if (path.endsWith('/decide')) return { method: 'remux', plan, media: { duration: 1200, width: 1280, height: 720, audio: [], subs: [] } }
    if (path.endsWith('/sessions')) {
      const session = { session_id: `lease-${++nextSid}`, method: 'remux', plan,
        media_start: body.start, initial_time: 0, playlist_url: `/session-${nextSid}.m3u8` }
      if (slowCreation && nextSid === 1) return creation.promise
      return session
    }
    if (path.endsWith('/ping')) {
      if (pingStatus === 404) throw Object.assign(new Error('expired'), { status: 404 })
      return { running: true, complete: false }
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
    '../caps.js': { getCaps: async () => ({ native_hls: true }), probeStrings: async () => [], withProbes: value => value, playbackSupport: () => ({}) },
    '../useFocusTrap.js': { useFocusTrap: () => {} },
    '../usePlaybackPreviews.js': { usePlaybackPreviews: () => ({ manifest: Vue.ref(null) }) },
    '../useCasting.js': { useCasting: () => ({ open: Vue.ref(false), active: Vue.ref(false), session: Vue.ref(null),
      busy: Vue.ref(false), restore: async () => {}, dispose: () => {} }) },
    '../viewing.js': { registerViewingPlaybackCleanup: () => () => {} },
  })
  const scope = Vue.effectScope()
  scope.run(() => component.setup({ versionId: 1, kind: 'movie', title: '合成影片', preview: false },
    { expose() {}, emit: name => emitted.push(name) }))
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
  return { video, page, requests, emitted, saving, creation, close,
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
