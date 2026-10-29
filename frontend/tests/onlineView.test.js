import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'
import * as catalog from '../src/onlineCatalog.js'
import * as relatedCatalog from '../src/onlineRelated.js'
import * as state from '../src/onlineState.js'
import { clearOnlineBrowse, onlineBrowseMemory, onlineBrowseScope } from '../src/onlineBrowse.js'

const SOURCE_A = 'src_' + '1'.repeat(32), SOURCE_B = 'src_' + '2'.repeat(32)
const sources = { enabled: true, items: [{ id: SOURCE_A, name: '来源 A', provider: 'maccms' }, { id: SOURCE_B, name: '来源 B', provider: 'maccms' }] }
const rows = { items: [{ external_id: 'movie-1', title: '合成电影', kind: 'movie', year: '2026' }], page: 1, page_count: 1, total: 1 }
const showVisibility = (element, binding) => { element.props['data-test-visible'] = !!binding.value }
const vue = { ...Vue, vModelText: {}, vModelSelect: {}, vModelCheckbox: {}, vShow: { beforeMount: showVisibility, updated: showVisibility } }
const SCOPE = 'a'.repeat(64)
async function setup(t, request, { stateRequest, relatedRequest, autoplay = false, routeQuery = {}, session = {} } = {}) {
  const cleanups = new Set()
  const viewingSession = Vue.reactive({ mode: 'normal', generation: 1, revision: 0, locallyLocked: false, suspended: false, ...session })
  const players = [], writers = [], preferences = [], navigations = []
  const stateRead = stateRequest || (async path => path.includes('/library?')
    ? { items: [], total: 0, limit: 20, offset: 0, scope_token: SCOPE }
    : { item_id: path.split('/').at(-1).split('?')[0], favorite: false, last_progress: null, selected_progress: null, available: true, scope_token: SCOPE })
  const component = await loadSfc(new URL('../src/views/Online.vue', import.meta.url), {
    vue,
    'vue-router': { useRoute: () => ({ query: routeQuery }), useRouter: () => ({ replace: async target => { navigations.push(target) } }) },
    '../onlineCatalog.js': { ...catalog, createOnlineCatalogClient: () => catalog.createOnlineCatalogClient({ request }), createOnlineHealthClient: () => ({ read: async () => null, cancel() {}, close() {} }) },
    '../onlineRelated.js': { createOnlineRelatedClient: () => relatedCatalog.createOnlineRelatedClient({ delay: 0,
      request: relatedRequest || (async path => ({ source: { id: SOURCE_A }, external_id: new URL(path, 'https://jzmedia.invalid').searchParams.get('external_id'), status: 'unsupported' })) }) },
    '../onlineState.js': { ...state, createOnlineStateClient: () => state.createOnlineStateClient({ request: stateRead }),
      loadOnlineAutoplay: () => autoplay, saveOnlineAutoplay: value => preferences.push(value),
      createOnlineProgressWriter: options => { const writer = { options, snapshots: [], sessions: [], closes: 0, session(value) { this.sessions.push(value) }, update(value) { this.snapshots.push(value) }, async close() { this.closes++ } }; writers.push(writer); return writer } },
    '../viewing.js': { isChildren: Vue.ref(false), viewing: viewingSession, registerViewingPlaybackCleanup: callback => { cleanups.add(callback); return () => cleanups.delete(callback) } },
    // Overflow measurement belongs to the real-browser check; this host has no layout engine.
    '../components/MediaOverview.vue': { default: { props: ['text', 'lines'],
      setup: props => () => Vue.h('p', { 'data-stub-overview': true }, props.text) } },
    '../components/JzDialog.vue': { default: { props: ['title', 'size'], emits: ['close'],
      setup: (_props, { slots }) => () => Vue.h('section', { 'data-stub-category-dialog': true }, slots.default?.()) } },
    '../components/OnlinePlayerModal.vue': { default: { props: ['onlineIdentity', 'initialPosition', 'prepareSession'], setup: props => {
      const listeners = Vue.getCurrentInstance().vnode.props
      const player = { props, ended: identity => listeners.onEnded(identity || props.onlineIdentity),
        progress: value => listeners.onProgress(value), close: () => listeners.onClose(), session: value => props.prepareSession(value) }
      players.push(player)
      return () => Vue.h('div', { 'data-stub-player': true })
    } } },
  })
  const ui = renderHarness(component)
  const unmount = ui.app.unmount.bind(ui.app)
  let mounted = true
  ui.app.unmount = () => { if (mounted) { mounted = false; unmount() } }
  t.after(() => ui.app.unmount())
  t.after(clearOnlineBrowse)
  await flush()
  return { ui, cleanup: () => Promise.all([...cleanups].map(cleanup => cleanup())), players, writers, preferences, navigations, viewing: viewingSession }
}
function search(ui, query) {
  ui.find(node => node.props.id === 'online-query').props['onUpdate:modelValue'](query)
  ui.find(node => node.props['data-online-search'] !== undefined).props.onSubmit({ preventDefault() {} })
}

const hierarchy = [{ id: '20', name: '电影片' }, { id: '21', parent_id: '20', name: '动作片' },
  { id: '58', name: '短剧大全' }, { id: '71', parent_id: '58', name: '都市脑洞' },
  { id: '72', parent_id: '58', name: '古装仙侠' }]
function taxonomySources(yearFilter = true) {
  return { enabled: true, scope_token: SCOPE,
    items: [{ ...sources.items[0], configuration_token: 'b'.repeat(64), capabilities: { browse: true, year_filter: yearFilter } }] }
}
function categoryRows(path) {
  const params = new URL(path, 'https://jzmedia.invalid').searchParams
  return { ...rows, page: Number(params.get('page') || 1), page_count: 2, total: 40, categories: hierarchy }
}

test('a failed new year cannot paginate using a previous year count until retry succeeds', async t => {
  let failing = true
  const calls = []
  const { ui } = await setup(t, async path => {
    calls.push(path)
    if (path.endsWith('/sources')) return taxonomySources()
    const params = new URL(path, 'https://jzmedia.invalid').searchParams
    if (params.get('year') === '2025' && failing) throw { code: 'provider_timeout' }
    return { ...categoryRows(path), items: [{ ...rows.items[0], year: params.get('year') || '2026' }] }
  })
  const year = () => ui.find(node => node.props['data-online-year'] !== undefined)
  const next = () => ui.find(node => node.type === 'button' && nodeText(node) === '下一页')
  year().props.onChange({ target: { value: '2026' } }); await flush()
  assert.equal(next().props.disabled, false)
  year().props.onChange({ target: { value: '2025' } }); await flush()
  assert.ok(ui.text().includes('以下仍为上次结果'))
  assert.ok(ui.text().includes('2026 年'))
  assert.equal(next().props.disabled, true)
  failing = false
  ui.find(node => node.type === 'button' && nodeText(node) === '重试').props.onClick(); await flush()
  assert.equal(new URL(calls.at(-1), 'https://jzmedia.invalid').searchParams.get('year'), '2025')
  assert.equal(next().props.disabled, false)
})

