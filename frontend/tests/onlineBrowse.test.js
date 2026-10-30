import test from 'node:test'
import assert from 'node:assert/strict'
import { createOnlineBrowseMemory, onlineBrowseScope, captureOnlinePosition, restoreOnlinePosition, onlineBrowseResponseEqual, ONLINE_BROWSE_FRESH_MS, ONLINE_BROWSE_NAVIGATION_MS } from '../src/onlineBrowse.js'

const source = { id: 'src_' + '1'.repeat(32), configuration_token: '2'.repeat(64), capabilities: { year_filter: true } }
const scope = onlineBrowseScope('3'.repeat(64), { mode: 'private', generation: 4, revision: 1 })
const response = (page = 1, title = '合成剧集') => ({ items: [{ external_id: String(page), title }],
  categories: [{ id: '71', name: '都市脑洞' }], page, page_count: 3, total: 50 })
const snapshot = (extra = {}) => ({ scope, source, categoryId: '71', query: '', response: response(), ...extra })

test('catalogue reuse requires the verified device scope and complete source configuration', () => {
  const memory = createOnlineBrowseMemory()
  assert.equal(memory.save(snapshot()), true)
  assert.equal(memory.read(scope, source, { categoryId: '71' }).response.items[0].title, '合成剧集')
  assert.equal(memory.last(onlineBrowseScope('4'.repeat(64), { mode: 'private', generation: 4, revision: 1 }), [source]), null)
  assert.equal(memory.last(onlineBrowseScope('3'.repeat(64), { mode: 'normal', generation: 4, revision: 1 }), [source]), null)
  assert.equal(memory.last(onlineBrowseScope('3'.repeat(64), { mode: 'private', generation: 5, revision: 1 }), [source]), null)
  assert.equal(memory.last(scope, [{ ...source, configuration_token: '5'.repeat(64) }]), null)
  assert.equal(memory.last(scope, []), null)
  assert.equal(memory.last(scope, [source], 'src_' + '6'.repeat(32)), null)
})

test('old servers without source and scope tokens never enable cross-mount catalogue reuse', () => {
  const memory = createOnlineBrowseMemory()
  assert.equal(onlineBrowseScope(undefined, { mode: 'normal' }), '')
  assert.equal(memory.save(snapshot({ scope: '' })), false)
  assert.equal(memory.save(snapshot({ source: { id: source.id } })), false)
  assert.equal(memory.stats().pages, 0)
})

test('stale directory rows stay visible without renewing their original age when navigation is saved again', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now, dataTtl: 100, navigationTtl: 1000 })
  memory.save(snapshot({ loadedAt: now, position: { scrollTop: 400 } }))
  now = 1099
  assert.ok(memory.last(scope, [source]).response)
  now = 1100
  const stale = memory.last(scope, [source])
  assert.equal(stale.stale, true)
  assert.equal(stale.response.items[0].title, '合成剧集')
  assert.equal(stale.categoryId, '71')
  assert.equal(stale.position.scrollTop, 400)
  memory.save(snapshot({ loadedAt: stale.loadedAt }))
  assert.equal(memory.last(scope, [source]).stale, true)
  now = 2000
  assert.equal(memory.last(scope, [source]).response, null)
  now = 2100
  assert.equal(memory.last(scope, [source]), null)
  assert.deepEqual(memory.stats(), { pages: 0, bytes: 0 })
})

test('page caches keep category and search identities distinct and return independent snapshots', () => {
  const memory = createOnlineBrowseMemory()
  const data = response()
  memory.save(snapshot({ response: data, inputQuery: '尚未提交的片名' }))
  data.items[0].title = '调用方修改'
  memory.save(snapshot({ query: '另一片名', categoryId: '', response: response(2, '搜索结果') }))
  const first = memory.read(scope, source, { categoryId: '71' })
  assert.equal(first.response.items[0].title, '合成剧集')
  assert.equal(first.inputQuery, '尚未提交的片名')
  first.response.items[0].title = '读取方修改'
  assert.equal(memory.read(scope, source, { categoryId: '71' }).response.items[0].title, '合成剧集')
  assert.equal(memory.read(scope, source, { query: '另一片名', page: 2 }).response.items[0].title, '搜索结果')
  assert.equal(memory.read(scope, source, { query: '另一片名', categoryId: '71', page: 2 }), null)
})

