import test from 'node:test'
import assert from 'node:assert/strict'
import { createOnlineCatalogClient, createOnlineRefreshClient, choiceNetworkRule, networkRuleChoice, onlineCatalogError, onlineRouteTarget } from '../src/onlineCatalog.js'
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

test('year filtering is encoded as an upstream browse condition and never as a search substitute', async () => {
  const calls = []
  const client = createOnlineCatalogClient({ request: async path => { calls.push(path); return {} } })
  await client.browse(SOURCE, 2, '71', '2026')
  const selected = new URL(calls[0], 'https://jzmedia.invalid')
  assert.equal(selected.searchParams.get('year'), '2026')
  assert.equal(selected.searchParams.get('category_id'), '71')
  assert.equal(selected.searchParams.get('page'), '2')
  assert.equal(selected.searchParams.has('q'), false)
  await client.browse(SOURCE)
  assert.equal(new URL(calls[1], 'https://jzmedia.invalid').searchParams.has('year'), false, 'Empty filters remain compatible with previous servers')
  for (const year of [2026, null, '0200', '2026\n', '２０２６', '2026&area=x', '10000']) {
    assert.throws(() => client.browse(SOURCE, 1, '', year), /分类条件无效/)
  }
  assert.throws(() => client.browse(SOURCE, 1, '71\n'), /分类条件无效/)
  assert.equal(calls.length, 2, 'Invalid conditions never initiate a request')
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

test('catalogue entry-count limits are distinct from body-size limits and never echo upstream data', () => {
  const message = 'https://private.invalid/path?token=private-secret'
  const entries = onlineCatalogError({ code: 'provider_entry_limit', message })
  const body = onlineCatalogError({ code: 'provider_response_limit', message })
  assert.match(entries, /分集总数超过 2000 集/)
  assert.match(body, /资料过大/)
  assert.notEqual(entries, body)
  assert.equal(entries.includes(message), false)
  assert.equal(body.includes(message), false)
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

test('background refresh requests use the existing conditions with explicit low priority and their own deadline', async () => {
  const calls = []
  const ready = { status: 'ready', configuration_token: 'b'.repeat(64), source: { id: SOURCE }, items: [], page: 2, page_count: 3, total: 50 }
  const client = createOnlineRefreshClient({ request: async (path, options) => { calls.push({ path, options }); return ready } })
  assert.deepEqual(await client.browse(SOURCE, 2, '71', '2026'), ready)
  const selected = new URL(calls[0].path, 'https://jzmedia.invalid')
  assert.equal(selected.pathname, '/api/online/catalog/browse')
  assert.equal(selected.searchParams.get('priority'), 'background')
  assert.equal(selected.searchParams.get('category_id'), '71')
  assert.equal(selected.searchParams.get('year'), '2026')
  assert.equal(selected.searchParams.get('page'), '2')
  assert.equal(calls[0].options.timeout, 10000)
  await client.search(SOURCE, '片名 & ids=other', 2)
  const search = new URL(calls[1].path, 'https://jzmedia.invalid')
  assert.equal(search.pathname, '/api/online/catalog/search')
  assert.equal(search.searchParams.get('q'), '片名 & ids=other')
  assert.equal(search.searchParams.get('priority'), 'background')
  assert.equal(client.detail, undefined)
  assert.equal(client.sources, undefined)
  assert.throws(() => client.browse(SOURCE, 0), /分类条件无效/)
  assert.throws(() => client.search(SOURCE + '\n', '片名'), /身份无效/)
  client.close()
})

test('background cancellation cannot abort foreground work and never exposes a late ready or deferred result', async () => {
  const main = deferred(), first = deferred(), second = deferred(), calls = []
  const request = (path, options) => {
    calls.push({ path, options })
    return !path.includes('priority=background') ? main.promise
      : calls.filter(call => call.path.includes('priority=background')).length === 1 ? first.promise : second.promise
  }
  const foreground = createOnlineCatalogClient({ request })
  const background = createOnlineRefreshClient({ request })
  const mainWork = foreground.detail(SOURCE, 'show-1')
  const old = background.search(SOURCE, 'old')
  const current = background.search(SOURCE, 'new')
  assert.equal(calls[0].options.signal.aborted, false)
  assert.equal(calls[1].options.signal.aborted, true)
  first.resolve({ status: 'ready', configuration_token: 'b'.repeat(64), source: { id: SOURCE }, items: [], page: 1, page_count: 0, total: 0 })
  second.resolve({ status: 'deferred' })
  main.resolve({ item: { title: '当前详情' } })
  assert.equal(await old, null)
  assert.deepEqual(await current, { status: 'deferred' })
  assert.deepEqual(await mainWork, { item: { title: '当前详情' } })
  background.close(); foreground.close()
})

test('background refresh refuses malformed protocol responses instead of inventing an empty success', async () => {
  for (const body of [{}, { status: 'deferred', items: [] }, { status: 'ready', items: [], page: 1 },
    { status: 'ready', source: { id: 'src_' + '9'.repeat(32) }, items: [], page: 1, page_count: 0, total: 0 },
    { status: 'ready', source: { id: SOURCE }, items: [], page: 0, page_count: 0, total: 0 },
    { status: 'ready', source: { id: SOURCE }, items: [], page: 1, page_count: 0, total: 0 },
    { status: 'ready', configuration_token: 'invalid', source: { id: SOURCE }, items: [], page: 1, page_count: 0, total: 0 }]) {
    const client = createOnlineRefreshClient({ request: async () => body })
    await assert.rejects(client.browse(SOURCE), error => error.code === 'invalid_provider_response'
      && !error.message.includes('source_id'))
    client.close()
  }
})