test('channels choose a concrete child, remember it and keep native parent queries explicit', async t => {
  const calls = []
  const { ui } = await setup(t, async path => { calls.push(path); return path.endsWith('/sources') ? taxonomySources() : categoryRows(path) })
  ui.find(node => node.props['data-online-channel'] === '58').props.onClick(); await flush()
  assert.equal(new URL(calls.at(-1), 'https://jzmedia.invalid').searchParams.get('category_id'), '71')
  ui.find(node => node.props['data-online-category'] === '72').props.onClick(); await flush()
  ui.find(node => node.props['data-online-channel'] === '20').props.onClick(); await flush()
  assert.equal(new URL(calls.at(-1), 'https://jzmedia.invalid').searchParams.get('category_id'), '21')
  ui.find(node => node.props['data-online-channel'] === '58').props.onClick(); await flush()
  assert.equal(ui.find(node => node.props['data-online-category'] === '72').props['aria-pressed'], true)
  ui.find(node => node.props['data-online-category'] === '58').props.onClick(); await flush()
  assert.equal(new URL(calls.at(-1), 'https://jzmedia.invalid').searchParams.get('category_id'), '58')
  assert.ok(ui.all().some(node => node.type === 'h2' && nodeText(node).includes('短剧大全综合条目')))
  assert.equal(ui.text().includes('全部短剧'), false)
})

test('verified year filters reach pagination, validate custom years and clear before keyword search', async t => {
  const calls = []
  const { ui } = await setup(t, async path => { calls.push(path); return path.endsWith('/sources') ? taxonomySources() : categoryRows(path) })
  const chooseYear = value => ui.find(node => node.props['data-online-year'] !== undefined).props.onChange({ target: { value } })
  chooseYear('2026'); await flush()
  ui.find(node => node.props['data-online-channel'] === '58').props.onClick(); await flush()
  ui.find(node => node.type === 'button' && nodeText(node) === '下一页').props.onClick(); await flush()
  let params = new URL(calls.at(-1), 'https://jzmedia.invalid').searchParams
  assert.equal(params.get('year'), '2026'); assert.equal(params.get('page'), '2'); assert.equal(params.get('category_id'), '71')
  chooseYear('custom'); await flush()
  const input = () => ui.find(node => node.props['data-online-year-custom'] !== undefined)
  input().props['onUpdate:modelValue']('0000')
  const before = calls.length
  ui.find(node => node.props['data-online-year-form'] !== undefined).props.onSubmit({ preventDefault() {} }); await flush()
  assert.equal(calls.length, before)
  assert.ok(ui.text().includes('1000 至 9999'))
  input().props['onUpdate:modelValue']('1988')
  ui.find(node => node.props['data-online-year-form'] !== undefined).props.onSubmit({ preventDefault() {} }); await flush()
  params = new URL(calls.at(-1), 'https://jzmedia.invalid').searchParams
  assert.equal(params.get('year'), '1988'); assert.equal(params.get('page'), '1')
  search(ui, '合成'); await flush()
  const keyword = new URL(calls.at(-1), 'https://jzmedia.invalid')
  assert.equal(keyword.pathname, '/api/online/catalog/search')
  assert.equal(keyword.searchParams.has('year'), false)
  assert.equal(ui.find(node => node.props['data-online-year'] !== undefined).props.value, '')
  assert.equal(ui.find(node => node.props['data-online-channel'] === '58').props['aria-pressed'], false)
})

test('unknown-source capabilities hide year controls and cannot inherit a prior source year', async t => {
  const catalogue = taxonomySources()
  catalogue.items.push({ ...sources.items[1], capabilities: { browse: true, year_filter: false } })
  const calls = []
  const { ui } = await setup(t, async path => { calls.push(path); return path.endsWith('/sources') ? catalogue : categoryRows(path) })
  ui.find(node => node.props['data-online-year'] !== undefined).props.onChange({ target: { value: '2026' } }); await flush()
  const select = ui.find(node => node.props['data-online-source'] !== undefined)
  select.props['onUpdate:modelValue'](SOURCE_B); select.props.onChange(); await flush()
  assert.equal(ui.find(node => node.props['data-online-year'] !== undefined), undefined)
  const next = new URL(calls.at(-1), 'https://jzmedia.invalid')
  assert.equal(next.searchParams.get('source_id'), SOURCE_B)
  assert.equal(next.searchParams.has('year'), false)
})

test('verified warm reentry restores channel, selected child and year without another provider request', async t => {
  const request = async path => path.endsWith('/sources') ? taxonomySources() : categoryRows(path)
  const first = await setup(t, request)
  first.ui.find(node => node.props['data-online-channel'] === '58').props.onClick(); await flush()
  first.ui.find(node => node.props['data-online-category'] === '72').props.onClick(); await flush()
  first.ui.find(node => node.props['data-online-year'] !== undefined).props.onChange({ target: { value: '2026' } }); await flush()
  first.ui.find(node => node.type === 'button' && nodeText(node) === '下一页').props.onClick(); await flush()
  first.ui.app.unmount()
  const calls = []
  const second = await setup(t, async path => { calls.push(path); return request(path) })
  assert.equal(calls.length, 1)
  assert.equal(second.ui.find(node => node.props['data-online-year'] !== undefined).props.value, '2026')
  assert.equal(second.ui.find(node => node.props['data-online-channel'] === '58').props['aria-pressed'], true)
  assert.equal(second.ui.find(node => node.props['data-online-category'] === '72').props['aria-pressed'], true)
  assert.ok(second.ui.text().includes('第 2 / 2 页'))
})

test('category panel searches local labels without fetching and selections close before querying', async t => {
  const calls = []
  const { ui, viewing } = await setup(t, async path => { calls.push(path); return path.endsWith('/sources') ? taxonomySources() : categoryRows(path) })
  ui.find(node => node.props['data-online-categories-open'] !== undefined).props.onClick(); await flush()
  const before = calls.length
  ui.find(node => node.props['data-online-category-query'] !== undefined).props['onUpdate:modelValue']('都市'); await flush()
  assert.equal(calls.length, before)
  const panel = ui.find(node => node.props['data-online-categories-dialog'] !== undefined)
  const child = ui.all().find(node => node.props['data-online-category'] === '71' && (() => {
    let parent = node.parent
    while (parent) { if (parent === panel) return true; parent = parent.parent }
    return false
  })())
  child.props.onClick(); await flush()
  assert.equal(ui.find(node => node.props['data-online-categories-dialog'] !== undefined), undefined)
  assert.equal(new URL(calls.at(-1), 'https://jzmedia.invalid').searchParams.get('category_id'), '71')
  ui.find(node => node.props['data-online-categories-open'] !== undefined).props.onClick(); await flush()
  viewing.suspended = true; await flush()
  viewing.suspended = false; await flush()
  assert.equal(ui.find(node => node.props['data-online-categories-dialog'] !== undefined), undefined, 'temporary verification never reopens the panel')
})

test('provider remarks and available metadata render as text without requiring local metadata services', async t => {
  const { ui } = await setup(t, async path => {
    if (path.endsWith('/sources')) return taxonomySources()
    if (path.includes('/browse?')) return { ...categoryRows(path), items: [{ ...rows.items[0], remarks: '更新至第7集' }] }
    return { ...show(), category: '泰国剧', year: '2026', remarks: '更新至第7集', area: '泰国', language: '泰语',
      genres: ['剧情', '悬疑'], directors: ['导演甲'], actors: ['演员甲', '演员乙'], subtitle: '来源别名' }
  })
  assert.equal(nodeText(ui.find(node => node.props['data-online-item-status'] !== undefined)), '更新至第7集')
  ui.find(node => node.props['data-online-result'] === 'movie-1').props.onClick(); await flush()
  assert.equal(nodeText(ui.find(node => node.props['data-online-detail-status'] !== undefined)), '更新至第7集')
  for (const text of ['泰国', '泰语', '剧情', '悬疑', '导演甲', '演员甲', '来源别名']) assert.ok(ui.text().includes(text))
  assert.ok(ui.find(node => node.props['data-online-metadata'] !== undefined))
})