test('page LRU and byte ceilings bound all cached directories, including oversized data', () => {
  const memory = createOnlineBrowseMemory({ maxPages: 2, maxBytes: 2000 })
  memory.save(snapshot({ response: response(1) }))
  memory.save(snapshot({ response: response(2) }))
  memory.read(scope, source, { categoryId: '71', page: 1 })
  memory.save(snapshot({ response: response(3) }))
  assert.equal(memory.read(scope, source, { categoryId: '71', page: 2 }), null)
  assert.equal(memory.stats().pages, 2)
  assert.ok(memory.stats().bytes <= 2000)
  assert.equal(memory.save(snapshot({ response: response(2, 'x'.repeat(2000)) })), false)
  assert.ok(memory.stats().bytes <= 2000)
  memory.clear()
  assert.deepEqual(memory.stats(), { pages: 0, bytes: 0 })
})

test('an omitted taxonomy inherits the known source tree while expired poster rows stay expired', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now, dataTtl: 100, navigationTtl: 1000 })
  memory.save(snapshot({ loadedAt: now }))
  now = 1050
  memory.save(snapshot({ response: { ...response(2), categories: [] }, loadedAt: now }))
  assert.deepEqual(memory.read(scope, source, { categoryId: '71', page: 2 }).response.categories, response().categories)
  now = 1150
  const expired = memory.last(scope, [source])
  assert.equal(expired.stale, true)
  assert.equal(expired.response.items[0].title, '合成剧集')
  assert.deepEqual(expired.categories, response().categories)
  expired.categories[0].name = '读取方修改'
  assert.equal(memory.last(scope, [source]).categories[0].name, '都市脑洞')
  now = 2050
  assert.equal(memory.last(scope, [source]), null)
})

test('a newer real tree replaces old page metadata without renewing freshness or navigation lifetime', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now, dataTtl: 100, navigationTtl: 1000 })
  memory.save(snapshot({ loadedAt: now }))
  now = 1050
  const updated = [{ id: '72', name: '古装仙侠' }]
  memory.save(snapshot({ response: { ...response(2), categories: updated }, loadedAt: now }))
  assert.deepEqual(memory.read(scope, source, { categoryId: '71', page: 1 }).response.categories, updated)
  now = 1100
  const first = memory.read(scope, source, { categoryId: '71', page: 1 })
  assert.equal(first.loadedAt, 1000)
  assert.equal(first.stale, true)
  assert.equal(first.response.items[0].title, '合成剧集')
  assert.deepEqual(first.categories, updated)
  now = 2000
  assert.equal(memory.read(scope, source, { categoryId: '71', page: 1 }), null)
  assert.deepEqual(memory.read(scope, source, { categoryId: '71', page: 2 }).categories, updated)
})

test('evicting the newest taxonomy page cannot revive categories removed from an older retained page', () => {
  const memory = createOnlineBrowseMemory({ maxPages: 2 })
  memory.save(snapshot())
  const updated = [{ id: '72', name: '古装仙侠' }]
  memory.save(snapshot({ response: { ...response(2), categories: updated } }))
  memory.read(scope, source, { categoryId: '71', page: 1 })
  const other = { ...source, id: 'src_' + '4'.repeat(32) }
  memory.save(snapshot({ source: other, response: { ...response(), categories: [] } }))
  assert.equal(memory.read(scope, source, { categoryId: '71', page: 2 }), null)
  assert.deepEqual(memory.read(scope, source, { categoryId: '71', page: 1 }).response.categories, updated)
  assert.deepEqual(memory.last(scope, [other]).categories, [])
})

test('taxonomy replacement obeys the existing byte cap and oversized new trees invalidate old bindings', () => {
  const memory = createOnlineBrowseMemory({ maxPages: 3, maxBytes: 1600 })
  memory.save(snapshot())
  memory.save(snapshot({ response: response(2) }))
  const changed = [{ id: '72', name: '新分类'.repeat(60) }]
  assert.equal(memory.save(snapshot({ response: { ...response(3), categories: changed } })), true)
  assert.ok(memory.stats().bytes <= 1600)
  assert.deepEqual(memory.last(scope, [source]).categories, changed)
  assert.equal(memory.save(snapshot({ response: { ...response(4, 'x'.repeat(1600)), categories: [{ id: '73', name: '再更新' }] } })), false)
  assert.deepEqual(memory.stats(), { pages: 0, bytes: 0 })
})

