import test from 'node:test'
import assert from 'node:assert/strict'
import { createOnlineArtworkQueue, onlineArtworkPath, ONLINE_ARTWORK_LIMIT } from '../src/onlineArtwork.js'

const source = 'src_' + '1'.repeat(32)
const path = index => `/api/online/catalog/artwork/${source}/${index.toString(16).padStart(32, '0')}`
const bytes = size => new Uint8Array(size).fill(1)
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
async function flush() { for (let i = 0; i < 30; i++) await Promise.resolve() }
function response({ status = 200, mime = 'image/jpeg', chunks = [bytes(8)], length, read } = {}) {
  const state = { cancelled: false, released: false, index: 0 }
  const headers = new Headers({ 'Content-Type': mime })
  if (length !== undefined) headers.set('Content-Length', length)
  return { status, ok: status >= 200 && status < 300, headers, state, body: {
    async cancel() { state.cancelled = true },
    getReader() { return {
      async read() { return read ? read() : state.index < chunks.length ? { value: chunks[state.index++], done: false } : { done: true } },
      async cancel() { state.cancelled = true },
      releaseLock() { state.released = true },
    } },
  } }
}
function setup(options = {}) {
  const created = [], revoked = [], events = []
  const queue = createOnlineArtworkQueue({
    createObjectURL(blob) { const url = `blob:fixture-${created.length}`; created.push({ blob, url }); return url },
    revokeObjectURL(url) { revoked.push(url) },
    dispatch(type, detail) { events.push({ type, detail }) },
    ...options,
  })
  return { queue, created, revoked, events }
}

test('artwork paths reject provider URLs, query strings and trailing control characters before fetching', () => {
  const { queue } = setup({ request: () => assert.fail('invalid address reached fetch') })
  assert.equal(onlineArtworkPath(path(1)), path(1))
  for (const value of ['https://untrusted.invalid/p.jpg', '//untrusted.invalid/p.jpg', path(1) + '?secret=hidden',
    path(1) + '#x', path(1) + '\n', path(1) + '/other', path(1).replace(source, 'src_' + '2'.repeat(31)), '', null]) {
    assert.equal(onlineArtworkPath(value), '')
    assert.throws(() => queue.enqueue(value), error => error.code === 'artwork_path_invalid')
  }
})

test('visible priority selects at most two requests and cancelled transports retain their real slots', async t => {
  const calls = [], reads = []
  const { queue } = setup({ request: (url, options) => {
    const pending = deferred(); reads.push(pending); calls.push({ url, options }); return pending.promise
  } })
  t.after(() => queue.clear())
  const far = queue.enqueue(path(1), { priority: 100 })
  const near = queue.enqueue(path(2), { priority: 0 })
  const middle = queue.enqueue(path(3), { priority: 50 })
  await flush()
  assert.deepEqual(calls.map(call => call.url), [path(2), path(3)])
  near.cancel()
  assert.equal(await near.promise, null)
  assert.equal(calls[0].options.signal.aborted, true)
  const urgent = queue.enqueue(path(4), { priority: -1 })
  await flush()
  assert.equal(calls.length, 2)
  reads[0].resolve(response())
  await flush()
  assert.deepEqual(calls.map(call => call.url), [path(2), path(3), path(4)])
  reads[1].resolve(response()); reads[2].resolve(response())
  await flush()
  assert.equal(calls[3].url, path(1))
  reads[3].resolve(response())
  await Promise.all([far.promise, middle.promise, urgent.promise])
  for (const call of calls) {
    assert.equal(call.options.mode, 'same-origin')
    assert.equal(call.options.credentials, 'same-origin')
    assert.equal(call.options.redirect, 'error')
    assert.equal(call.options.cache, 'no-store')
  }
})

test('twenty posters are read without sending the queued requests early', async t => {
  const calls = [], reads = []
  const { queue } = setup({ request: (url, options) => {
    const pending = deferred(); calls.push({ url, options }); reads.push(pending); return pending.promise
  } })
  t.after(() => queue.clear())
  const tasks = Array.from({ length: 20 }, (_, index) => queue.enqueue(path(index)))
  await flush()
  assert.equal(calls.length, 2)
  for (let index = 0; index < 20; index++) {
    assert.ok(queue.stats().active <= 2)
    reads[index].resolve(response())
    await flush()
    assert.equal(calls.length, Math.min(index + 3, 20))
  }
  const assets = await Promise.all(tasks.map(task => task.promise))
  assert.equal(assets.length, 20)
  assert.equal(queue.stats().active, 0)
})

test('cancelled queued work and scope cleanup never create late object URLs', async () => {
  const pending = deferred(), calls = []
  const { queue, created } = setup({ request: (url, options) => { calls.push({ url, options }); return pending.promise } })
  const first = queue.enqueue(path(1))
  const second = queue.enqueue(path(2))
  const queued = queue.enqueue(path(3))
  queued.cancel()
  await flush()
  assert.equal(calls.length, 2)
  queue.clear()
  assert.equal(await first.promise, null)
  assert.equal(await second.promise, null)
  pending.resolve(response())
  await flush()
  assert.equal(created.length, 0)
  assert.equal(queue.stats().active, 0)
  assert.ok(calls.every(call => call.options.signal.aborted))
})

