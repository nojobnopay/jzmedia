import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, deferred, flush, nodeText } from './helpers/renderHarness.js'

const link = { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', { href: props.to }, slots.default?.()) }
const vue = { ...Vue, vShow: {}, vModelText: {}, vModelSelect: {}, vModelCheckbox: {},
  resolveComponent: name => name.toLowerCase().replace('-', '') === 'routerlink' ? link : Vue.resolveComponent(name) }
const blank = { setup: () => () => Vue.h('div') }
const button = (ui, label) => ui.find(node => node.type === 'button' && nodeText(node).trim() === label)
const show = { id: 41, tmdb_id: 101, title: '合成剧集', year: 2026, media_library_id: 7, media_name: '测试媒体库', library_id: 8, library_name: '视频库 A',
  episode_count: 1, watched_count: 0, seasons: [{ season: 1, total: 1, distinct: 1, versions: 1, watched_count: 0 }], extras: [], genres: [], cast: [] }
const local = { show_id: 41, show_title: show.title, season: 1, name: '第一季', episode_count: 1, distinct_count: 1,
  total: 1, watched_count: 0, versions: [{ version: 1, count: 1, distinct: 1 }], has_more: false,
  episodes: [{ id: 701, season: 1, episode: 1, title: '本地第一集', exists: true }], next_episode: null, cast: [] }
const collection = { show_id: 41, tmdb_id: 101, media_library_id: 7, checked_at: 1780000000,
  status_text: '连载中', missing_seasons: [2], seasons: [
    { season: 0, name: '特别篇（SP）', official_count: 2, collected_count: 0, collection_state: 'uncollected', airing_state: 'unknown', sources: [] },
    { season: 1, name: '第一季', official_count: 12, collected_count: 8, collection_state: 'collected', airing_state: 'aired', sources: [{ show_id: 82, season: 3, library_name: '视频库 B' }] },
    { season: 2, name: '第二季', poster_url: '/api/tv/shows/41/seasons/2/poster', official_count: 12, collected_count: 0, collection_state: 'uncollected', airing_state: 'upcoming', air_date: '2027-01-01', sources: [] },
  ] }
const catalog = { show_id: 41, tmdb_id: 101, season: 1, name: '第一季', checked_at: 1780000000, items: [
  { tmdb_episode_id: 9001, season: 1, episode: 1, title: '官方第一集', collection_state: 'collected', airing_state: 'aired', sources: [{ show_id: 82, season: 3, episode_id: 910, episode: 6, library_name: '视频库 B' }] },
  { tmdb_episode_id: 9002, season: 1, episode: 2, title: '官方第二集', collection_state: 'uncollected', airing_state: 'unknown', sources: [] },
], sources: [] }

async function page(t, name, responder = () => undefined) {
  const globals = { window: { addEventListener() {}, removeEventListener() {} }, document: { addEventListener() {}, removeEventListener() {} } }
  const descriptors = new Map(Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value })
  const route = Vue.reactive({ params: name === 'TvShow' ? { id: '41' } : { showId: '41', season: '1' }, path: name === 'TvShow' ? '/tv/41' : '/tv/41/s/1', query: {} })
  const requests = [], pushed = []
  let player
  const component = await loadSfc(new URL(`../src/views/${name}.vue`, import.meta.url), {
    vue,
    'vue-router': { useRoute: () => route, useRouter: () => ({ push: path => pushed.push(path) }) },
    '../api.js': { posterUrl: path => '/posters/' + path, api: async (path, options) => {
      requests.push({ path, options })
      const result = responder(path, options)
      if (result !== undefined) return result
      if (path === '/api/tv/shows/41') return { ...show }
      if (path === '/api/tv/shows/41/collection') return structuredClone(collection)
      if (path.endsWith('/similar')) return { items: [] }
      if (path.endsWith('/catalog')) return structuredClone(catalog)
      if (path.includes('/seasons/1?')) return structuredClone(local)
      if (path.endsWith('/watched')) return { ok: true }
      throw new Error('Unexpected request: ' + path)
    } },
    '../libraries.js': { currentMediaId: () => 7, loadLibs: async () => {}, switchMedia: () => true },
    '../useFocusTrap.js': { useFocusTrap() {} },
    '../components/PlayerModal.vue': { default: { emits: ['watched', 'ended'], setup(_props, { emit }) {
      const instance = { emit, active: true }
      player = instance
      Vue.onUnmounted(() => { instance.active = false })
      return () => Vue.h('div', { class: 'test-player' })
    } } },
    ...Object.fromEntries(['MediaBackdrop', 'MediaOverview', 'SimilarRow', 'CastWall', 'TvBindingsDialog', 'TvOrganizeDialog', 'AiMatchSuggestions']
      .map(value => [`../components/${value}.vue`, { default: blank }])),
  })
  const ui = renderHarness(component)
  let unmounted = false
  const unmount = () => { if (!unmounted) { unmounted = true; ui.app.unmount() } }
  t.after(() => {
    unmount()
    for (const [key, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  })
  await flush()
  return { ...ui, route, requests, pushed, unmount, get player() { return player } }
}

test('show cards include SP and uncollected seasons only after the collection snapshot is known', async t => {
  const pending = deferred()
  const ui = await page(t, 'TvShow', path => path.endsWith('/collection') ? pending.promise : undefined)
  assert.doesNotMatch(ui.text(), /未收藏/)
  pending.resolve(structuredClone(collection))
  await flush()
  assert.match(ui.text(), /特别篇（SP）/)
  assert.match(ui.text(), /未收藏/)
  assert.match(ui.text(), /尚未播出/)
  assert.match(ui.text(), /已收藏 8\/12 集/)
  assert.ok(ui.find(node => node.type === 'a' && node.props.href === '/tv/41/s/2'))
  assert.ok(ui.find(node => node.type === 'a' && node.props.href === '/tv/82/s/3'))
  assert.ok(ui.find(node => node.type === 'img' && node.props.class === 'season-uncollected-image'))
  assert.ok(ui.requests.every(request => request.options?.method !== 'POST'))
})

test('an old server keeps local collection counts and explains the missing inventory API', async t => {
  const ui = await page(t, 'TvShow', path => path.endsWith('/collection') ? Promise.reject(new Error('404 not found')) : undefined)
  assert.match(ui.text(), /已收藏 1 集/)
  assert.doesNotMatch(ui.text(), /本页已登记|404 not found/)
  assert.match(ui.text(), /服务端尚未提供播出与收藏接口/)
  assert.match(ui.text(), /重启服务后重试（404）/)
  assert.ok(ui.find(node => node.type === 'a' && node.props.href === '/tv/41/s/1'))
})

test('missing official catalogs retain local counts without warning badges and explain the next step once', async t => {
  const snapshot = structuredClone(collection)
  Object.assign(snapshot.seasons[0], { local_count: 2, collection_state: 'uncertain', collection_reason: 'catalog_missing' })
  Object.assign(snapshot.seasons[1], { local_count: 3, collected_count: 0, collection_state: 'uncertain', collection_reason: 'catalog_missing' })
  snapshot.seasons.push({ season: 3, name: '第三季', local_count: 0, collected_count: 5, official_count: 12, collection_state: 'uncertain', collection_reason: 'catalog_missing', sources: snapshot.seasons[1].sources })
  snapshot.latest_episode = { season: 1, episode: 8, airing_state: 'aired', collection_state: 'uncertain', collection_reason: 'catalog_missing' }
  const ui = await page(t, 'TvShow', path => path.endsWith('/collection') ? snapshot : undefined)
  assert.match(ui.text(), /已收藏 2 集 · 官方 2 集/)
  assert.match(ui.text(), /已收藏 3 集 · 官方 12 集/)
  assert.match(ui.text(), /已确认收藏 5\/12 集/)
  assert.match(ui.text(), /最近播出 S01E08.*资料待补全/)
  assert.doesNotMatch(ui.text(), /待核对|编号待对照|匹配需确认|已收藏 3\s*\/|后台正在/)
  const notes = ui.all().filter(node => node.type === 'p' && String(node.props.class).includes('collection-explanation'))
  assert.equal(notes.length, 1)
  assert.match(nodeText(notes[0]), /进入「全部分集」可补充目录，无需重新匹配/)
  const badges = ui.all().filter(node => node.props.class === 'season-collection-badge')
  assert.deepEqual(badges.map(nodeText), ['未收藏'], 'only the actually uncollected season has a badge')
  assert.ok(ui.find(node => node.type === 'a' && node.props.href === '/tv/82/s/3'))
})

test('only seasons with actual numbering or match questions display their specific badge', async t => {
  const snapshot = structuredClone(collection)
  Object.assign(snapshot.seasons[0], { local_count: 2, collection_state: 'uncertain', collection_reason: 'numbering_unresolved' })
  Object.assign(snapshot.seasons[1], { local_count: 3, collection_state: 'uncertain', collection_reason: 'match_review' })
  snapshot.seasons.push({ season: 3, local_count: 1, collection_state: 'uncertain', collection_reason: 'show_unconfirmed' })
  const ui = await page(t, 'TvShow', path => path.endsWith('/collection') ? snapshot : undefined)
  const badges = ui.all().filter(node => node.props.class === 'season-collection-badge').map(nodeText)
  assert.deepEqual(badges, ['分集编号待对照', '分集匹配需确认', '未收藏'])
  assert.match(ui.text(), /查看本季分集编号与官方目录的对应关系/)
  assert.match(ui.text(), /查看本季分集的匹配信息/)
  assert.match(ui.text(), /已确认收藏 8\/12 集/)
  assert.doesNotMatch(ui.text(), /待核对|冲突/)
})

test('official episode rows distinguish missing metadata, numbering and match review while keeping source links', async t => {
  const component = await loadSfc(new URL('../src/components/TvSeasonCatalog.vue', import.meta.url), { vue })
  const reasons = ['catalog_missing', 'numbering_unresolved', 'match_review', 'coverage_incomplete']
  const ui = renderHarness(component, { data: { ...catalog, items: reasons.map((reason, index) => ({
    ...catalog.items[0], tmdb_episode_id: 9100 + index, episode: index + 1, collection_state: 'uncertain', collection_reason: reason,
  })) } })
  t.after(() => ui.app.unmount())
  assert.match(ui.text(), /资料待补全/)
  assert.match(ui.text(), /分集编号待对照/)
  assert.match(ui.text(), /分集匹配需确认/)
  assert.match(ui.text(), /收藏范围暂未确定/)
  assert.doesNotMatch(ui.text(), /未收藏|待核对|冲突/)
  assert.ok(ui.find(node => node.type === 'a' && node.props.href === '/tv/82/s/3/e/910'))
})

test('partly collected seasons keep local playback and watch scope, with official catalog available on demand', async t => {
  const ui = await page(t, 'SeasonView')
  assert.equal(button(ui, '已收藏').props['aria-pressed'], true)
  assert.ok(!ui.requests.some(request => request.path.endsWith('/catalog')))
  assert.match(ui.text(), /本页 0\/1 已看/)
  ui.find(node => node.type === 'button' && node.props['aria-label'] === '播放 S01E01').props.onClick({ stopPropagation() {} })
  await flush()
  button(ui, '全部分集').props.onClick()
  await flush()
  assert.match(ui.text(), /官方第二集/)
  assert.match(ui.text(), /播出时间未知/)
  assert.ok(ui.find(node => node.type === 'a' && node.props.href === '/tv/82/s/3/e/910'))
  assert.ok(!ui.all().some(node => node.type === 'a' && String(node.props.href).includes('/e/9002')))
  ui.player.emit('watched')
  await flush()
  assert.equal(button(ui, '全部分集').props['aria-pressed'], true, 'local data refresh must not change the selected list')
  assert.ok(ui.requests.some(request => request.path === '/api/tv/episodes/701/watched'))
  assert.ok(!ui.requests.some(request => request.path.includes('/9001/watched') || request.path.includes('/9002/watched')))
})

test('refreshing the same show reloads collection once after local data changes', async t => {
  let reads = 0
  const ui = await page(t, 'TvShow', path => {
    if (!path.endsWith('/collection')) return
    const snapshot = structuredClone(collection)
    snapshot.seasons[1].collected_count = ++reads === 1 ? 8 : 9
    return snapshot
  })
  assert.equal(reads, 1, 'initial identity watch performs one read')
  assert.match(ui.text(), /已收藏 8\/12 集/)
  await button(ui, '标记整剧已看').props.onClick()
  await flush()
  assert.equal(reads, 2, 'same-identity reload refreshes ownership without duplicate reads')
  assert.match(ui.text(), /已收藏 9\/12 集/)
})

test('a freshly loaded catalog updates the season summary without another snapshot request', async t => {
  const ui = await page(t, 'SeasonView', path => {
    if (path.endsWith('/collection')) {
      const snapshot = structuredClone(collection)
      Object.assign(snapshot.seasons[1], { local_count: 3, collected_count: 0, collection_state: 'uncertain', collection_reason: 'catalog_missing' })
      return snapshot
    }
    if (path.endsWith('/catalog')) return { ...catalog, official_count: 12, collected_count: 8, collection_state: 'collected', airing_state: 'aired' }
  })
  assert.match(ui.text(), /已收藏 3 集 · 官方 12 集/)
  assert.match(ui.text(), /无需重新匹配/)
  button(ui, '全部分集').props.onClick()
  await flush()
  assert.doesNotMatch(ui.text(), /收藏信息待补全|无需重新匹配/)
  assert.match(ui.text(), /已收藏 8\/12 集/)
  assert.equal(ui.requests.filter(request => request.path.endsWith('/collection')).length, 1)
})

test('navigating from a playing local season to an uncollected season disposes the player', async t => {
  const ui = await page(t, 'SeasonView', path => {
    if (path.includes('/seasons/2?')) return { ...local, season: 2, episode_count: 0, distinct_count: 0, episodes: [] }
    if (path.endsWith('/seasons/2/catalog')) return { ...catalog, season: 2 }
  })
  ui.find(node => node.type === 'button' && node.props['aria-label'] === '播放 S01E01').props.onClick({ stopPropagation() {} })
  await flush()
  const player = ui.player
  assert.equal(player.active, true)
  ui.route.params.season = '2'
  await flush()
  assert.equal(player.active, false)
  assert.equal(ui.find(node => node.props.class === 'test-player'), undefined)
  assert.equal(button(ui, '全部分集').props['aria-pressed'], true)
  assert.equal(button(ui, '标记本季全部版本已看'), undefined)
})

test('an announced season absent from the old local season route has a read-only catalog page', async t => {
  const ui = await page(t, 'SeasonView', path => path.includes('/seasons/1?') ? Promise.reject(new Error('404 season not found')) : undefined)
  assert.match(ui.text(), /合成剧集/)
  assert.match(ui.text(), /官方第一集/)
  assert.equal(button(ui, '全部分集').props['aria-pressed'], true)
  assert.equal(button(ui, '标记本季全部版本已看'), undefined)
  assert.equal(button(ui, '检查文件是否可用'), undefined)
  assert.doesNotMatch(ui.text(), /0\/0 已看|0 集 · 1 版本|剧季加载失败/)
})

test('a late official catalog cannot overwrite another season or survive unmount', async t => {
  const previous = deferred(), next = deferred()
  const ui = await page(t, 'SeasonView', path => {
    if (path.endsWith('/seasons/1/catalog')) return previous.promise
    if (path.endsWith('/seasons/2/catalog')) return next.promise
    if (path.includes('/seasons/2?')) return { ...local, season: 2, episode_count: 0, distinct_count: 0, episodes: [] }
  })
  button(ui, '全部分集').props.onClick()
  await flush()
  ui.route.params.season = '2'
  await flush()
  const previousRequest = ui.requests.find(request => request.path.endsWith('/seasons/1/catalog'))
  assert.equal(previousRequest.options.signal.aborted, true)
  previous.resolve({ ...catalog, items: [{ ...catalog.items[0], title: '迟到的第一季' }] })
  await flush()
  assert.doesNotMatch(ui.text(), /迟到的第一季/)
  const nextRequest = ui.requests.find(request => request.path.endsWith('/seasons/2/catalog'))
  ui.unmount()
  assert.equal(nextRequest.options.signal.aborted, true)
  const count = ui.requests.length
  next.resolve({ ...catalog, season: 2 })
  await flush()
  assert.equal(ui.requests.length, count)
})
