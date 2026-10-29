import test from 'node:test'
import assert from 'node:assert/strict'
import { createOnlineRelatedClient, onlineRelatedError } from '../src/onlineRelated.js'
import { createOnlineCatalogClient } from '../src/onlineCatalog.js'

const SOURCE = 'src_' + '1'.repeat(32)
const result = (externalId = 'show-1', extra = {}) => ({ source: { id: SOURCE }, external_id: externalId,
  current_season: 2, status: 'ready', partial: false, series: [], related: [], name_related: [], ...extra })
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
function scheduler() {
  let lastDelay
  const tasks = new Map()
  return { tasks, get delay() { return lastDelay },
    setTimer(callback, delay) { const key = {}; tasks.set(key, callback); lastDelay = delay; return key },
    clearTimer(key) { tasks.delete(key) },
    run() { const callbacks = [...tasks.values()]; tasks.clear(); callbacks.forEach(callback => callback()) } }
}

test('related work waits briefly and sends stable identities with a bounded request timeout', async () => {
  const clock = scheduler(), calls = []
  const client = createOnlineRelatedClient({ setTimer: clock.setTimer, clearTimer: clock.clearTimer,
    request: async (path, options) => { calls.push({ path, options }); return result() } })
  const work = client.read(SOURCE, 'show-1')
  assert.equal(clock.delay, 300)
  assert.equal(calls.length, 0, 'A foreground detail gets the first opportunity to complete')
  clock.run()
  assert.deepEqual(await work, result())
  const url = new URL(calls[0].path, 'https://jzmedia.invalid')
  assert.equal(url.pathname, '/api/online/catalog/related')
  assert.deepEqual([...url.searchParams], [['source_id', SOURCE], ['external_id', 'show-1']])
  assert.equal(calls[0].options.timeout, 10000)
})

test('cancelling the delay settles its caller without making a metadata request', async () => {
  const clock = scheduler()
  let calls = 0
  const client = createOnlineRelatedClient({ setTimer: clock.setTimer, clearTimer: clock.clearTimer,
    request: async () => { calls++; return result() } })
  const work = client.read(SOURCE, 'show-1')
  client.cancel()
  assert.equal(await work, null)
  assert.equal(clock.tasks.size, 0)
  clock.run()
  assert.equal(calls, 0)
})

test('related cancellation is independent of a foreground detail and rejects late completions', async () => {
  const foreground = deferred(), background = deferred(), foregroundSignals = [], backgroundSignals = []
  const catalog = createOnlineCatalogClient({ request: (_path, options) => { foregroundSignals.push(options.signal); return foreground.promise } })
  const related = createOnlineRelatedClient({ delay: 0, request: (_path, options) => { backgroundSignals.push(options.signal); return background.promise } })
  const detail = catalog.detail(SOURCE, 'show-1'), extra = related.read(SOURCE, 'show-1')
  related.cancel()
  assert.equal(backgroundSignals[0].aborted, true)
  assert.equal(foregroundSignals[0].aborted, false)
  background.resolve(result()); foreground.resolve({ title: '详情' })
  assert.equal(await extra, null)
  assert.deepEqual(await detail, { title: '详情' })
})

test('replacement and closing ignore providers that finish after abort', async () => {
  const first = deferred(), second = deferred(), signals = []
  const client = createOnlineRelatedClient({ delay: 0, request: (_path, options) => {
    signals.push(options.signal); return signals.length === 1 ? first.promise : second.promise
  } })
  const old = client.read(SOURCE, 'show-1'), fresh = client.read(SOURCE, 'show-2')
  assert.equal(signals[0].aborted, true)
  first.resolve(result()); second.resolve(result('show-2'))
  assert.equal(await old, null)
  assert.equal((await fresh).external_id, 'show-2')
  const last = deferred()
  const closed = createOnlineRelatedClient({ delay: 0, request: () => last.promise })
  const work = closed.read(SOURCE, 'show-1')
  closed.close(); last.reject(new Error('upstream private error'))
  assert.equal(await work, null)
  assert.equal(await closed.read(SOURCE, 'show-1'), null)
})

