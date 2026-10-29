import test from 'node:test'
import assert from 'node:assert/strict'
import { createOnlineCatalogClient, choiceNetworkRule, networkRuleChoice, onlineCatalogError, onlineRouteTarget } from '../src/onlineCatalog.js'
import { onlineIdentityKey, validateOnlineIdentity, validateOnlineDescriptor, createOnlineProgressMemory } from '../src/onlinePlayback.js'

const SOURCE = 'src_' + '1'.repeat(32)
const MOVIE = { source_id: SOURCE, item_id: 'itm_' + '2'.repeat(32), kind: 'movie' }
const LINE_A = 'ln_' + 'a'.repeat(32), LINE_B = 'ln_' + 'b'.repeat(32)
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

test('online return links accept only one stable source and provider item identity', () => {
  assert.deepEqual(onlineRouteTarget({ source: SOURCE, item: 'show_12-A' }), { sourceId: SOURCE, externalId: 'show_12-A' })
  assert.deepEqual(onlineRouteTarget({ source: SOURCE }), { sourceId: SOURCE, externalId: '' })
  for (const value of [{}, { source: [SOURCE], item: 'a' }, { source: SOURCE, item: ['a'] },
    { source: SOURCE + '\n', item: 'a' }, { source: SOURCE, item: 'a\n' },
    { source: SOURCE, item: 'https://untrusted.invalid/private' }]) assert.equal(onlineRouteTarget(value), null)
})

test('catalogue requests encode data only, never receive a target URL from the caller', async () => {
  const calls = []
  const client = createOnlineCatalogClient({ request: async (path, options) => { calls.push({ path, options }); return {} } })
  await client.search(SOURCE, '片名 & ids=other', 2)
  const search = new URL(calls[0].path, 'https://jzmedia.invalid')
  assert.equal(search.pathname, '/api/online/catalog/search')
  assert.equal(search.searchParams.get('q'), '片名 & ids=other')
  assert.equal(search.searchParams.get('source_id'), SOURCE)
  assert.equal(search.searchParams.get('page'), '2')
  assert.equal(calls[0].options.timeout, 35000)
  await client.detail(SOURCE, 'vod_2006-A')
  assert.equal(new URL(calls[1].path, 'https://jzmedia.invalid').searchParams.get('external_id'), 'vod_2006-A')
  assert.throws(() => client.search(SOURCE + '\n', 'x'), /身份无效/)
  assert.throws(() => client.search(SOURCE, 'x', 1001), /条件无效/)
  assert.throws(() => client.detail(SOURCE, 'id\nother'), /身份无效/)
  assert.throws(() => client.detail(SOURCE, 'id & secret=private'), /身份无效/)
  assert.throws(() => client.detail(SOURCE, 'a'.repeat(129)), /身份无效/)
  assert.throws(() => client.detail(SOURCE, 'id\n'), /身份无效/)
})

test('a replacement query cancels its predecessor and ignores its late success', async () => {
  const first = deferred(), second = deferred(), signals = []
  const client = createOnlineCatalogClient({ request: (_path, options) => { signals.push(options.signal); return signals.length === 1 ? first.promise : second.promise } })
  const old = client.search(SOURCE, 'old')
  const current = client.search(SOURCE, 'new')
  assert.equal(signals[0].aborted, true)
  first.resolve({ items: ['old'] }); second.resolve({ items: ['new'] })
  assert.equal(await old, null)
  assert.deepEqual(await current, { items: ['new'] })
})

test('leaving the catalogue ignores a late provider failure and forbids new work', async () => {
  const pending = deferred()
  let calls = 0
  const client = createOnlineCatalogClient({ request: () => { calls++; return pending.promise } })
  const work = client.detail(SOURCE, 'movie-1')
  client.close(); pending.reject(new Error('signed URL and secret must not enter the page'))
  assert.equal(await work, null)
  assert.equal(await client.sources(), null)
  assert.equal(calls, 1)
})

