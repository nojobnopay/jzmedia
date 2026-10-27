import test from 'node:test'
import assert from 'node:assert/strict'
import { OnlinePlaybackError, validateOnlineIdentity, validateOnlineDescriptor,
  onlineIdentityKey, createOnlinePlaybackClient, createOnlinePlaybackController,
  createOnlineProgressMemory, canRecoverOnlineStartupGap } from '../src/onlinePlayback.js'

const NOW = 1700000000000
const SID_A = 'a'.repeat(43)
const SID_B = 'b'.repeat(43)
const MOVIE = { source_id: 'src_' + '1'.repeat(32), item_id: 'itm_' + '2'.repeat(32), kind: 'movie' }
const EPISODE = { ...MOVIE, kind: 'episode', episode_id: 'ep_' + '3'.repeat(32) }
const OTHER_EPISODE = { ...EPISODE, episode_id: 'ep_' + '4'.repeat(32) }

function descriptor(sid = SID_A, identity = MOVIE, changes = {}) {
  return { identity: { ...identity, episode_id: identity.episode_id || null }, session_id: sid,
    media_url: `/api/online/sessions/${sid}/media`, transport: 'hls', processing: 'passthrough',
    seekable: true, duration_seconds: 24, time_offset_seconds: 0,
    expires_at: NOW / 1000 + 7200, ...changes }
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const flush = async () => { for (let index = 0; index < 8; index++) await Promise.resolve() }

function client(options = {}) {
  return createOnlinePlaybackClient({ request: async () => descriptor(),
    releaseRequest: async () => ({ status: 204 }), now: () => NOW, ...options })
}

test('identity is stable, exact and detached from caller metadata', () => {
  const original = { ...EPISODE }
  const validated = validateOnlineIdentity(original)
  original.episode_id = OTHER_EPISODE.episode_id
  assert.equal(validated.episode_id, EPISODE.episode_id)
  assert.equal(Object.isFrozen(validated), true)
  assert.notEqual(onlineIdentityKey(EPISODE), onlineIdentityKey(OTHER_EPISODE))
  assert.deepEqual(validateOnlineIdentity(MOVIE), { ...MOVIE, episode_id: null })
})

for (const [name, value] of Object.entries({
  null: null, array: [], localId: { ...MOVIE, item_id: 12 },
  stringObject: { ...MOVIE, source_id: Object(MOVIE.source_id) },
  newlineId: { ...MOVIE, source_id: MOVIE.source_id + '\n' },
  missingEpisode: { ...MOVIE, kind: 'episode' },
  movieWithEpisode: { ...MOVIE, episode_id: EPISODE.episode_id },
  externalUrl: { ...MOVIE, url: 'https://external.invalid/?secret=private-secret' },
  processing: { ...MOVIE, processing: 'transcode' },
})) test(`invalid ${name} identity is rejected without request or input echo`, async () => {
  let requested = false
  const player = client({ request: () => { requested = true } })
  await assert.rejects(player.create(value), error => error.code === 'invalid_online_identity'
    && !error.message.includes('private-secret'))
  assert.equal(requested, false)
})

test('MP4 and HLS descriptors preserve an unchanged original timeline', () => {
  for (const transport of ['mp4', 'hls']) {
    const validated = validateOnlineDescriptor(descriptor(SID_A, MOVIE, { transport }), MOVIE, { now: () => NOW })
    assert.equal(validated.transport, transport)
    assert.equal(validated.processing, 'passthrough')
    assert.equal(validated.time_offset_seconds, 0)
    assert.equal(Object.isFrozen(validated), true)
    assert.equal(Object.isFrozen(validated.identity), true)
  }
})

for (const [name, change] of Object.entries({
  absolute: { media_url: `https://external.invalid/api/online/sessions/${SID_A}/media` },
  protocolRelative: { media_url: `//external.invalid/api/online/sessions/${SID_A}/media` },
  query: { media_url: `/api/online/sessions/${SID_A}/media?secret=private-secret` },
  localPath: { media_url: '/api/stream/file/12' },
  wrongSessionPath: { media_url: `/api/online/sessions/${SID_B}/media` },
  sidTraversal: { session_id: '../private-secret' },
  sidNewline: { session_id: SID_A + '\n', media_url: `/api/online/sessions/${SID_A}\n/media` },
  localMethod: { method: 'video_transcode' }, processing: { processing: 'transcode' },
  offset: { time_offset_seconds: 1800 }, unknownTransport: { transport: 'dash' },
  nonbooleanSeek: { seekable: 'true' }, invalidDuration: { duration_seconds: Infinity },
  negativeDuration: { duration_seconds: -1 }, invalidExpiry: { expires_at: NaN },
  unsafeExpiry: { expires_at: Number.MAX_SAFE_INTEGER },
  mismatch: { identity: { ...MOVIE, item_id: 'itm_' + '5'.repeat(32) } },
})) test(`${name} descriptor cannot enter an online playback source`, () => {
  assert.throws(() => validateOnlineDescriptor(descriptor(SID_A, MOVIE, change), MOVIE, { now: () => NOW }),
    error => error.code === 'invalid_online_descriptor' && !error.message.includes('private-secret'))
})

test('expired descriptors have a distinct fixed error and unknown duration remains valid', () => {
  assert.throws(() => validateOnlineDescriptor(descriptor(SID_A, MOVIE, { expires_at: NOW / 1000 }), MOVIE,
    { now: () => NOW }), error => error.code === 'online_session_expired')
  assert.equal(validateOnlineDescriptor(descriptor(SID_A, MOVIE, { duration_seconds: null }), MOVIE,
    { now: () => NOW }).duration_seconds, null)
})

test('creation posts only stable identity and rejected descriptors release their safe session', async () => {
  const calls = [], releases = []
  const player = client({ request: async (path, options) => {
    calls.push({ path, options })
    return descriptor(SID_A, EPISODE, { media_url: 'https://external.invalid/?secret=private-secret' })
  }, releaseRequest: async (path, options) => { releases.push({ path, options }); return { status: 204 } } })
  await assert.rejects(player.create(EPISODE), error => error.code === 'invalid_online_descriptor')
  assert.equal(calls[0].path, '/api/online/sessions')
  assert.deepEqual(JSON.parse(calls[0].options.body), EPISODE)
  assert.equal(calls[0].options.method, 'POST')
  assert.deepEqual(releases, [{ path: `/api/online/sessions/${SID_A}`,
    options: { method: 'DELETE', keepalive: true, timeout: 15000 } }])
  assert.equal(calls.some(call => call.path.startsWith('/api/stream')), false)
})

test('release is shared while in flight, treats absent leases as done and retries failed delivery', async () => {
  const pending = deferred()
  let attempts = 0
  const player = client({ releaseRequest: () => { attempts++; return attempts === 1 ? pending.promise : { status: 404 } } })
  await player.create(MOVIE)
  const first = player.release(SID_A)
  assert.equal(first, player.release(SID_A))
  assert.equal(attempts, 1)
  pending.reject(new Error('offline private-secret'))
  assert.equal(await first, false)
  assert.equal(await player.release(SID_A), true)
  assert.equal(attempts, 2)
  await player.release(SID_A)
  assert.equal(attempts, 2)
})

test('a client never releases another player session or an arbitrary path', async () => {
  const calls = []
  const first = client({ releaseRequest: async path => { calls.push(path); return { status: 204 } } })
  const second = client({ request: async () => descriptor(SID_B) })
  await first.create(MOVIE)
  await second.create(MOVIE)
  await first.release(SID_B)
  await first.release('../private-secret')
  assert.deepEqual(calls, [])
  await first.close()
  assert.deepEqual(calls, [`/api/online/sessions/${SID_A}`])
  await second.close()
})

test('cancelled late creation is released instead of being mounted', async () => {
  const response = deferred(), deleted = []
  const player = client({ request: () => response.promise,
    releaseRequest: async path => { deleted.push(path); return { status: 204 } } })
  const abort = new AbortController()
  const pending = player.create(MOVIE, { signal: abort.signal }).catch(error => error)
  abort.abort()
  response.resolve(descriptor())
  assert.equal((await pending).code, 'online_cancelled')
  assert.deepEqual(deleted, [`/api/online/sessions/${SID_A}`])
})

test('replacement keeps only the newest generation and closes an older late response', async () => {
  const old = deferred(), next = deferred(), creates = [], deleted = []
  const player = client({ request: (_path, options) => {
    creates.push(options)
    return creates.length === 1 ? old.promise : next.promise
  }, releaseRequest: async path => { deleted.push(path); return { status: 204 } } })
  const controller = createOnlinePlaybackController({ client: player })
  const previous = controller.begin(MOVIE).catch(error => error)
  await flush()
  const replacement = controller.begin(EPISODE)
  await flush()
  assert.equal(creates[0].signal.aborted, true)
  next.resolve(descriptor(SID_B, EPISODE))
  assert.equal((await replacement).session_id, SID_B)
  old.resolve(descriptor(SID_A))
  assert.equal((await previous).code, 'online_cancelled')
  assert.equal(controller.current().session_id, SID_B)
  assert.deepEqual(deleted, [`/api/online/sessions/${SID_A}`])
  await controller.close()
  assert.deepEqual(deleted, [`/api/online/sessions/${SID_A}`, `/api/online/sessions/${SID_B}`])
})

test('stop can restore playback after pagehide while close blocks later creation', async () => {
  let count = 0
  const deleted = []
  const player = client({ request: async () => descriptor(++count === 1 ? SID_A : SID_B),
    releaseRequest: async path => { deleted.push(path); return { status: 204 } } })
  const controller = createOnlinePlaybackController({ client: player })
  await controller.begin(MOVIE)
  await controller.stop()
  assert.equal(controller.current(), null)
  assert.equal((await controller.begin(MOVIE)).session_id, SID_B)
  await controller.close()
  await assert.rejects(controller.begin(MOVIE), error => error.code === 'online_player_closed')
  assert.deepEqual(deleted, [`/api/online/sessions/${SID_A}`, `/api/online/sessions/${SID_B}`])
})

test('closing a pending controller reclaims a response that arrives after disposal', async () => {
  const late = deferred(), deleted = []
  const controller = createOnlinePlaybackController({ client: client({ request: () => late.promise,
    releaseRequest: async path => { deleted.push(path); return { status: 204 } } }) })
  const creation = controller.begin(MOVIE).catch(error => error)
  await flush()
  await controller.close()
  late.resolve(descriptor())
  assert.equal((await creation).code, 'online_cancelled')
  assert.equal(controller.current(), null)
  assert.deepEqual(deleted, [`/api/online/sessions/${SID_A}`])
})

test('the default 204 deletion keeps same-origin credentials and the write token without JSON parsing', async t => {
  const calls = []
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    calls.push({ path, options })
    return { status: 204, json: () => assert.fail('204 has no JSON body') }
  })
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true,
    value: { getItem: () => 'fixture-write-token' } })
  t.after(() => previousStorage ? Object.defineProperty(globalThis, 'localStorage', previousStorage)
    : delete globalThis.localStorage)
  const player = createOnlinePlaybackClient({ request: async () => descriptor(), now: () => NOW })
  await player.create(MOVIE)
  assert.equal(await player.release(SID_A), true)
  assert.equal(calls[0].path, `/api/online/sessions/${SID_A}`)
  assert.equal(calls[0].options.keepalive, true)
  assert.equal(calls[0].options.credentials, 'same-origin')
  assert.equal(calls[0].options.headers['X-Api-Token'], 'fixture-write-token')
  assert.equal(calls[0].options.signal.aborted, false)
})