test('taxonomy never crosses source, configuration or viewing-scope bindings', () => {
  const memory = createOnlineBrowseMemory()
  memory.save(snapshot())
  for (const changed of [{ source: { ...source, id: 'src_' + '5'.repeat(32) } },
    { source: { ...source, configuration_token: '6'.repeat(64) } },
    { scope: onlineBrowseScope('7'.repeat(64), { mode: 'private', generation: 4, revision: 1 }) }]) {
    const values = snapshot({ ...changed, response: { ...response(2), categories: [] } })
    assert.equal(memory.save(values), true)
    assert.deepEqual(memory.read(values.scope, values.source, { categoryId: '71', page: 2 }).categories, [])
  }
  assert.deepEqual(memory.read(scope, source, { categoryId: '71' }).categories, response().categories)
})

test('year and channel navigation cannot reuse a different filtered catalogue page', () => {
  const memory = createOnlineBrowseMemory()
  const selections = { '58': '71', '20': '21' }
  memory.save(snapshot({ year: '2026', channelId: '58', channelSelections: selections,
    response: response(2, '2026都市脑洞') }))
  selections['58'] = '74'
  const filters = { categoryId: '71', year: '2026', channelId: '58', page: 2 }
  const saved = memory.read(scope, source, filters)
  assert.equal(saved.response.items[0].title, '2026都市脑洞')
  assert.deepEqual(saved.channelSelections, { '58': '71', '20': '21' })
  saved.channelSelections['58'] = '65'
  assert.equal(memory.last(scope, [source]).channelSelections['58'], '71')
  for (const changed of [{ year: '' }, { year: '2025' }, { channelId: '20' }, { channelId: '' }]) {
    assert.equal(memory.read(scope, source, { ...filters, ...changed }), null)
  }
  memory.save(snapshot({ year: '2025', channelId: '58', response: response(2, '2025都市脑洞') }))
  assert.equal(memory.last(scope, [source]).year, '2025')
  assert.equal(memory.read(scope, source, filters).response.items[0].title, '2026都市脑洞')
})

test('malformed years and unbounded channel memories are refused before caching', () => {
  const memory = createOnlineBrowseMemory()
  for (const extra of [{ year: 2026 }, { year: '2026\n' }, { year: '0999' }, { channelId: '58\n' },
    { channelSelections: [] }, { channelSelections: new Date() }, { channelSelections: { '58': '71\n' } },
    { channelSelections: Object.fromEntries(Array.from({ length: 257 }, (_, index) => [String(index), '71'])) }]) {
    assert.equal(memory.save(snapshot(extra)), false)
  }
  assert.deepEqual(memory.stats(), { pages: 0, bytes: 0 })
})

test('a year capability downgrade cannot display a filtered snapshot under an empty year selector', () => {
  const memory = createOnlineBrowseMemory()
  memory.save(snapshot({ year: '2026' }))
  const unsupported = { ...source, capabilities: { year_filter: false } }
  assert.equal(memory.last(scope, [unsupported]), null)
  assert.equal(memory.read(scope, unsupported, { categoryId: '71', year: '2026' }), null)
  assert.equal(memory.save(snapshot({ source: unsupported, year: '2026' })), false)
  assert.equal(memory.save(snapshot({ source: unsupported })), true)
  assert.equal(memory.last(scope, [unsupported]).year, '')
})

test('scroll restoration follows the first visible poster anchor across layout changes', () => {
  const cards = [
    { dataset: { onlineBrowseId: 'offscreen' }, getBoundingClientRect: () => ({ top: -200, bottom: -10 }) },
    { dataset: { onlineBrowseId: 'episode' }, getBoundingClientRect: () => ({ top: -10, bottom: 190 }) },
  ]
  const root = { querySelectorAll: () => cards }
  const restored = []
  const viewport = { scrollY: 500, scrollTo: value => restored.push(value) }
  const position = captureOnlinePosition(root, viewport)
  assert.deepEqual(position, { scrollTop: 500, anchor: { id: 'episode', offset: -10 } })
  viewport.scrollY = 0
  cards[1].getBoundingClientRect = () => ({ top: 800, bottom: 1000 })
  restoreOnlinePosition(position, root, viewport)
  assert.deepEqual(restored[0], { top: 810, behavior: 'instant' })
  restoreOnlinePosition(position, { querySelectorAll: () => [] }, viewport)
  assert.equal(restored[1].top, 500)
  assert.doesNotThrow(() => restoreOnlinePosition(position, null, undefined))
})

