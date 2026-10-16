import test from 'node:test'
import assert from 'node:assert/strict'
import { createSessionReleaser, createPlaybackHeartbeat, createPlaybackPageLifecycle,
  createExpiredSessionRecovery, playbackHeartbeatPayload } from '../src/playbackLifecycle.js'
import { createMp4Queue } from '../src/mp4Chunks.js'

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve() }
function pageEvents() {
  const listeners = new Map()
  return { listeners,
    addEventListener(name, fn) { listeners.set(name, fn) },
    removeEventListener(name, fn) { if (listeners.get(name) === fn) listeners.delete(name) },
    emit(name, event = {}) { return listeners.get(name)?.(event) } }
}

test('releasing a lease is immediate, idempotent and keeps authentication requests alive on exit', async () => {
  const calls = [], delivery = deferred()
  const release = createSessionReleaser((path, options) => { calls.push({ path, options }); return delivery.promise })
  const first = release('mine')
  assert.equal(first, release('mine'))
  assert.equal(calls.length, 1)
  assert.equal(calls[0].path, '/api/stream/sessions/mine')
  assert.deepEqual(calls[0].options, { method: 'DELETE', keepalive: true, timeout: 15000 })
  await release(null)
  delivery.reject(new Error('offline'))
  await first
  await release('mine')
  assert.equal(calls.length, 1)
})

test('page exit stops current and prefetched leases while progress saving is still pending', async () => {
  const saving = deferred(), creation = deferred(), deleted = []
  let generation = 1, aborted = false, captures = 0
  const release = createSessionReleaser(path => { deleted.push(path.split('/').at(-1)); return Promise.resolve() })
  const queue = createMp4Queue({
    create: (_body, signal) => { signal.addEventListener('abort', () => { aborted = true }); return creation.promise },
    close: release, isCurrent: gen => gen === generation,
  })
  queue.prepare(90, {}, 1)
  const life = createPlaybackPageLifecycle({
    capture: () => { captures++; return { position: 61.5, paused: false } },
    save: () => saving.promise,
    stop: () => { generation++; queue.clear(); return release('current') }, restore: () => {},
  })
  const leaving = life.leave()
  assert.equal(life.leave(), leaving)
  assert.deepEqual(deleted, ['current'])
  assert.equal(captures, 1)
  assert.equal(aborted, true)
  creation.resolve({ session_id: 'late-prefetch' })
  await flush()
  assert.deepEqual(deleted, ['current', 'late-prefetch'])
  saving.resolve()
  await leaving
})

test('pagehide releases once and bfcache restores source position and paused intent', async () => {
  const events = pageEvents(), restored = [], calls = []
  const life = createPlaybackPageLifecycle({ capture: () => ({ position: 1842.25, paused: true }),
    save: snapshot => { calls.push(['save', snapshot.position]) },
    stop: () => { calls.push(['stop']) }, restore: snapshot => restored.push(snapshot) })
  life.bind(events)
  life.bind(events)
  assert.equal(events.listeners.size, 2)
  assert.equal(events.listeners.has('visibilitychange'), false, 'Background playback is not an exit')
  events.emit('pagehide', { persisted: true })
  events.emit('pagehide', { persisted: true })
  assert.deepEqual(calls, [['save', 1842.25], ['stop']])
  events.emit('pageshow', { persisted: false })
  await flush()
  assert.equal(restored.length, 0)
  events.emit('pageshow', { persisted: true })
  events.emit('pageshow', { persisted: true })
  await flush()
  assert.deepEqual(restored, [{ position: 1842.25, paused: true }])
  events.emit('pagehide')
  assert.equal(calls.filter(([name]) => name === 'stop').length, 2)
  life.unbind()
  assert.equal(events.listeners.size, 0)
})

