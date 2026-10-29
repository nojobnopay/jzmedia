import test from 'node:test'
import assert from 'node:assert/strict'
import { createOnlineBrowseMemory, onlineBrowseScope, captureOnlinePosition, restoreOnlinePosition } from '../src/onlineBrowse.js'

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

test('expired directory data retains navigation but never renews its original freshness when saved again', () => {
  let now = 1000
  const memory = createOnlineBrowseMemory({ clock: () => now, dataTtl: 100, navigationTtl: 1000 })
  memory.save(snapshot({ loadedAt: now, position: { scrollTop: 400 } }))
  now = 1099
  assert.ok(memory.last(scope, [source]).response)
  now = 1100
  const stale = memory.last(scope, [source])
  assert.equal(stale.response, null)
  assert.equal(stale.categoryId, '71')
  assert.equal(stale.position.scrollTop, 400)
  memory.save(snapshot({ loadedAt: stale.loadedAt }))
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
  assert.equal(expired.response, null)
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
  assert.equal(first.response, null)
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