test('memory progress stays scoped by source, item and episode without persistent or local media writes', () => {
  const memory = createOnlineProgressMemory({ maxEntries: 2 })
  memory.save(EPISODE, { position: 12.5, duration: 24, paused: true })
  assert.equal(memory.get(OTHER_EPISODE), null)
  assert.deepEqual(memory.get(EPISODE), { position: 12.5, duration: 24, paused: true })
  assert.equal(Object.isFrozen(memory.get(EPISODE)), true)
  memory.save(EPISODE, { position: NaN })
  assert.equal(memory.get(EPISODE).position, 12.5)
  memory.save(OTHER_EPISODE, { position: 50, duration: 24 })
  assert.equal(memory.get(OTHER_EPISODE).position, 24)
  memory.save(MOVIE, { position: 3, duration: Infinity })
  assert.equal(memory.get(EPISODE), null)
  assert.equal(memory.get(MOVIE).duration, null)
  memory.clearAll()
  assert.equal(memory.get(MOVIE), null)
})

const GAP = { type: 'mediaError', detail: 'bufferSeekOverHole', fatal: false,
  beforeFirstFrame: true, priorRecoveries: 0, mediaTime: .195778, seekFrom: 0, seekTo: .195778 }

test('only one bounded initial HLS alignment is recoverable without changing the original timeline', () => {
  assert.equal(canRecoverOnlineStartupGap(GAP), true)
  assert.equal(canRecoverOnlineStartupGap({ ...GAP, mediaTime: .5, seekTo: .5 }), true)
  for (const change of [{ priorRecoveries: 1 }, { beforeFirstFrame: false }, { fatal: true },
    { detail: 'bufferStalledError' }, { type: 'networkError' }, { seekFrom: -.01 },
    { mediaTime: .6, seekTo: .6 }, { mediaTime: null }, { seekTo: Infinity }, { mediaTime: .4 }]) {
    assert.equal(canRecoverOnlineStartupGap({ ...GAP, ...change }), false)
  }
  assert.equal(canRecoverOnlineStartupGap(null), false)
  assert.equal(new OnlinePlaybackError('invalid_online_descriptor').message.includes('http'), false)
})