test('default freshness is fifteen minutes and a thirty-minute-old row is never revived by remembering position', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now })
  memory.save(snapshot({ loadedAt: now }))
  now += ONLINE_BROWSE_FRESH_MS - 1
  assert.equal(memory.last(scope, [source]).stale, false)
  now++
  const stale = memory.last(scope, [source])
  assert.equal(stale.stale, true)
  assert.equal(stale.response.items[0].title, '合成剧集')
  memory.save(snapshot({ loadedAt: stale.loadedAt, position: { scrollTop: 900 } }))
  now = 1000 + ONLINE_BROWSE_NAVIGATION_MS
  assert.equal(memory.last(scope, [source]).response, null)
  assert.equal(memory.last(scope, [source]).position.scrollTop, 900)
})

test('recording a failed or deferred attempt does not extend row or navigation lifetime', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now, dataTtl: 100, navigationTtl: 1000 })
  memory.save(snapshot({ loadedAt: now }))
  now = 1500
  assert.equal(memory.recordAttempt(scope, source, { categoryId: '71' }), true)
  let saved = memory.last(scope, [source])
  assert.equal(saved.loadedAt, 1000)
  assert.equal(saved.lastAttemptAt, 1500)
  assert.equal(saved.stale, true)
  now = 1999
  saved = memory.last(scope, [source])
  assert.equal(saved.lastAttemptAt, 1500)
  now = 2000
  assert.equal(memory.last(scope, [source]), null)
})

test('unchanged successful checks renew data freshness without replacing original rows or shell metadata', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now, dataTtl: 100, navigationTtl: 1000 })
  memory.save(snapshot({ loadedAt: now, response: { ...response(), diagnostic: 'keep-original' } }))
  now = 1200
  memory.recordAttempt(scope, source, { categoryId: '71' })
  assert.deepEqual(memory.stage(scope, source, { categoryId: '71' },
    { ...response(), categories: [], status: 'ready', source: { name: 'shell only' }, diagnostic: 'new-shell' }), { changed: false, cached: false })
  const saved = memory.last(scope, [source])
  assert.equal(saved.loadedAt, 1200)
  assert.equal(saved.lastAttemptAt, 1200)
  assert.equal(saved.stale, false)
  assert.equal(saved.hasUpdate, false)
  assert.equal(saved.pending, null)
  assert.equal(saved.response.diagnostic, 'keep-original')
  assert.deepEqual(saved.response.categories, response().categories)
})

test('an unchanged real read renews navigation retention while an attempt alone cannot', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now, dataTtl: 100, navigationTtl: 1000 })
  memory.save(snapshot({ loadedAt: now }))
  now = 1900
  memory.recordAttempt(scope, source, { categoryId: '71' })
  now = 1950
  assert.deepEqual(memory.stage(scope, source, { categoryId: '71' }, response()), { changed: false, cached: false })
  now = 2100
  assert.equal(memory.last(scope, [source]).loadedAt, 1950)
  assert.ok(memory.last(scope, [source]).response)
})

test('changed background results remain pending until explicitly applied and survive position-only saves', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now })
  const position = { scrollTop: 600, anchor: { id: '1', offset: 20 } }
  memory.save(snapshot({ loadedAt: now, position }))
  now = 1100
  memory.recordAttempt(scope, source, { categoryId: '71' })
  const changed = { ...response(), items: [{ external_id: '1', title: '合成剧集', remarks: '更新至第2集' }] }
  assert.deepEqual(memory.stage(scope, source, { categoryId: '71' }, changed), { changed: true, cached: true })
  let saved = memory.last(scope, [source])
  assert.equal(saved.response.items[0].remarks, undefined)
  assert.equal(saved.loadedAt, 1000)
  assert.equal(saved.pendingLoadedAt, 1100)
  assert.equal(saved.hasUpdate, true)
  memory.save(snapshot({ loadedAt: 1000, position: { scrollTop: 800 } }))
  saved = memory.last(scope, [source])
  assert.equal(saved.lastAttemptAt, 1100)
  assert.equal(saved.pending.items[0].remarks, '更新至第2集')
  saved.pending.items[0].remarks = '读取方修改'
  const applied = memory.applyPending(scope, source, { categoryId: '71' })
  assert.equal(applied.response.items[0].remarks, '更新至第2集')
  assert.equal(applied.loadedAt, 1100)
  assert.deepEqual(applied.position, { scrollTop: 800 })
  assert.equal(applied.hasUpdate, false)
  assert.equal(applied.pending, null)
  assert.equal(memory.applyPending(scope, source, { categoryId: '71' }), null)
})