test('heartbeat reports absolute source position, pause and rate without overlapping slow requests', async () => {
  const pending = deferred(), calls = [], updates = []
  let sid = 'mine', tick, unscheduled = false
  let state = { position: 1805.75, paused: false, playbackRate: 2 }
  const heartbeat = createPlaybackHeartbeat({
    request: (path, options) => { calls.push({ path, options }); return calls.length === 1 ? pending.promise : Promise.resolve({ complete: false }) },
    session: () => sid, state: () => state, enabled: () => true,
    onUpdate: data => updates.push(data), onExpired: () => assert.fail('lease exists'),
    schedule: (fn, milliseconds) => { tick = fn; assert.equal(milliseconds, 10000); return 0 },
    unschedule: handle => { assert.equal(handle, 0); unscheduled = true },
  })
  heartbeat.start()
  assert.equal(calls.length, 1, 'Claim immediately rather than waiting for the first timer')
  assert.deepEqual(JSON.parse(calls[0].options.body), { position: 1805.75, paused: false, playback_rate: 2 })
  await tick(); await heartbeat.send()
  assert.equal(calls.length, 1)
  pending.resolve({ running: true, buffer_paused: true, complete: false })
  await flush()
  assert.equal(updates.length, 1)
  state = { position: 1820, paused: true, playbackRate: 1.5 }
  await tick()
  assert.deepEqual(JSON.parse(calls[1].options.body), { position: 1820, paused: true, playback_rate: 1.5 })
  sid = null
  heartbeat.stop()
  assert.equal(unscheduled, true)
  await tick()
  assert.equal(calls.length, 2)
})

test('late heartbeat 404 cannot recover a replaced or closed player', async () => {
  const pending = deferred(), expired = []
  let sid = 'old', signal
  const heartbeat = createPlaybackHeartbeat({
    request: (_path, options) => { signal = options.signal; return pending.promise },
    session: () => sid, state: () => ({ position: 50, paused: false, playbackRate: 1 }), enabled: () => true,
    onExpired: value => expired.push(value), schedule: () => 1, unschedule: () => {},
  })
  heartbeat.start()
  sid = 'other'
  heartbeat.stop()
  assert.equal(signal.aborted, true)
  pending.reject(Object.assign(new Error('gone'), { status: 404 }))
  await flush()
  assert.deepEqual(expired, [])
})

test('only a current missing lease requests recovery; ordinary connectivity errors keep retrying', async () => {
  const expired = []
  let status = 503
  const heartbeat = createPlaybackHeartbeat({ request: async () => { throw { status } },
    session: () => 'mine', state: () => ({}), enabled: () => true,
    onExpired: sid => expired.push(sid), schedule: () => 1, unschedule: () => {} })
  await heartbeat.send()
  assert.deepEqual(expired, [])
  status = 404
  await heartbeat.send()
  assert.deepEqual(expired, ['mine'])
  assert.deepEqual(playbackHeartbeatPayload({ position: Infinity, playbackRate: NaN, paused: true }),
    { position: 0, paused: true, playback_rate: 1 })
})

test('expired-session recovery is shared, bounded and can be reset by an explicit retry', async () => {
  const pending = deferred()
  let sid = 'first', now = 0, recovered = 0, failed = 0
  const recovery = createExpiredSessionRecovery({ current: () => sid, now: () => now,
    recover: () => { recovered++; return pending.promise }, failed: () => { failed++ } })
  const result = recovery.expired('first')
  assert.equal(recovery.expired('first'), result)
  await flush()
  assert.equal(recovered, 1)
  pending.resolve(); await result
  sid = 'new'
  await recovery.expired('first')
  assert.equal(failed, 0)
  await recovery.expired('new')
  assert.equal(failed, 1, 'Repeated immediate failure must not create a reload loop')
  assert.equal(recovered, 1)
  now += 30001
  await recovery.expired('new')
  assert.equal(recovered, 2)
  now += 30001
  await recovery.expired('new')
  assert.equal(recovered, 2)
  assert.equal(failed, 2)
  recovery.reset()
  await recovery.expired('new')
  assert.equal(recovered, 3)
})