test('body read enforces the 512 KiB streaming limit and rejects oversized length before allocating a URL', async () => {
  for (const result of [response({ chunks: [bytes(ONLINE_ARTWORK_LIMIT), bytes(1)] }), response({ length: String(ONLINE_ARTWORK_LIMIT + 1) })]) {
    const { queue, created } = setup({ request: async () => result })
    await assert.rejects(queue.enqueue(path(1)).promise, error => error.code === 'artwork_byte_limit' && error.retryable === false)
    await flush()
    assert.equal(created.length, 0)
    assert.equal(queue.stats().active, 0)
    if (result.state.index) assert.equal(result.state.cancelled, true)
  }
})

test('unsupported content and non-retryable HTTP failures stay terminal', async () => {
  for (const result of [response({ mime: 'image/svg+xml' }), response({ length: '500', chunks: [bytes(8)] }), response({ status: 404 })]) {
    const { queue, created } = setup({ request: async () => result })
    await assert.rejects(queue.enqueue(path(1)).promise, error => error.retryable === false)
    assert.equal(created.length, 0)
    queue.clear()
  }
  const { queue } = setup({ request: async () => response({ status: 503 }) })
  await assert.rejects(queue.enqueue(path(1)).promise, error => error.retryable && error.status === 503)
})

test('object URL byte and count budgets evict offscreen posters before visible ones', async () => {
  const evicted = []
  const { queue, revoked } = setup({ byteBudget: ONLINE_ARTWORK_LIMIT, objectLimit: 2, request: async () => response({ chunks: [bytes(ONLINE_ARTWORK_LIMIT / 2)] }) })
  const visible = queue.enqueue(path(1), { priority: 0, onEvict: () => evicted.push('visible') })
  await visible.promise
  const offscreen = queue.enqueue(path(2), { priority: Infinity, onEvict: () => evicted.push('offscreen') })
  const second = await offscreen.promise
  await queue.enqueue(path(3), { priority: 50 }).promise
  assert.deepEqual(evicted, ['offscreen'])
  assert.deepEqual(revoked, [second.url])
  assert.equal(queue.stats().retainedBytes, ONLINE_ARTWORK_LIMIT)
  assert.equal(queue.stats().objects, 2)
  queue.clear()
  assert.equal(queue.stats().retainedBytes, 0)
  assert.equal(queue.stats().objects, 0)
  assert.equal(revoked.length, 3)
})

test('a response authorization failure clears old posters and emits only a fixed viewing code', async () => {
  let auth = false
  const encoded = new TextEncoder().encode(JSON.stringify({ code: 'online_access_revoked', ignored: 'https://upstream.invalid/private' }))
  const { queue, created, revoked, events } = setup({ request: async () => {
    const value = auth ? response({ status: 401, mime: 'application/json', chunks: [encoded] }) : response()
    value.headers.set('X-Viewing-Generation', '8')
    return value
  } })
  await queue.enqueue(path(1)).promise
  auth = true
  await assert.rejects(queue.enqueue(path(2)).promise, error => error.status === 401 && error.code === 'online_access_revoked')
  assert.equal(created.length, 1)
  assert.deepEqual(revoked, [created[0].url])
  assert.ok(events.some(event => event.type === 'jzmedia:viewing-error' && event.detail.code === 'online_access_revoked'))
  assert.ok(events.some(event => event.type === 'jzmedia:viewing-generation' && event.detail.generation === 8))
  assert.equal(JSON.stringify(events).includes('upstream.invalid'), false)
})

test('oversized authorization bodies are bounded and cannot inject event codes', async () => {
  const { queue, events } = setup({ request: async () => response({ status: 401, chunks: [bytes(2049)] }) })
  await assert.rejects(queue.enqueue(path(1)).promise, error => error.status === 401 && error.code === 'artwork_http_error')
  assert.deepEqual(events, [{ type: 'jzmedia:unauthorized', detail: undefined }])
})

test('total request timeout covers stalled bodies and does not free a still-running reader', async () => {
  let timeout
  const body = deferred()
  const { queue, created } = setup({
    request: async () => response({ read: () => body.promise }),
    setTimer(callback, milliseconds) { assert.equal(milliseconds, 12000); timeout = callback; return 1 },
    clearTimer() {},
  })
  const first = queue.enqueue(path(1))
  await flush()
  timeout()
  await assert.rejects(first.promise, error => error.code === 'artwork_timeout' && error.retryable)
  assert.equal(queue.stats().active, 1)
  body.resolve({ done: true })
  await flush()
  assert.equal(queue.stats().active, 0)
  assert.equal(created.length, 0)
  queue.clear()
})