test('a source repair return link opens the selected item once without auto-discovery or playback', async t => {
  const calls = []
  const { ui, players } = await setup(t, async path => {
    calls.push(path)
    if (path.endsWith('/sources')) return { ...sources, items: sources.items.map(source => ({ ...source, capabilities: { browse: true } })) }
    return { source: sources.items[1], item: { id: ITEM, source_id: SOURCE_B, kind: 'tv', title: '待修复剧集' },
      year: '2026', lines: [{ id: LINE, label: '主线路', entries: [{ label: '第1集', supported: false,
        identity: null, transport: null, reason: 'target_host_not_allowed' }] }], attribution: [] }
  }, { routeQuery: { source: SOURCE_B, item: 'show-1' } })
  assert.equal(calls.length, 2)
  assert.equal(new URL(calls[1], 'https://jzmedia.invalid').searchParams.get('source_id'), SOURCE_B)
  assert.equal(new URL(calls[1], 'https://jzmedia.invalid').searchParams.get('external_id'), 'show-1')
  assert.equal(ui.text().includes('待修复剧集'), true)
  assert.equal(!!ui.find(node => node.props['data-online-host-repair'] !== undefined), true)
  assert.equal(players.length, 0)
})

test('a repair link for a missing source never opens the same item ID on another source', async t => {
  const calls = []
  const { ui } = await setup(t, async path => { calls.push(path); return sources },
    { routeQuery: { source: 'src_' + 'f'.repeat(32), item: 'show-1' } })
  assert.equal(calls.length, 1)
  assert.equal(ui.text().includes('该来源已停用或不存在'), true)
})

test('discovery loads a poster wall on entry and sends the actual source category instead of a keyword', async t => {
  const calls = []
  const { ui } = await setup(t, async path => {
    calls.push(path)
    if (path.endsWith('/sources')) return { enabled: true, items: [{ ...sources.items[0], capabilities: { browse: true } }] }
    return { ...rows, categories: [{ id: '7', name: '动作片' }] }
  })
  assert.equal(calls.some(path => path.includes('/browse?')), true)
  assert.equal(ui.text().includes('合成电影'), true)
  assert.equal(ui.find(node => node.props['data-online-source'] !== undefined).props.disabled, false,
    'automatic discovery must finish the source-loading state')
  const before = calls.length
  ui.find(node => node.props['data-online-source'] !== undefined).props.onChange()
  await flush()
  assert.equal(calls.length, before, 'reselecting the same source cannot cancel and duplicate automatic discovery')
  ui.find(node => node.props['data-online-category'] === '7').props.onClick()
  await flush()
  const selected = new URL(calls.at(-1), 'https://jzmedia.invalid')
  assert.equal(selected.pathname, '/api/online/catalog/browse')
  assert.equal(selected.searchParams.get('category_id'), '7')
  assert.equal(selected.searchParams.has('q'), false)
  search(ui, '合成'); await flush()
  assert.equal(ui.find(node => node.props['data-online-category'] === '7').props['aria-pressed'], false,
    'a keyword search cannot claim an unapplied source category filter')
})

test('changing source cancels an automatic discovery and ignores its late category list', async t => {
  const old = deferred(), calls = []
  const { ui } = await setup(t, async (path, options) => {
    if (path.endsWith('/sources')) return { ...sources, items: sources.items.map(source => ({ ...source, capabilities: { browse: true } })) }
    calls.push({ path, signal: options.signal })
    return path.includes(SOURCE_A) ? old.promise : { ...rows, categories: [{ id: 'new', name: '当前来源分类' }] }
  })
  const select = ui.find(node => node.props['data-online-source'] !== undefined)
  select.props['onUpdate:modelValue'](SOURCE_B); select.props.onChange()
  await flush()
  old.resolve({ ...rows, categories: [{ id: 'old', name: '旧来源分类' }] }); await flush()
  assert.equal(calls[0].signal.aborted, true)
  assert.equal(ui.text().includes('当前来源分类'), true)
  assert.equal(ui.text().includes('旧来源分类'), false)
})

test('changing a source cancels an old search without showing its late error on the new source', async t => {
  const pending = deferred(), calls = []
  const { ui } = await setup(t, async (path, options) => {
    calls.push({ path, options })
    return path.endsWith('/sources') ? sources : pending.promise
  })
  search(ui, '旧片名'); await flush()
  const select = ui.find(node => node.props['data-online-source'] !== undefined)
  select.props['onUpdate:modelValue'](SOURCE_B); select.props.onChange()
  pending.reject(new Error('old source failure'))
  await flush()
  assert.equal(calls.at(-1).options.signal.aborted, true)
  assert.equal(ui.text().includes('搜索失败'), false)
  assert.equal(ui.text().includes('查找想看的内容'), true)
})

test('returning from an unfinished detail keeps search rows and discards its late response', async t => {
  const pending = deferred()
  const { ui } = await setup(t, path => Promise.resolve(path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : pending.promise))
  search(ui, '合成'); await flush()
  ui.find(node => node.props['data-online-result'] === 'movie-1').props.onClick()
  await flush()
  assert.equal(ui.text().includes('正在加载详情'), true)
  ui.find(node => node.type === 'button' && node.children.some(child => child.text === '返回搜索结果')).props.onClick()
  pending.resolve({ item: { title: '过时详情' }, lines: [] })
  await flush()
  assert.equal(ui.text().includes('合成电影'), true)
  assert.equal(ui.text().includes('过时详情'), false)
  assert.equal(ui.find(node => node.props['data-online-detail'] !== undefined), undefined)
})

test('permission cleanup discards the pending detail and previously visible results', async t => {
  const pending = deferred()
  const { ui, cleanup } = await setup(t, path => Promise.resolve(path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : pending.promise))
  search(ui, '合成'); await flush()
  ui.find(node => node.props['data-online-result'] === 'movie-1').props.onClick()
  await flush(); cleanup()
  pending.resolve({ item: { title: '受保护详情' }, lines: [] })
  await flush()
  assert.equal(ui.text().includes('受保护详情'), false)
  assert.equal(ui.text().includes('合成电影'), false)
  assert.equal(ui.text().includes('查找想看的内容'), true)
})

const ITEM = 'itm_' + '3'.repeat(32), LINE = 'ln_' + '4'.repeat(32)
const EP1 = 'ep_' + '5'.repeat(32), EP2 = 'ep_' + '6'.repeat(32)
const identity = ep => ({ source_id: SOURCE_A, item_id: ITEM, kind: 'episode', episode_id: ep, line_id: LINE })
const show = () => ({ source: sources.items[0], item: { id: ITEM, source_id: SOURCE_A, kind: 'tv', title: '合成剧集' },
  description: '', year: '2026', lines: [{ id: LINE, label: '同一线路', entries: [
    { label: '第1集', supported: true, identity: identity(EP1), transport: 'mp4' },
    { label: '第2集', supported: true, identity: identity(EP2), transport: 'hls' },
  ] }], attribution: [] })
const itemState = (extra = {}) => ({ item_id: ITEM, favorite: false, last_progress: null, selected_progress: null,
  available: true, reason: '', scope_token: SCOPE, ...extra })
async function openShow(ui) {
  search(ui, '合成'); await flush()
  ui.find(node => node.props['data-online-result'] === 'movie-1').props.onClick(); await flush()
}
function selectedEntry(ui, label = '第1集') { return ui.find(node => node.props['data-online-entry'] === label) }

const ALT_LINE = 'ln_' + '7'.repeat(32)
function multiLineShow() {
  const data = show()
  return { ...data, lines: [...data.lines, { id: ALT_LINE, label: '另一线路', entries: data.lines[0].entries.map(entry => ({
    ...entry, identity: { ...entry.identity, line_id: ALT_LINE },
  })) }] }
}
function primaryPlay(ui) { return ui.find(node => node.props['data-online-primary-play'] !== undefined) }
function chooseDetailLine(ui, line) { ui.find(node => node.props['data-online-line'] === line).props.onClick() }
const savedProgress = (entry, extra = {}) => ({ identity: entry, position: 11.5, duration: 24, completed: false, updated_at: 10, writer_revision: 4, ...extra })

