import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import * as playback from '../src/onlinePlayback.js'
import { createOnlineProgressWriter } from '../src/onlineState.js'
import { loadSfc } from './helpers/loadSfc.js'
import { deferred, flush } from './helpers/renderHarness.js'

const IDENTITY = { source_id: 'src_' + '1'.repeat(32), item_id: 'itm_' + '2'.repeat(32),
  kind: 'movie', line_id: 'ln_' + '3'.repeat(32) }
const SCOPE = 'a'.repeat(64), WRITER = 'w'.repeat(43)
function eventTarget() {
  const handlers = new Map()
  return {
    addEventListener(name, fn) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(fn) },
    removeEventListener(name, fn) { handlers.get(name)?.delete(fn) },
    dispatch(name, event = {}) { for (const fn of handlers.get(name) || []) fn(event) },
  }
}

// Run the real SFC setup/load/teardown and real progress writer with a media
// element double. Native decoding itself is covered by the browser fixture.
async function player(t, claimResponse, { sessionProblem = null } = {}) {
  const mounted = [], beforeUnmount = [], unmounted = [], refs = [], requests = [], notices = []
  const page = eventTarget()
  const globals = new Map(['window', 'document'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
  Object.defineProperty(globalThis, 'window', { configurable: true, value: page })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: eventTarget() })
  let playCalls = 0, sessionCount = 0, frameCallback
  const video = Vue.markRaw({ currentTime: 0, paused: true, readyState: 0,
    buffered: { length: 0 }, volume: 1, muted: false, src: '',
    pause() { this.paused = true },
    play() { this.paused = false; playCalls++; return Promise.resolve() },
    load() { this.currentTime = 0 }, removeAttribute(name) { if (name === 'src') this.src = '' },
    requestVideoFrameCallback(callback) { frameCallback = callback; return 1 },
    cancelVideoFrameCallback() { frameCallback = null },
  })
  const request = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body)
    requests.push({ path, options, body })
    if (path === '/api/viewing/session') return {}
    if (path === '/api/online/sessions') {
      if (sessionProblem) throw sessionProblem
      const sessionId = String(++sessionCount).repeat(43)
      return { identity: IDENTITY, session_id: sessionId, media_url: `/api/online/sessions/${sessionId}/media`,
        transport: 'mp4', processing: 'passthrough', seekable: true, duration_seconds: 24,
        time_offset_seconds: 0, expires_at: Math.floor(Date.now() / 1000) + 7200 }
    }
    if (path === '/api/online/state/progress/claim') return claimResponse(body)
    if (path === '/api/online/state/progress') return {
      scope_token: SCOPE, progress: { identity: IDENTITY, ...body, updated_at: 100 },
    }
    throw new Error(`Unexpected fixture request: ${path}`)
  }
  const writer = createOnlineProgressWriter({ identity: IDENTITY, scopeToken: SCOPE, request,
    onNotice: message => notices.push(message) })
  const component = await loadSfc(new URL('../src/components/OnlinePlayerModal.vue', import.meta.url), {
    vue: { ...Vue, ref(value) { const result = Vue.ref(value); refs.push(result); return result },
      onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => beforeUnmount.push(fn), onUnmounted: fn => unmounted.push(fn) },
    '../api.js': { api: request },
    '../onlinePlayback.js': { ...playback, createOnlinePlaybackClient: ({ request: api }) => playback.createOnlinePlaybackClient({
      request: api, releaseRequest: async (path, options) => { requests.push({ path, options }); return { status: 204 } },
    }) },
    '../viewing.js': { registerViewingPlaybackCleanup: () => () => {}, refreshViewing: async () => {} },
    '../useFocusTrap.js': { useFocusTrap: () => ({ isTop: () => true }) },
  })
  const scope = Vue.effectScope()
  let render, exposed
  scope.run(() => {
    render = component.setup({ onlineIdentity: IDENTITY, title: '隔离媒体', initialPosition: 0,
      prepareSession: value => writer.session(value) }, {
      expose: value => { exposed = value },
      emit: (name, value) => { if (name === 'progress') writer.update(value) },
    })
  })
  refs[2].value = video
  let closed = false
  function close() {
    if (closed) return
    closed = true
    for (const callback of beforeUnmount) callback()
    for (const callback of unmounted) callback()
    scope.stop()
  }
  t.after(async () => {
    close()
    await writer.close()
    for (const [name, descriptor] of globals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else delete globalThis[name]
    }
  })
  for (const callback of mounted) callback()
  await flush()
  function videoNode() {
    function visit(node) {
      if (!node || typeof node !== 'object') return null
      if (node.type === 'video') return node
      for (const child of Array.isArray(node.children) ? node.children : []) {
        const found = visit(child)
        if (found) return found
      }
      return null
    }
    return visit(render({}, []))
  }
  return { video, page, writer, requests, notices, close, snapshot: exposed.snapshot,
    text() {
      const text = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(text).join('')
        : node && typeof node === 'object' ? text(node.children) : ''
      return text(render({}, []))
    },
    playCalls: () => playCalls,
    frame() { frameCallback?.() },
    timeUpdate() { videoNode().props.onTimeupdate({ currentTarget: video }) },
  }
}
const grant = (revision = 1) => ({ identity: IDENTITY, writer_id: WRITER, writer_revision: revision, scope_token: SCOPE })

