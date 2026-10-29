import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred } from './helpers/renderHarness.js'
import * as catalog from '../src/onlineCatalog.js'
import * as state from '../src/onlineState.js'

const SOURCE_A = 'src_' + '1'.repeat(32), SOURCE_B = 'src_' + '2'.repeat(32)
const sources = { enabled: true, items: [{ id: SOURCE_A, name: '来源 A', provider: 'maccms' }, { id: SOURCE_B, name: '来源 B', provider: 'maccms' }] }
const rows = { items: [{ external_id: 'movie-1', title: '合成电影', kind: 'movie', year: '2026' }], page: 1, page_count: 1, total: 1 }
const vue = { ...Vue, vModelText: {}, vModelSelect: {}, vModelCheckbox: {}, vShow: {} }
const SCOPE = 'a'.repeat(64)
async function setup(t, request, { stateRequest, autoplay = false, routeQuery = {} } = {}) {
  let cleanup = () => {}
  const players = [], writers = [], preferences = []
  const stateRead = stateRequest || (async path => path.includes('/library?')
    ? { items: [], total: 0, limit: 20, offset: 0, scope_token: SCOPE }
    : { item_id: path.split('/').at(-1).split('?')[0], favorite: false, last_progress: null, selected_progress: null, available: true, scope_token: SCOPE })
  const component = await loadSfc(new URL('../src/views/Online.vue', import.meta.url), {
    vue,
    'vue-router': { useRoute: () => ({ query: routeQuery }), useRouter: () => ({ replace: async () => {} }) },
    '../onlineCatalog.js': { ...catalog, createOnlineCatalogClient: () => catalog.createOnlineCatalogClient({ request }), createOnlineHealthClient: () => ({ read: async () => null, cancel() {}, close() {} }) },
    '../onlineState.js': { ...state, createOnlineStateClient: () => state.createOnlineStateClient({ request: stateRead }),
      loadOnlineAutoplay: () => autoplay, saveOnlineAutoplay: value => preferences.push(value),
      createOnlineProgressWriter: options => { const writer = { options, snapshots: [], sessions: [], session(value) { this.sessions.push(value) }, update(value) { this.snapshots.push(value) }, close: async () => {} }; writers.push(writer); return writer } },
    '../viewing.js': { isChildren: Vue.ref(false), viewing: Vue.reactive({ mode: 'normal', locallyLocked: false, suspended: false }), registerViewingPlaybackCleanup: callback => { cleanup = callback; return () => {} } },
    '../components/OnlinePlayerModal.vue': { default: { props: ['onlineIdentity', 'initialPosition', 'prepareSession'], setup: props => {
      const listeners = Vue.getCurrentInstance().vnode.props
      const player = { props, ended: identity => listeners.onEnded(identity || props.onlineIdentity),
        progress: value => listeners.onProgress(value), close: () => listeners.onClose(), session: value => props.prepareSession(value) }
      players.push(player)
      return () => Vue.h('div', { 'data-stub-player': true })
    } } },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  await flush()
  return { ui, cleanup: () => cleanup(), players, writers, preferences }
}
function search(ui, query) {
  ui.find(node => node.props.id === 'online-query').props['onUpdate:modelValue'](query)
  ui.find(node => node.props['data-online-search'] !== undefined).props.onSubmit({ preventDefault() {} })
}

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