test('details show the primary play and episode picker before supplementary information while browse controls keep their state', async t => {
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows
    : { ...show(), description: '合成来源简介', actors: ['合成演员'] })
  const tools = ui.find(node => node.props['data-online-browse-tools'] !== undefined)
  const queryField = ui.find(node => node.props.id === 'online-query')
  await openShow(ui)
  assert.equal(tools.props['data-test-visible'], false)
  assert.equal(nodeText(primaryPlay(ui)), '立即播放 · 第1集')
  const nodes = ui.all()
  assert.ok(nodes.indexOf(primaryPlay(ui)) < nodes.indexOf(selectedEntry(ui)))
  assert.ok(nodes.indexOf(selectedEntry(ui)) < nodes.indexOf(ui.find(node => node.props['data-online-detail-info'] !== undefined)))
  const metadata = ui.find(node => node.props['data-online-detail-metadata'] !== undefined)
  assert.equal(metadata.type, 'details')
  assert.equal(metadata.props.open, undefined, 'Supplementary cast metadata is initially collapsed')
  assert.ok(ui.find(node => node.props['data-stub-overview'] !== undefined))
  assert.equal(players.length, 0, 'Opening the detail never starts playback')
  ui.find(node => node.type === 'button' && nodeText(node) === '返回搜索结果').props.onClick(); await flush()
  assert.equal(ui.find(node => node.props['data-online-browse-tools'] !== undefined), tools)
  assert.equal(tools.props['data-test-visible'], true)
  assert.equal(ui.find(node => node.props.id === 'online-query'), queryField)
})

test('a pending watching-state read leaves the detail visible and fences primary playback until the record is known', async t => {
  const pending = deferred()
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : show(),
    { stateRequest: async path => path.includes('/library?') ? { items: [], total: 0, offset: 0, scope_token: SCOPE } : pending.promise })
  await openShow(ui)
  assert.ok(ui.find(node => node.props['data-online-detail'] !== undefined))
  assert.equal(ui.text().includes('正在加载详情'), false)
  assert.equal(primaryPlay(ui).props.disabled, true)
  assert.ok(ui.find(node => node.props['data-online-detail-state-loading'] !== undefined))
  assert.equal(players.length, 0)
  pending.resolve(itemState()); await flush()
  assert.equal(primaryPlay(ui).props.disabled, false)
  assert.equal(nodeText(primaryPlay(ui)), '立即播放 · 第1集')
})

test('the primary action restores the exact unfinished episode and line without starting until clicked', async t => {
  const saved = savedProgress({ ...identity(EP2), line_id: ALT_LINE }), reads = []
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : multiLineShow(),
    { stateRequest: async path => {
      reads.push(path)
      return path.includes('/library?') ? { items: [], total: 0, offset: 0, scope_token: SCOPE }
        : itemState({ last_progress: saved, selected_progress: path.includes('?') ? saved : null })
    } })
  await openShow(ui)
  assert.equal(ui.find(node => node.props['data-online-line'] === ALT_LINE).props['aria-pressed'], true)
  assert.equal(nodeText(primaryPlay(ui)), '继续播放 · 第2集')
  assert.ok(ui.find(node => node.props['data-online-detail-resume'] !== undefined))
  assert.equal(players.length, 0)
  primaryPlay(ui).props.onClick(); await flush()
  const selected = new URL(reads.find(path => path.includes('episode_id=')), 'https://jzmedia.invalid').searchParams
  assert.equal(selected.get('line_id'), ALT_LINE)
  assert.equal(selected.get('episode_id'), EP2)
  assert.equal(players[0].props.onlineIdentity.line_id, ALT_LINE)
  assert.equal(players[0].props.initialPosition, 11.5)
})

test('manual line intent wins even when a deferred resume record later names another line', async t => {
  const pending = deferred(), saved = savedProgress({ ...identity(EP2), line_id: ALT_LINE }), reads = []
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : multiLineShow(),
    { stateRequest: async path => {
      reads.push(path)
      return path.includes('/library?') ? { items: [], total: 0, offset: 0, scope_token: SCOPE }
        : path.includes('?') ? itemState({ last_progress: saved, selected_progress: null }) : pending.promise
    } })
  await openShow(ui)
  chooseDetailLine(ui, ALT_LINE); chooseDetailLine(ui, LINE)
  pending.resolve(itemState({ last_progress: saved })); await flush()
  assert.equal(ui.find(node => node.props['data-online-line'] === LINE).props['aria-pressed'], true)
  assert.equal(nodeText(primaryPlay(ui)), '立即播放 · 第1集')
  assert.ok(ui.find(node => node.props['data-online-detail-locate-last'] !== undefined))
  primaryPlay(ui).props.onClick(); await flush()
  assert.equal(players[0].props.onlineIdentity.line_id, LINE)
  assert.equal(players[0].props.initialPosition, 0, 'Another line cannot lend its saved position')
  assert.equal(new URL(reads.find(path => path.includes('episode_id=')), 'https://jzmedia.invalid').searchParams.get('line_id'), LINE)
})

test('explicitly locating the previous line changes the picker without starting playback', async t => {
  const saved = savedProgress({ ...identity(EP2), line_id: ALT_LINE })
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : multiLineShow(),
    { stateRequest: async path => path.includes('/library?') ? { items: [], total: 0, offset: 0, scope_token: SCOPE } : itemState({ last_progress: saved }) })
  await openShow(ui)
  chooseDetailLine(ui, LINE); await flush()
  const locate = ui.find(node => node.props['data-online-detail-locate-last'] !== undefined)
  assert.ok(locate)
  locate.props.onClick(); await flush()
  assert.equal(ui.find(node => node.props['data-online-line'] === ALT_LINE).props['aria-pressed'], true)
  assert.equal(nodeText(primaryPlay(ui)), '继续播放 · 第2集')
  assert.equal(players.length, 0)
})

test('completed playback restarts from zero and unsupported entries never become the primary action', async t => {
  const saved = savedProgress(identity(EP1), { completed: true, position: 24 })
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : show(),
    { stateRequest: async path => path.includes('/library?') ? { items: [], total: 0, offset: 0, scope_token: SCOPE }
      : itemState({ last_progress: saved, selected_progress: saved }) })
  await openShow(ui)
  assert.equal(nodeText(primaryPlay(ui)), '重新播放 · 第1集')
  assert.equal(ui.find(node => node.props['data-online-detail-resume'] !== undefined), undefined)
  primaryPlay(ui).props.onClick(); await flush()
  assert.equal(players[0].props.initialPosition, 0)
  const other = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : {
    ...show(), lines: [{ ...show().lines[0], entries: [{ label: '不可播放', supported: false, identity: null, reason: 'host_not_allowed' }] }],
  })
  await openShow(other.ui)
  assert.equal(primaryPlay(other.ui).props.disabled, true)
  assert.equal(other.players.length, 0)
})

test('a failed detail-state read still permits exact-selection playback and reports the record error', async t => {
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : show(),
    { stateRequest: async path => {
      if (path.includes('/library?')) return { items: [], total: 0, offset: 0, scope_token: SCOPE }
      throw new Error('isolated state read failure')
    } })
  await openShow(ui)
  assert.equal(primaryPlay(ui).props.disabled, false)
  assert.ok(ui.text().includes('暂时无法保存或读取在线记录'))
  primaryPlay(ui).props.onClick(); await flush()
  assert.equal(players.length, 1)
  assert.equal(players[0].props.onlineIdentity.episode_id, EP1)
})

