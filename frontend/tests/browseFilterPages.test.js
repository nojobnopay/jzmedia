import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred } from './helpers/renderHarness.js'

const vue = { ...Vue, vShow: {}, vModelText: {}, vModelSelect: {}, vModelCheckbox: {} }
const blank = { setup: () => () => Vue.h('div') }
const facets = label => ({ genres: [{ value: label, count: 1 }], regions: [], countries: [],
  years: [], decades: [], tags: [], status: [], collections: [],
  watched: { watched: 0, unwatched: 1 }, ratings: { tmdb: [], custom: [], douban: [] } })

async function wall(t, kind, { query = {}, snapshot = null } = {}) {
  const globals = {
    localStorage: { getItem: () => null, setItem() {} },
    window: { addEventListener() {}, removeEventListener() {}, scrollY: 40 },
    requestAnimationFrame: callback => { callback(); return 1 },
  }
  const descriptors = new Map(Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value })
  let ui, unmounted = false
  const unmount = () => { if (!unmounted) { ui?.app.unmount(); unmounted = true } }
  t.after(() => {
    unmount()
    for (const [key, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  })

  const pagePath = kind === 'movie' ? '/' : '/tv'
  const route = Vue.reactive({ path: pagePath, query: { media: '7', ...query }, fullPath: pagePath })
  let media = 7, onLibraryChange, beforeLeave, filter, unsubscribed = false
  const requests = [], facetRequests = [], saved = [], replaced = []
  const facetPath = kind === 'movie' ? '/api/facets' : '/api/tv/facets'
  const listPath = kind === 'movie' ? '/api/search' : '/api/tv/shows'
  const listRow = kind === 'movie'
    ? { id: 1, title: '合成电影', tmdb_id: 101, genres: [], version_count: 1 }
    : { id: 1, title: '合成剧集', tmdb_id: 101, genres: [], season_count: 1, episode_count: 1 }
  const setRoute = destination => {
    route.path = destination.path
    route.query = destination.query
    route.fullPath = destination.path + '?' + new URLSearchParams(destination.query)
  }
  const filtersStub = {
    props: ['modelValue', 'facets', 'kind', 'open', 'scopeLabel', 'scopeKey', 'contextKey', 'loading', 'error'],
    emits: ['apply', 'retry', 'open', 'update:open'],
    setup(props, { emit }) {
      filter = { props, emit }
      return () => Vue.h('section', { 'data-test': 'filters' })
    },
  }
  const continueStub = {
    setup(_props, { expose }) {
      expose({ ready: async () => {} })
      return () => Vue.h('section')
    },
  }
  const component = await loadSfc(new URL(`../src/views/${kind === 'movie' ? 'Library' : 'Tv'}.vue`, import.meta.url), {
    vue,
    'vue-router': {
      useRoute: () => route,
      useRouter: () => ({ push() {}, replace: async destination => { replaced.push(destination); setRoute(destination) } }),
      onBeforeRouteLeave: callback => { beforeLeave = callback },
    },
    '../api.js': { posterUrl: path => path, api: (path, options) => {
      assert.equal(options, undefined, 'Browsing tests may only send read requests')
      requests.push(path)
      if (path.startsWith(facetPath + '?')) {
        const request = { path, ...deferred() }
        facetRequests.push(request)
        return request.promise
      }
      if (path.startsWith('/api/collections?')) return Promise.resolve({ items: [] })
      if (path.startsWith(listPath + '?')) return Promise.resolve({ items: [listRow], has_more: false, total: 1 })
      throw new Error('Unexpected request: ' + path)
    } },
    '../libraries.js': {
      currentMediaId: () => media, mediaParam: () => media, preferredVideoLibId: () => 3,
      loadLibs: async () => {}, listMediaLibs: () => [7, 9, 11, 13, 15].map(id => ({ id, name: '模拟库' + id })),
      switchMedia: id => { media = id; onLibraryChange?.() }, switchLib() {},
      onLibChange: callback => { onLibraryChange = callback; return () => { unsubscribed = true; onLibraryChange = null } },
    },
    '../browseHistory.js': {
      browseKey: (path, mediaId, params) => `${path}|${mediaId}|${params}`,
      saveBrowse: (key, value) => saved.push({ key, value }), readBrowse: () => snapshot,
      captureAnchor: () => ({ id: 1, top: 40 }), restoreBrowsePosition() {},
    },
    '../caps.js': { getCaps: async () => ({}) },
    '../useFocusTrap.js': { useFocusTrap() {} },
    '../components/BrowseFilters.vue': { default: filtersStub },
    '../components/ContinueWatchingRow.vue': { default: continueStub },
    ...Object.fromEntries(['PlayerModal', 'AiSearchPanel', 'ScanAction', 'ActionMenu', 'UploadDialog']
      .map(name => [`../components/${name}.vue`, { default: blank }])),
  })
  ui = renderHarness(component)
  await flush()
  return {
    ...ui, route, requests, facetRequests, saved, replaced, listPath,
    get filter() { return filter }, get unsubscribed() { return unsubscribed }, unmount,
    switchMedia(id) { assert.equal(typeof onLibraryChange, 'function'); media = id; onLibraryChange() },
    leave() { beforeLeave() },
  }
}

for (const kind of ['movie', 'tv']) {
  test(kind + ' filters clear the previous library immediately and ignore late successes or errors', async t => {
    const ui = await wall(t, kind)
    assert.equal(ui.filter.props.loading, true)
    ui.facetRequests[0].resolve(facets('旧库类型'))
    await flush()
    assert.equal(ui.filter.props.facets.genres[0].value, '旧库类型')

    ui.switchMedia(9)
    await flush()
    assert.deepEqual(ui.filter.props.facets.genres, [])
    assert.equal(ui.filter.props.scopeLabel, '模拟库9')
    assert.equal(ui.filter.props.loading, true)
    ui.switchMedia(11)
    await flush()
    ui.facetRequests[2].resolve(facets('当前库类型'))
    await flush()
    ui.facetRequests[1].reject(new Error('过期库错误'))
    await flush()
    assert.equal(ui.filter.props.facets.genres[0].value, '当前库类型')
    assert.equal(ui.filter.props.error, '')
    assert.equal(ui.filter.props.loading, false)

    ui.switchMedia(13)
    await flush()
    ui.switchMedia(15)
    await flush()
    ui.facetRequests[4].resolve(facets('最新库类型'))
    await flush()
    ui.facetRequests[3].resolve(facets('迟到类型'))
    await flush()
    assert.equal(ui.filter.props.facets.genres[0].value, '最新库类型')
    assert.equal(ui.filter.props.scopeKey, 15)
    assert.equal(ui.filter.props.loading, false)
  })

  test(kind + ' facet errors remain retryable without removing URL conditions absent from facets', async t => {
    const ui = await wall(t, kind, { query: { country: 'HK', genre: '旧标签类型', min_rating: '7.5', rating_source: 'custom' } })
    ui.facetRequests[0].reject(new Error('连接中断'))
    await flush()
    assert.match(ui.filter.props.error, /连接中断/)
    assert.equal(ui.filter.props.loading, false)
    assert.deepEqual(ui.filter.props.modelValue.countries, ['HK'])
    assert.deepEqual(ui.filter.props.modelValue.genres, ['旧标签类型'])
    assert.equal(ui.filter.props.modelValue.rating, 7.5)
    assert.match(ui.text(), /HK/)
    ui.filter.emit('retry')
    await flush()
    assert.equal(ui.filter.props.error, '')
    assert.equal(ui.filter.props.loading, true)
    assert.equal(ui.facetRequests.length, 2)
    ui.facetRequests[1].resolve(facets('可用类型'))
    await flush()
    assert.equal(ui.filter.props.error, '')
    assert.equal(ui.filter.props.loading, false)
    assert.deepEqual(ui.filter.props.modelValue.genres, ['旧标签类型'])
    assert.deepEqual(ui.filter.props.modelValue.countries, ['HK'])
  })

  test(kind + ' applying filters preserves media/query/sort, normalizes countries, and does not reload global facets', async t => {
    const ui = await wall(t, kind, { query: { q: '关键词', sort: 'rating', order: 'asc' } })
    ui.facetRequests[0].resolve(facets('剧情'))
    await flush()
    const draft = { ...ui.filter.props.modelValue, countries: ['hk'], regions: ['欧美'], watched: 0, rating: 0, ratingSource: 'custom' }
    const initialLists = ui.requests.filter(path => path.startsWith(ui.listPath + '?')).length
    ui.filter.emit('apply', draft)
    await flush()
    assert.equal(ui.replaced.length, 1)
    assert.equal(ui.facetRequests.length, 1)
    assert.equal(ui.requests.filter(path => path.startsWith(ui.listPath + '?')).length, initialLists + 1)
    const query = new URLSearchParams(ui.requests.filter(path => path.startsWith(ui.listPath + '?')).at(-1).split('?')[1])
    assert.equal(query.get('q'), '关键词')
    assert.equal(query.get('media_library'), '7')
    assert.equal(query.get('country'), 'HK')
    assert.equal(query.get('region'), null)
    assert.equal(query.get('min_rating'), '0')
    assert.equal(query.get('rating_source'), 'custom')
    assert.equal(query.get('watched'), '0')
    assert.equal(query.get('sort'), 'rating')
    assert.equal(query.get('order'), 'asc')
    assert.equal(ui.route.query.media, '7')
    assert.equal(ui.route.query.country, 'HK')
    assert.equal(ui.route.query.region, undefined)
    assert.deepEqual(draft.regions, ['欧美'])
  })

  test(kind + ' returning to a wall keeps filters closed and unmount ignores pending facet updates', async t => {
    const ui = await wall(t, kind, { query: { genre: '剧情' }, snapshot: { items: [{ id: 1, title: '缓存海报', genres: [] }], hasMore: false, filtersOpen: true } })
    ui.facetRequests[0].resolve(facets('剧情'))
    await flush()
    assert.equal(ui.filter.props.open, false)
    ui.filter.emit('update:open', true)
    await flush()
    ui.leave()
    assert.equal(ui.saved[0].value.filtersOpen, false)
    assert.equal(ui.saved[0].value.query.genre, '剧情')
    assert.equal(ui.saved[0].value.mediaId, 7)
    ui.filter.emit('retry')
    await flush()
    const props = ui.filter.props
    ui.unmount()
    assert.equal(ui.unsubscribed, true)
    ui.facetRequests[1].resolve(facets('卸载后迟到'))
    await flush()
    assert.equal(props.facets.genres[0].value, '剧情')
  })
}