test('the actual player distinguishes entry-count limits without exposing an upstream playback URL', async t => {
  const privateMessage = 'https://private.invalid/movie.m3u8?token=private-secret'
  const ui = await player(t, () => grant(), { sessionProblem: Object.assign(new Error(privateMessage), { code: 'provider_entry_limit' }) })
  assert.match(ui.text(), /分集总数超过 2000 集/)
  assert.doesNotMatch(ui.text(), /资料过大|private-secret|private\.invalid/)
  assert.equal(ui.video.src, '')
  assert.equal(ui.playCalls(), 0)
  assert.equal(ui.snapshot().firstFrame, false)
})

test('actual online player waits for its writer before attaching media or observing a first frame', async t => {
  const claim = deferred()
  const ui = await player(t, () => claim.promise)
  assert.equal(ui.requests.filter(value => value.path.endsWith('/claim')).length, 1)
  assert.equal(ui.video.src, '')
  assert.equal(ui.playCalls(), 0)
  assert.equal(ui.snapshot().firstFrame, false)
  claim.resolve(grant())
  await flush()
  assert.match(ui.video.src, /\/media$/)
  assert.equal(ui.playCalls(), 1)
  ui.frame(); ui.video.currentTime = 2.5; ui.timeUpdate()
  await ui.writer.flush()
  const saved = ui.requests.filter(value => value.path === '/api/online/state/progress')
  assert.equal(saved.length, 1)
  assert.equal(saved[0].body.position, 2.5)
})

for (const exit of ['pagehide', 'unmount']) {
  test(`online ${exit} releases immediately during a delayed claim and never starts the late media`, async t => {
    const claim = deferred()
    const ui = await player(t, () => claim.promise)
    if (exit === 'pagehide') ui.page.dispatch('pagehide')
    else ui.close()
    assert.equal(ui.requests.filter(value => value.options.method === 'DELETE').length, 1)
    claim.resolve(grant())
    await flush()
    assert.equal(ui.video.src, '')
    assert.equal(ui.playCalls(), 0)
    assert.equal(ui.requests.filter(value => value.path === '/api/online/state/progress').length, 0)
  })
}

test('a rejected online writer claim still permits media playback with a saving notice', async t => {
  const ui = await player(t, async () => { throw Object.assign(new Error('isolated failure'), { code: 'online_state_unavailable' }) })
  assert.match(ui.video.src, /\/media$/)
  assert.equal(ui.playCalls(), 1)
  assert.equal(ui.writer.snapshot().disabled, true)
  assert.equal(ui.notices.length, 1)
  assert.deepEqual(ui.snapshot().errors, [])
})

test('bfcache restoration prepares the renewed session before attaching its media', async t => {
  const renewal = deferred()
  let count = 0
  const ui = await player(t, () => ++count === 1 ? grant() : renewal.promise)
  assert.equal(ui.playCalls(), 1)
  ui.page.dispatch('pagehide', { persisted: true })
  ui.page.dispatch('pageshow', { persisted: true })
  await flush()
  assert.equal(count, 2)
  assert.equal(ui.video.src, '')
  assert.equal(ui.playCalls(), 1)
  renewal.resolve(grant(2))
  await flush()
  assert.match(ui.video.src, /\/sessions\/2+\/media$/)
  assert.equal(ui.playCalls(), 2)
})