test('an obsolete detail-state read cannot remove the loading guard of a newer detail', async t => {
  const first = deferred(), second = deferred(), nextItem = 'itm_' + '8'.repeat(32)
  let details = 0
  const other = { ...show(), item: { ...show().item, id: nextItem, title: '另一剧集' }, lines: show().lines.map(line => ({
    ...line, entries: line.entries.map(entry => ({ ...entry, identity: { ...entry.identity, item_id: nextItem } })),
  })) }
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : ++details === 1 ? show() : other,
    { stateRequest: async path => path.includes('/library?') ? { items: [], total: 0, offset: 0, scope_token: SCOPE }
      : path.includes(nextItem) ? second.promise : first.promise })
  await openShow(ui)
  ui.find(node => node.type === 'button' && nodeText(node) === '返回搜索结果').props.onClick(); await flush()
  await openShow(ui)
  first.resolve(itemState()); await flush()
  assert.equal(primaryPlay(ui).props.disabled, true)
  assert.ok(ui.text().includes('另一剧集'))
  second.resolve(itemState({ item_id: nextItem })); await flush()
  assert.equal(primaryPlay(ui).props.disabled, false)
  assert.equal(players.length, 0)
})

const relatedSources = () => ({ ...sources, items: sources.items.map(source => ({ ...source, capabilities: { related: true } })) })
const relatedRows = (externalId = 'movie-1', extra = {}) => ({ source: sources.items[0], external_id: externalId,
  current_season: 2, status: 'ready',
  series: [{ external_id: 'season-1', title: '合成剧集第一季', kind: 'tv', season: 1, reason: 'inferred_season' },
    { external_id: 'season-3', title: '合成剧集第三季', kind: 'tv', season: 3, reason: 'inferred_season' }],
  related: [{ external_id: 'same-type', title: '同类内容', kind: 'tv', season: null, reason: 'same_category' }],
  name_related: [{ external_id: 'possible', title: '合成剧集特别篇', kind: 'tv', season: null, reason: 'name_related' }], ...extra })
const detailRequest = async path => path.endsWith('/sources') ? relatedSources() : path.includes('/search?') ? rows : show()

test('related content loads after the detail and renders season, category and uncertain-name rows independently', async t => {
  const pending = deferred()
  const { ui } = await setup(t, detailRequest, { relatedRequest: () => pending.promise })
  await openShow(ui)
  assert.ok(selectedEntry(ui), 'The selected detail remains usable while recommendations wait')
  assert.ok(ui.find(node => node.props['data-online-related-loading'] !== undefined))
  assert.equal(ui.text().includes('正在加载详情'), false)
  const response = relatedRows(); response.series.reverse()
  pending.resolve(response); await flush()
  const groups = ui.all().filter(node => node.props['data-online-related-group'] !== undefined)
  assert.deepEqual(groups.map(node => node.props['data-online-related-group']), ['series', 'related', 'name_related'])
  assert.deepEqual(ui.all().filter(node => node.props['data-online-related-id'] !== undefined).map(node => node.props['data-online-related-id']), ['season-1', 'season-3', 'same-type', 'possible'])
  assert.ok(nodeText(ui.find(node => node.props['data-online-current-season'] !== undefined)).includes('第 2 季'))
  assert.ok(ui.text().includes('根据片名与季号整理'))
  assert.ok(ui.text().includes('可能并非同一系列'))
  assert.equal(ui.text().includes('TMDB'), false)
})

test('a related card opens its stable same-source detail without autoplay and back keeps the browse results', async t => {
  const calls = []
  const { ui, players } = await setup(t, async path => {
    calls.push(path)
    if (path.includes('/detail?')) return { ...show(), item: { ...show().item, title: new URL(path, 'https://jzmedia.invalid').searchParams.get('external_id') === 'season-3' ? '第三季详情' : '合成剧集' } }
    return detailRequest(path)
  }, { relatedRequest: async path => relatedRows(new URL(path, 'https://jzmedia.invalid').searchParams.get('external_id')) })
  await openShow(ui)
  ui.find(node => node.props['data-online-related-id'] === 'season-3').props.onClick(); await flush()
  const opened = new URL(calls.at(-1), 'https://jzmedia.invalid')
  assert.equal(opened.pathname, '/api/online/catalog/detail')
  assert.equal(opened.searchParams.get('source_id'), SOURCE_A)
  assert.equal(opened.searchParams.get('external_id'), 'season-3')
  assert.ok(ui.text().includes('第三季详情'))
  assert.equal(players.length, 0)
  const searches = calls.filter(path => path.includes('/search?')).length
  ui.find(node => node.type === 'button' && nodeText(node) === '返回搜索结果').props.onClick(); await flush()
  assert.ok(ui.text().includes('合成电影'))
  assert.equal(calls.filter(path => path.includes('/search?')).length, searches)
})

test('deferred recommendations require one explicit retry and failures leave the detail playable', async t => {
  let calls = 0
  const { ui, players } = await setup(t, detailRequest, { relatedRequest: async () => {
    calls++
    if (calls === 1) return relatedRows('movie-1', { status: 'deferred' })
    if (calls === 2) throw new Error('signed upstream details must not be displayed')
    return relatedRows('movie-1', { current_season: null })
  } })
  await openShow(ui)
  assert.ok(ui.find(node => node.props['data-online-related-deferred'] !== undefined))
  await flush(); assert.equal(calls, 1)
  ui.find(node => node.props['data-online-related-retry'] !== undefined).props.onClick(); await flush()
  assert.ok(ui.find(node => node.props['data-online-related-error'] !== undefined))
  assert.ok(selectedEntry(ui))
  assert.equal(ui.text().includes('signed upstream'), false)
  ui.find(node => node.props['data-online-related-retry'] !== undefined).props.onClick(); await flush()
  assert.equal(calls, 3)
  assert.equal(ui.find(node => node.props['data-online-current-season'] !== undefined), undefined, 'An unnumbered title is never labelled first season')
  selectedEntry(ui).props.onClick(); await flush()
  assert.equal(players.length, 1)
})

test('choosing another season replaces a deep-link identity before a later refresh can reopen the old season', async t => {
  const { ui, navigations, players } = await setup(t, detailRequest, {
    routeQuery: { source: SOURCE_A, item: 'movie-1' },
    relatedRequest: async path => relatedRows(new URL(path, 'https://jzmedia.invalid').searchParams.get('external_id')),
  })
  assert.ok(selectedEntry(ui))
  ui.find(node => node.props['data-online-related-id'] === 'season-3').props.onClick(); await flush()
  assert.deepEqual(navigations.at(-1), { path: '/online', query: { source: SOURCE_A, item: 'season-3' } })
  assert.equal(players.length, 0)
})

test('playing an episode aborts pending recommendations before reading selected progress', async t => {
  const pending = deferred(), signals = []
  let progressReadAfterAbort = false
  const { ui, players } = await setup(t, detailRequest, {
    relatedRequest: (_path, options) => { signals.push(options.signal); return pending.promise },
    stateRequest: async (path, options) => {
      if (path.includes('/library?')) return { items: [], total: 0, offset: 0, scope_token: SCOPE }
      if (options?.signal && path.includes('episode_id=')) progressReadAfterAbort = signals[0]?.aborted === true
      return itemState()
    },
  })
  await openShow(ui)
  selectedEntry(ui).props.onClick(); await flush()
  assert.equal(signals[0].aborted, true)
  assert.equal(progressReadAfterAbort, true)
  assert.equal(players.length, 1)
  pending.resolve(relatedRows()); await flush()
  assert.equal(ui.find(node => node.props['data-online-related-id'] !== undefined), undefined)
  assert.ok(ui.find(node => node.props['data-stub-player'] !== undefined))
})