test('pending data shares the existing byte budget and oversized updates retain only the update marker', () => {
  const probe = createOnlineBrowseMemory()
  probe.save(snapshot())
  const perPage = probe.stats().bytes
  const memory = createOnlineBrowseMemory({ maxBytes: perPage * 2 + 100 })
  memory.save(snapshot())
  memory.save(snapshot({ response: response(2) }))
  assert.deepEqual(memory.stage(scope, source, { categoryId: '71' }, response(1, '新剧集')), { changed: true, cached: true })
  assert.equal(memory.read(scope, source, { categoryId: '71', page: 2 }), null)
  assert.ok(memory.stats().bytes <= perPage * 2 + 100)
  assert.deepEqual(memory.stage(scope, source, { categoryId: '71' }, response(1, 'x'.repeat(perPage * 3))), { changed: true, cached: false })
  const saved = memory.read(scope, source, { categoryId: '71' })
  assert.equal(saved.hasUpdate, true)
  assert.equal(saved.pending, null)
  assert.equal(saved.response.items[0].title, '合成剧集')
  assert.equal(memory.applyPending(scope, source, { categoryId: '71' }), null)
  assert.ok(memory.stats().bytes <= perPage * 2 + 100)
})

test('pending results cannot cross an identity or configuration boundary or outlive thirty minutes', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now, dataTtl: 100, navigationTtl: 1000 })
  memory.save(snapshot({ loadedAt: now }))
  now = 1100
  memory.stage(scope, source, { categoryId: '71' }, response(1, '新剧集'))
  const changedSource = { ...source, configuration_token: 'a'.repeat(64) }
  assert.equal(memory.applyPending(scope, changedSource, { categoryId: '71' }), null)
  assert.equal(memory.stage('a'.repeat(64) + ':normal:1:1', source, { categoryId: '71' }, response()), null)
  assert.equal(memory.recordAttempt(scope, source, { categoryId: '72' }), false)
  now = 1900
  memory.save(snapshot({ loadedAt: 1000 }))
  now = 2100
  assert.equal(memory.last(scope, [source]).response, null)
  assert.equal(memory.last(scope, [source]).pending, null)
  assert.equal(memory.applyPending(scope, source, { categoryId: '71' }), null)
  assert.equal(memory.last(scope, [source]).hasUpdate, true)
  memory.clearPending(scope, source, { categoryId: '71' })
  assert.equal(memory.last(scope, [source]).hasUpdate, false)
})

test('display equality ignores transport metadata and missing taxonomy but detects new episodes, images and order', () => {
  const current = { ...response(), source: { name: 'source shell' } }
  assert.equal(onlineBrowseResponseEqual(current, { ...response(), status: 'ready', categories: [] }), true)
  assert.equal(onlineBrowseResponseEqual(current, { ...response(), categories: [{ name: '都市脑洞', parent_id: '', id: '71' }] }), true)
  for (const extra of [{ items: [{ ...response().items[0], remarks: '更新至2集' }] },
    { items: [{ ...response().items[0], poster_path: '/different-opaque-lease' }] },
    { page_count: 4 }, { total: 51 }, { categories: [{ id: '72', name: '古装仙侠' }] }]) {
    assert.equal(onlineBrowseResponseEqual(current, { ...response(), ...extra }), false)
  }
  const second = { external_id: '2', title: '另一剧集' }
  assert.equal(onlineBrowseResponseEqual({ ...response(), items: [...response().items, second] },
    { ...response(), items: [second, ...response().items] }), false)
  assert.equal(onlineBrowseResponseEqual(current, { status: 'deferred' }), false)
})