test('network form preserves existing direct-profile and proxy references without guessing a fallback', () => {
  const direct = { id: 'np_' + '1'.repeat(32), mode: 'direct' }
  const proxy = { id: 'np_' + '2'.repeat(32), mode: 'proxy' }
  for (const profile of [direct, proxy]) {
    const rule = { mode: profile.mode, profile_id: profile.id }
    assert.deepEqual(choiceNetworkRule(networkRuleChoice(rule), [direct, proxy]), rule)
  }
  assert.deepEqual(choiceNetworkRule('inherit'), { mode: 'inherit', profile_id: null })
  assert.deepEqual(choiceNetworkRule('direct'), { mode: 'direct', profile_id: null })
  assert.throws(() => choiceNetworkRule('profile:missing', [proxy]), /有效的网络档案/)
  assert.equal(onlineCatalogError({ code: 'network_timeout', message: 'private-secret' }).includes('private-secret'), false)
  for (const code of ['invalid_provider_response', 'provider_catalog_busy', 'provider_timeout', 'provider_rate_limited',
    'provider_auth_failed', 'provider_media_unsupported', 'provider_line_required', 'online_source_changed']) {
    const text = onlineCatalogError({ code })
    assert.notEqual(text, onlineCatalogError({ code: 'unknown' }))
  }
})

test('movie lines have distinct stable identities and progress without accepting URLs as line selectors', () => {
  const first = { ...MOVIE, line_id: LINE_A }, second = { ...MOVIE, line_id: LINE_B }
  assert.equal(validateOnlineIdentity(first).line_id, LINE_A)
  assert.notEqual(onlineIdentityKey(first), onlineIdentityKey(second))
  assert.notEqual(onlineIdentityKey(first), onlineIdentityKey(MOVIE))
  const memory = createOnlineProgressMemory()
  memory.save(first, { position: 18, duration: 24, paused: true })
  assert.equal(memory.get(second), null)
  for (const line_id of ['https://media.invalid/video.m3u8', LINE_A + '\n', 1, '']) {
    assert.throws(() => validateOnlineIdentity({ ...MOVIE, line_id }), error => error.code === 'invalid_online_identity')
  }
  const session = 'a'.repeat(43)
  const descriptor = { identity: second, session_id: session, media_url: `/api/online/sessions/${session}/media`,
    transport: 'mp4', processing: 'passthrough', seekable: true, duration_seconds: null, time_offset_seconds: 0, expires_at: 2000000000 }
  assert.throws(() => validateOnlineDescriptor(descriptor, first, { now: () => 1700000000000 }), error => error.code === 'invalid_online_descriptor')
})

test('health reads never cancel a catalogue query and cannot expose an older source status', async () => {
  const { createOnlineHealthClient, onlineHealthLabel } = await import('../src/onlineCatalog.js')
  const search = deferred(), oldHealth = deferred(), newHealth = deferred(), calls = []
  const request = (path, options) => {
    calls.push({ path, options })
    return path.includes('/search?') ? search.promise : calls.filter(call => call.path.includes('/health?')).length === 1 ? oldHealth.promise : newHealth.promise
  }
  const catalogue = createOnlineCatalogClient({ request }), health = createOnlineHealthClient({ request })
  const pendingSearch = catalogue.search(SOURCE, '同一片名')
  const first = health.read(SOURCE)
  const second = health.read('src_' + '9'.repeat(32))
  assert.equal(calls[0].options.signal.aborted, false)
  assert.equal(calls[1].options.signal.aborted, true)
  oldHealth.resolve({ state: 'old' }); newHealth.resolve({ state: 'new' }); search.resolve({ items: [] })
  assert.equal(await first, null); assert.deepEqual(await second, { state: 'new' }); assert.deepEqual(await pendingSearch, { items: [] })
  assert.equal(onlineHealthLabel({ state: 'success', stage: 'manifest', mode: 'direct' }), '清单已读 · 直连')
  assert.equal(onlineHealthLabel({ state: 'success', stage: 'bytes', mode: 'proxy' }), '取流通过 · 代理')
  assert.equal(onlineHealthLabel({ state: 'stale', mode: 'direct' }), '观察已过期 · 直连')
  catalogue.close(); health.close()
})