test('line changes, returning and permission cleanup discard pending related work and late posters', async t => {
  const work = [], signals = []
  const { ui, cleanup } = await setup(t, detailRequest, { relatedRequest: (_path, options) => {
    const pending = deferred(); work.push(pending); signals.push(options.signal); return pending.promise
  } })
  await openShow(ui)
  ui.find(node => node.props['data-online-line'] === LINE).props.onClick(); await flush()
  assert.equal(signals[0].aborted, true)
  work[0].resolve(relatedRows()); await flush()
  assert.equal(ui.find(node => node.props['data-online-related-id'] !== undefined), undefined)
  ui.find(node => node.props['data-online-related-retry'] !== undefined).props.onClick(); await flush()
  ui.find(node => node.type === 'button' && nodeText(node) === '返回搜索结果').props.onClick(); await flush()
  assert.equal(signals[1].aborted, true)
  work[1].resolve(relatedRows()); await flush()
  ui.find(node => node.props['data-online-result'] === 'movie-1').props.onClick(); await flush()
  await cleanup()
  assert.equal(signals[2].aborted, true)
  work[2].resolve(relatedRows()); await flush()
  assert.equal(ui.find(node => node.props['data-online-related-id'] !== undefined), undefined)
  assert.equal(ui.find(node => node.props['data-online-detail'] !== undefined), undefined)
})

test('temporary verification clears related posters and restores them without replacing the selected detail', async t => {
  let reads = 0
  const { ui, viewing, players } = await setup(t, detailRequest, { session: { mode: 'private' },
    relatedRequest: async () => { reads++; return relatedRows() } })
  await openShow(ui)
  assert.ok(ui.find(node => node.props['data-online-related-id'] === 'season-1'))
  viewing.suspended = true; await flush()
  assert.equal(ui.find(node => node.props['data-online-related-id'] !== undefined), undefined)
  viewing.suspended = false; await flush()
  assert.equal(reads, 2)
  assert.ok(selectedEntry(ui))
  selectedEntry(ui).props.onClick(); await flush()
  const playerNode = ui.find(node => node.props['data-stub-player'] !== undefined)
  viewing.suspended = true; await flush(); viewing.suspended = false; await flush()
  assert.equal(reads, 2, 'An active player cannot start a new background recommendation request')
  assert.equal(ui.find(node => node.props['data-stub-player'] !== undefined), playerNode)
  assert.equal(players.length, 1)
})

test('returning after another season restores the original browse button without scrolling it again', async t => {
  const { ui } = await setup(t, detailRequest, { relatedRequest: async () => relatedRows() })
  const previousDocument = globalThis.document
  const document = { body: {}, activeElement: null }
  const focused = []
  const poster = { isConnected: true, disabled: false,
    hasAttribute: name => name === 'data-online-result', getAttribute: () => 'movie-1',
    focus: options => { focused.push(options); document.activeElement = poster } }
  globalThis.document = document
  t.after(() => { if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument })
  document.activeElement = poster
  await openShow(ui)
  document.activeElement = { hasAttribute: name => name === 'data-online-related-id', getAttribute: () => 'season-1' }
  ui.find(node => node.props['data-online-related-id'] === 'season-1').props.onClick(); await flush()
  document.activeElement = document.body
  ui.find(node => node.type === 'button' && nodeText(node) === '返回搜索结果').props.onClick(); await flush()
  assert.equal(document.activeElement, poster, 'Other seasons retain the original browse focus target')
  assert.deepEqual(focused, [{ preventScroll: true }])
})

test('returning to refreshed rows finds the replacement button by stable identity', async t => {
  const { ui } = await setup(t, detailRequest, { relatedRequest: async () => relatedRows() })
  const previousDocument = globalThis.document, document = { body: {}, activeElement: null }
  let replacementFocused = 0, selector = ''
  const original = { isConnected: true, disabled: false,
    hasAttribute: name => name === 'data-online-result', getAttribute: () => 'movie-1',
    focus: () => { throw new Error('Detached original button cannot own focus') } }
  const replacement = { isConnected: true, disabled: false, focus: options => {
    assert.deepEqual(options, { preventScroll: true }); replacementFocused++; document.activeElement = replacement
  } }
  globalThis.document = document
  t.after(() => { if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument })
  document.activeElement = original
  await openShow(ui)
  original.isConnected = false
  const browser = ui.find(node => node.props['data-online-browser'] !== undefined)
  browser.querySelector = value => { selector = value; return replacement }
  document.activeElement = document.body
  ui.find(node => node.type === 'button' && nodeText(node) === '返回搜索结果').props.onClick(); await flush()
  assert.equal(selector, '[data-online-result="movie-1"]')
  assert.equal(replacementFocused, 1)
  assert.equal(document.activeElement, replacement)
})

test('disabled online sources do not hide saved records or their removal actions', async t => {
  const record = { item: { id: ITEM, source_id: SOURCE_A, external_id: 'show-1', kind: 'tv', title: '已停用来源的收藏' },
    source: { ...sources.items[0], enabled: false }, favorite: true, progress: { identity: identity(EP1), position: 5, duration: 24 }, available: false, reason: 'online_source_disabled' }
  const writes = []
  const { ui } = await setup(t, async () => ({ enabled: false, items: [] }), { stateRequest: async (path, options) => {
    if (options?.method) { writes.push(options); return { item_id: ITEM, favorite: false, scope_token: SCOPE } }
    return { items: [record], total: 1, offset: 0, scope_token: SCOPE }
  } })
  assert.equal(ui.text().includes('已停用来源的收藏'), true)
  assert.equal(ui.find(node => node.props['data-online-resume'] === ITEM).props.disabled, true)
  ui.find(node => node.props['data-online-favorite'] === ITEM).props.onClick(); await flush()
  assert.equal(writes[0].headers['X-Online-State-Scope'], SCOPE)
  assert.equal(JSON.parse(writes[0].body).favorite, false)
})

test('an explicit playback reads its selected line progress before mounting the player', async t => {
  const pending = deferred()
  const saved = { identity: identity(EP1), position: 11.5, duration: 24, paused: true, completed: false, updated_at: 10, writer_revision: 4 }
  const { ui, players, writers } = await setup(t,
    async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : show(),
    { stateRequest: async path => path.includes('/library?') ? { items: [], total: 0, offset: 0, scope_token: SCOPE }
      : path.includes('?') ? pending.promise : itemState() })
  await openShow(ui)
  selectedEntry(ui).props.onClick(); await flush()
  assert.equal(players.length, 0)
  pending.resolve(itemState({ selected_progress: saved })); await flush()
  assert.equal(players.length, 1); assert.equal(players[0].props.initialPosition, 11.5)
  assert.equal(writers[0].options.scopeToken, SCOPE); assert.equal(writers[0].options.revision, 4)
  players[0].progress({ identity: identity(EP1), position: 0, duration: 24, first_frame: false })
  assert.equal(writers[0].snapshots.length, 0)
})

test('autoplay remains off by default and a real parent ended signal closes at the first episode', async t => {
  let details = 0
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : (++details, show()))
  await openShow(ui); selectedEntry(ui).props.onClick(); await flush()
  assert.equal(ui.find(node => node.props['data-online-autoplay'] !== undefined).props['onUpdate:modelValue'] !== undefined, true)
  players[0].ended(); await flush()
  assert.equal(players.length, 1); assert.equal(details, 1)
  assert.equal(ui.find(node => node.props['data-stub-player'] !== undefined), undefined)
})