test('related client refuses raw URLs, invalid IDs and responses belonging to another selection', async () => {
  let calls = 0
  const client = createOnlineRelatedClient({ delay: 0, request: async () => { calls++; return result('other') } })
  for (const [source, externalId] of [[SOURCE + '\n', 'show-1'], [SOURCE, 'https://untrusted.invalid/'],
    [SOURCE, 'show-1\n'], [SOURCE, 'a'.repeat(129)], [SOURCE, ['show-1']]]) {
    assert.throws(() => client.read(source, externalId), /身份无效/)
  }
  assert.equal(calls, 0)
  await assert.rejects(client.read(SOURCE, 'show-1'), error => error.code === 'provider_invalid_response')
  const wrongSource = createOnlineRelatedClient({ delay: 0, request: async () => result('show-1', { source: { id: 'src_' + '2'.repeat(32) } }) })
  await assert.rejects(wrongSource.read(SOURCE, 'show-1'), error => error.code === 'provider_invalid_response')
  const rawCard = createOnlineRelatedClient({ delay: 0, request: async () => result('show-1', {
    series: [{ external_id: 'https://untrusted.invalid/', title: '不可导航', season: 1, reason: 'inferred_season' }],
  }) })
  await assert.rejects(rawCard.read(SOURCE, 'show-1'), error => error.code === 'provider_invalid_response')
})

test('deferred and unsupported replies remain distinct and never retry themselves', async () => {
  let calls = 0
  const client = createOnlineRelatedClient({ delay: 0, request: async () => { calls++; return result('show-1', { status: calls === 1 ? 'deferred' : 'unsupported' }) } })
  assert.equal((await client.read(SOURCE, 'show-1')).status, 'deferred')
  await Promise.resolve()
  assert.equal(calls, 1)
  assert.equal((await client.read(SOURCE, 'show-1', { immediate: true })).status, 'unsupported')
})

test('partial results keep usable cards and older complete responses default to not partial', async () => {
  const card = { external_id: 'season-1', title: '另一季', season: 1, reason: 'inferred_season' }
  let calls = 0
  const client = createOnlineRelatedClient({ delay: 0, request: async () => { calls++; return result('show-1', { partial: true, series: [card] }) } })
  const value = await client.read(SOURCE, 'show-1')
  assert.equal(value.partial, true)
  assert.deepEqual(value.series, [card])
  await Promise.resolve()
  assert.equal(calls, 1, 'A partial response is not an automatic retry instruction')
  const legacy = result(); delete legacy.partial
  const olderServer = createOnlineRelatedClient({ delay: 0, request: async () => legacy })
  assert.equal((await olderServer.read(SOURCE, 'show-1')).partial, false)
})

test('partial is a strict ready-only boolean and cannot label an empty result as a partial success', async () => {
  for (const extra of [{ partial: null }, { partial: 'true' }, { partial: 1 },
    { partial: true, status: 'deferred' }, { partial: true, status: 'unsupported' }, { partial: true }]) {
    const client = createOnlineRelatedClient({ delay: 0, request: async () => result('show-1', extra) })
    await assert.rejects(client.read(SOURCE, 'show-1'), error => error.code === 'provider_invalid_response')
  }
})

test('recommendation errors use fixed known codes and never include private upstream messages', () => {
  const privateMessage = 'https://private.invalid/path?token=private-secret'
  for (const [code, expected] of [['provider_timeout', '超时'], ['provider_rate_limited', '限制查询次数'],
    ['provider_response_limit', '资料过大'], ['provider_entry_limit', '分集总数超过 2000 集'], ['provider_network_error', '无法连接'],
    ['provider_upstream_error', '暂时无法提供'], ['provider_invalid_response', '资料暂不受支持']]) {
    const value = onlineRelatedError({ code, message: privateMessage })
    assert.equal(value.code, code)
    assert.ok(value.message.includes(expected))
    assert.equal(JSON.stringify(value).includes(privateMessage), false)
  }
  for (const code of ['unknown-upstream-' + privateMessage, '__proto__', 'constructor', null]) {
    assert.deepEqual(onlineRelatedError({ code, message: privateMessage }), { code: 'unknown', message: '相关内容暂时无法读取。' })
  }
})