test('enabled autoplay refreshes the same catalogue and advances once, while stale ended cannot advance a new run', async t => {
  let details = 0
  const { ui, players } = await setup(t, async path => path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : (++details, show()), { autoplay: true })
  await openShow(ui); selectedEntry(ui).props.onClick(); await flush()
  const old = players[0]
  old.ended(); await flush()
  assert.equal(details, 2); assert.equal(players.length, 2)
  assert.equal(players[1].props.onlineIdentity.episode_id, EP2)
  old.ended(); await flush()
  assert.equal(details, 2); assert.equal(players.length, 2)
  players[1].ended(); await flush()
  assert.equal(players.length, 2)
  assert.equal(ui.text().includes('当前线路已播放完毕'), true)
})

test('turning autoplay off cancels a pending next-detail result instead of opening another player', async t => {
  const pending = deferred(); let details = 0
  const { ui, players } = await setup(t, path => Promise.resolve(path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : ++details === 1 ? show() : pending.promise), { autoplay: true })
  await openShow(ui); selectedEntry(ui).props.onClick(); await flush()
  players[0].ended(); await flush()
  assert.equal(ui.find(node => node.props['data-online-next-loading'] !== undefined) !== undefined, true)
  const toggle = ui.find(node => node.props['data-online-autoplay'] !== undefined)
  toggle.props['onUpdate:modelValue'](false); toggle.props.onChange()
  pending.resolve(show()); await flush()
  assert.equal(players.length, 1)
  assert.equal(ui.find(node => node.props['data-online-next-loading'] !== undefined), undefined)
})

test('clearing history adopts the new scope epoch for later explicit favorite actions', async t => {
  const newer = 'b'.repeat(64), writes = []
  const record = { item: { id: ITEM, source_id: SOURCE_A, external_id: 'show-1', kind: 'tv', title: '合成剧集' }, source: sources.items[0], favorite: true,
    progress: { identity: identity(EP1), position: 5, duration: 24 }, available: true }
  let scope = SCOPE
  const { ui } = await setup(t, async () => sources, { stateRequest: async (_path, options) => {
    if (options?.method === 'DELETE') { writes.push(options); scope = newer; return { deleted: 1, scope_token: newer } }
    if (options?.method === 'PUT') { writes.push(options); return { favorite: false, scope_token: newer } }
    return { items: [record], total: 1, offset: 0, scope_token: scope }
  } })
  ui.find(node => node.props['data-online-history-delete'] === ITEM).props.onClick(); await flush()
  ui.find(node => node.props['data-online-favorite'] === ITEM).props.onClick(); await flush()
  assert.equal(writes[0].headers['X-Online-State-Scope'], SCOPE)
  assert.equal(writes[1].headers['X-Online-State-Scope'], newer)
})

const cacheableSources = (configuration = 'b'.repeat(64), scope = SCOPE) => ({ enabled: true, scope_token: scope,
  items: [{ ...sources.items[0], configuration_token: configuration, capabilities: { browse: true } }] })

test('warm route reentry verifies sources before restoring the category and page without another provider read', async t => {
  clearOnlineBrowse()
  const catalogue = cacheableSources(), calls = []
  const request = async path => {
    calls.push(path)
    if (path.endsWith('/sources')) return catalogue
    const page = Number(new URL(path, 'https://jzmedia.invalid').searchParams.get('page'))
    return { ...rows, page, page_count: 2, categories: [{ id: '71', name: '都市脑洞' }] }
  }
  const first = await setup(t, request)
  first.ui.find(node => node.props['data-online-category'] === '71').props.onClick(); await flush()
  first.ui.find(node => node.type === 'button' && node.children.some(child => child.text === '下一页')).props.onClick(); await flush()
  first.ui.app.unmount()
  const verified = deferred(), repeatCalls = []
  const second = await setup(t, path => { repeatCalls.push(path); return verified.promise })
  assert.equal(second.ui.text().includes('合成电影'), false, 'no snapshot may render before sources authorization')
  assert.equal(repeatCalls.length, 1)
  verified.resolve(catalogue); await flush()
  assert.equal(second.ui.text().includes('合成电影'), true)
  assert.equal(second.ui.text().includes('第 2 / 2 页'), true)
  assert.equal(second.ui.find(node => node.props['data-online-category'] === '71').props['aria-pressed'], true)
  assert.equal(repeatCalls.length, 1, 'the verified warm page should not download catalogue metadata again')
})

test('source configuration changes and device scope changes reject previous page snapshots', async t => {
  clearOnlineBrowse()
  const first = await setup(t, async path => path.endsWith('/sources') ? cacheableSources() : rows)
  first.ui.app.unmount()
  for (const catalogue of [cacheableSources('c'.repeat(64)), cacheableSources('b'.repeat(64), 'd'.repeat(64))]) {
    const calls = []
    const next = await setup(t, async path => { calls.push(path); return path.endsWith('/sources') ? catalogue
      : { ...rows, items: [{ ...rows.items[0], title: '重新核对的目录' }] } })
    assert.equal(calls.filter(path => path.includes('/browse?')).length, 1)
    assert.equal(next.ui.text().includes('重新核对的目录'), true)
    next.ui.app.unmount()
  }
})

test('an expired snapshot restores its navigation and fetches current directory data', async t => {
  clearOnlineBrowse()
  const catalogue = cacheableSources()
  onlineBrowseMemory.save({ scope: onlineBrowseScope(SCOPE, { mode: 'normal', generation: 1, revision: 0 }),
    source: catalogue.items[0], categoryId: '71', query: '', loadedAt: Date.now() - 61000,
    response: { ...rows, page: 2, page_count: 2, categories: [{ id: '71', name: '都市脑洞' }] } })
  const calls = []
  const { ui } = await setup(t, async path => { calls.push(path); return path.endsWith('/sources') ? catalogue
    : { ...rows, page: 2, page_count: 2, categories: [{ id: '71', name: '都市脑洞' }] } })
  const request = new URL(calls.find(path => path.includes('/browse?')), 'https://jzmedia.invalid')
  assert.equal(request.searchParams.get('page'), '2')
  assert.equal(request.searchParams.get('category_id'), '71')
  assert.equal(ui.text().includes('第 2 / 2 页'), true)
})

test('explicit refresh keeps the existing wall visible while replacing directory data and retries the posters', async t => {
  clearOnlineBrowse()
  const pending = deferred()
  let reads = 0
  const { ui } = await setup(t, async path => path.endsWith('/sources') ? cacheableSources() : ++reads === 1 ? rows : pending.promise)
  ui.find(node => node.props['data-online-refresh'] !== undefined).props.onClick(); await flush()
  assert.equal(reads, 2, 'explicit refresh bypasses the warm directory snapshot')
  assert.equal(ui.text().includes('合成电影'), true)
  assert.equal(ui.text().includes('正在更新结果'), true)
  assert.equal(ui.find(node => node.props['data-online-result'] === 'movie-1').props.disabled, true)
  pending.resolve({ ...rows, items: [{ ...rows.items[0], title: '新目录片名' }] }); await flush()
  assert.equal(ui.text().includes('新目录片名'), true)
  assert.equal(ui.text().includes('正在更新结果'), false)
  assert.equal(ui.find(node => node.props['data-online-result'] === 'movie-1').props.disabled, false)
})

test('locking clears the warm directory and a cancelled update cannot refill it after leaving', async t => {
  clearOnlineBrowse()
  const pending = deferred()
  let reads = 0
  const { ui, cleanup } = await setup(t, async path => path.endsWith('/sources') ? cacheableSources() : ++reads === 1 ? rows : pending.promise)
  ui.find(node => node.props['data-online-refresh'] !== undefined).props.onClick(); await flush()
  await cleanup(); await flush()
  pending.resolve({ ...rows, items: [{ ...rows.items[0], title: '撤销后的旧结果' }] }); await flush()
  ui.app.unmount()
  assert.equal(ui.text().includes('撤销后的旧结果'), false)
  assert.deepEqual(onlineBrowseMemory.stats(), { pages: 0, bytes: 0 })
})

test('new and cached page navigation captures the new render while refresh and detail return preserve position', async t => {
  clearOnlineBrowse()
  const catalogue = cacheableSources()
  let reads = 0
  const { ui } = await setup(t, async path => {
    if (path.endsWith('/sources')) return catalogue
    if (path.includes('/detail?')) return show()
    reads++
    const params = new URL(path, 'https://jzmedia.invalid').searchParams
    const page = Number(params.get('page')), category = params.get('category_id') || 'initial'
    return { ...rows, page, page_count: 2, items: [{ ...rows.items[0], external_id: category + '-' + page }],
      categories: [{ id: '71', name: '都市脑洞' }] }
  })
  const previousWindow = globalThis.window
  const moves = []
  const viewport = { scrollY: 900, scrollTo(value) { this.scrollY = value.top; moves.push(['restore', value.top]) } }
  globalThis.window = viewport
  t.after(() => { if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow })
  const root = ui.find(node => node.props['data-online-browser'] !== undefined)
  root.querySelectorAll = () => ui.all().filter(node => node.props['data-online-browse-id'] !== undefined).map(node => ({
    dataset: { onlineBrowseId: node.props['data-online-browse-id'] },
    getBoundingClientRect: () => ({ top: 1200 - viewport.scrollY, bottom: 1400 - viewport.scrollY }),
  }))
  root.scrollIntoView = () => { viewport.scrollY = 1000; moves.push(['top', 1000]) }

  ui.find(node => node.props['data-online-category'] === '71').props.onClick(); await flush()
  let saved = onlineBrowseMemory.last(onlineBrowseScope(SCOPE, { mode: 'normal', generation: 1, revision: 0 }), catalogue.items)
  assert.equal(saved.position.anchor.id, '71-1', 'the new category cannot store the preceding render anchor')
  assert.deepEqual(moves, [['top', 1000]])

  viewport.scrollY = 1300
  ui.find(node => node.type === 'button' && node.children.some(child => child.text === '下一页')).props.onClick(); await flush()
  saved = onlineBrowseMemory.last(onlineBrowseScope(SCOPE, { mode: 'normal', generation: 1, revision: 0 }), catalogue.items)
  assert.equal(saved.position.anchor.id, '71-2')
  assert.equal(moves.filter(([kind]) => kind === 'top').length, 2)

  const before = reads
  viewport.scrollY = 1300
  ui.find(node => node.type === 'button' && node.children.some(child => child.text === '上一页')).props.onClick(); await flush()
  assert.equal(reads, before, 'returning to the previous page uses its verified warm data')
  assert.equal(moves.filter(([kind]) => kind === 'top').length, 3, 'cached pagination still starts at the results top')

  viewport.scrollY = 1100
  ui.find(node => node.props['data-online-refresh'] !== undefined).props.onClick(); await flush()
  assert.equal(viewport.scrollY, 1100, 'explicit refresh preserves the visible poster position')
  assert.equal(moves.filter(([kind]) => kind === 'top').length, 3)
  ui.find(node => node.props['data-online-result'] === '71-1').props.onClick(); await flush()
  viewport.scrollY = 0
  ui.find(node => node.type === 'button' && node.children.some(child => child.text === '返回搜索结果')).props.onClick(); await flush()
  assert.equal(viewport.scrollY, 1100, 'returning from details restores the poster anchor')
})

test('an empty previous result cannot masquerade as a completed search while the next query loads or fails', async t => {
  const pending = deferred()
  const { ui } = await setup(t, async path => {
    if (path.endsWith('/sources')) return sources
    return path.includes('q=none') ? { ...rows, items: [], total: 0 } : pending.promise
  })
  search(ui, 'none'); await flush()
  assert.ok(ui.text().includes('没有找到相关内容'))
  search(ui, 'next'); await flush()
  assert.ok(ui.text().includes('正在搜索'))
  assert.equal(ui.text().includes('没有找到相关内容'), false)
  pending.reject({ code: 'provider_invalid_response' }); await flush()
  assert.ok(ui.text().includes('搜索失败'))
  assert.equal(ui.text().includes('没有找到相关内容'), false)
})

test('a successful private-session revalidation preserves the selected detail and active player', async t => {
  const calls = [], resumedState = deferred()
  let delayState = false, resumedSignal = null, libraryReads = 0
  const { ui, viewing, players, writers } = await setup(t, async path => {
    calls.push(path)
    return path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : show()
  }, { session: { mode: 'private' }, stateRequest: async (path, options) => {
    if (path.includes('/library?')) { libraryReads++; return { items: [], total: 0, offset: 0, scope_token: SCOPE } }
    if (delayState) { resumedSignal = options.signal; return resumedState.promise }
    return itemState()
  } })
  await openShow(ui)
  selectedEntry(ui).props.onClick(); await flush()
  assert.equal(players.length, 1)
  const player = ui.find(node => node.props['data-stub-player'] !== undefined)
  viewing.suspended = true; await flush()
  assert.equal(ui.find(node => node.props['data-stub-player'] !== undefined), player)
  assert.equal(writers[0].closes, 0, 'temporary verification cannot close the progress writer or stop playback')
  const previousLibraryReads = libraryReads
  delayState = true
  viewing.suspended = false; await flush()
  assert.ok(resumedSignal)
  assert.equal(resumedSignal.aborted, false)
  assert.equal(libraryReads, previousLibraryReads, 'the selected detail state is refreshed before the secondary shelf')
  resumedState.resolve(itemState({ favorite: true })); await flush()
  assert.equal(resumedSignal.aborted, false)
  assert.equal(libraryReads, previousLibraryReads + 1)
  assert.equal(ui.find(node => node.props['data-stub-player'] !== undefined), player)
  assert.equal(players.length, 1)
  assert.equal(writers[0].closes, 0)
  assert.ok(ui.find(node => node.props['data-online-detail'] !== undefined))
  assert.ok(selectedEntry(ui))
  assert.equal(ui.find(node => node.props['data-online-favorite'] === ITEM).props.disabled, false)
  assert.equal(ui.text().includes('取消收藏'), true, 'the delayed verified detail state must become usable')
  assert.equal(calls.filter(path => path.includes('/detail?')).length, 1, 'an unchanged valid session keeps the selected detail')
})

test('temporary verification cancels an unfinished detail and retries it without accepting its late response', async t => {
  const pending = deferred(), calls = []
  let details = 0
  const { ui, viewing } = await setup(t, async (path, options) => {
    calls.push({ path, signal: options.signal })
    if (path.endsWith('/sources')) return sources
    if (path.includes('/search?')) return rows
    return ++details === 1 ? pending.promise : show()
  }, { session: { mode: 'private' } })
  search(ui, '合成'); await flush()
  ui.find(node => node.props['data-online-result'] === 'movie-1').props.onClick(); await flush()
  viewing.suspended = true; await flush()
  assert.equal(calls.find(call => call.path.includes('/detail?')).signal.aborted, true)
  pending.resolve({ ...show(), item: { ...show().item, title: '失效请求的迟到详情' } }); await flush()
  viewing.suspended = false; await flush()
  assert.equal(details, 2)
  assert.equal(ui.text().includes('失效请求的迟到详情'), false)
  assert.ok(ui.find(node => node.props['data-online-detail'] !== undefined))
  assert.ok(selectedEntry(ui))
})
