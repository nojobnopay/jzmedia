import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

const vue = { ...Vue, vShow: {}, vModelText: {}, vModelSelect: {}, vModelCheckbox: {} }
const blank = { setup: () => () => Vue.h('div') }
const facets = label => ({ genres: [{ value: label, count: 1 }], regions: [], countries: [],
  years: [], decades: [], tags: [], status: [], collections: [],
  watched: { watched: 0, unwatched: 1 }, ratings: { tmdb: [], custom: [], douban: [] } })

async function wall(t, kind, { query = {}, snapshot = null, components = {}, writeRequest, item } = {}) {
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
  const listRow = item || (kind === 'movie'
    ? { id: 1, title: '合成电影', tmdb_id: 101, genres: [], version_count: 1 }
    : { id: 1, title: '合成剧集', tmdb_id: 101, genres: [], season_count: 1, episode_count: 1 })
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
      if (writeRequest && options) return writeRequest(path, options)
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
    ...components,
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
  test(kind + ' wall keeps titles separate and replaces failed posters without retrying stale URLs', async t => {
    const item = Vue.reactive({ id: 201, tmdb_id: 501, title: '一部名字很长的作品', year: 2023,
      version_count: 2, season_count: 3, episode_count: 16, genres: ['悬疑'], region: '华语',
      tmdb_rating: 8.6, custom_rating: 7.2, status: 'Ended', poster_path: '/posters/201.jpg' })
    const ui = await wall(t, kind, { item })
    ui.facetRequests[0].resolve(facets('悬疑'))
    await flush()
    const byClass = name => ui.find(node => node.props.class?.split(' ').includes(name))
    const image = () => ui.find(node => node.type === 'img' && node.parent === byClass('poster-wrap'))
    const reads = ui.requests.slice()

    assert.equal(nodeText(byClass('card-title')), item.title)
    const metadata = nodeText(byClass('card-metadata'))
    assert.match(metadata, /2023.*8\.6/)
    assert.match(nodeText(byClass('card-meta')), kind === 'movie' ? /华语 · 悬疑/ : /3 季 · 16 集 · 已完结/)
    if (kind === 'movie') {
      assert.match(metadata, /2023.*×2.*7\.2/)
      assert.equal(byClass('poster-play').props['aria-label'], '播放 ' + item.title)
    }
    const original = image()
    assert.equal(original.props.loading, 'lazy')
    original.props.onError({ currentTarget: original })
    await flush()
    assert.equal(image(), undefined)
    assert.ok(byClass('artwork-placeholder'))
    item.poster_path = '/posters/301.jpg'
    await flush()
    assert.equal(image().props.src, '/posters/301.jpg')
    original.props.onError({ currentTarget: original })
    await flush()
    assert.equal(image().props.src, '/posters/301.jpg', 'late load failures cannot displace replacement art')
    const replacement = image()
    item.poster_path = ''
    await flush()
    assert.ok(byClass('artwork-placeholder'))
    item.poster_path = '/posters/201.jpg'
    await flush()
    replacement.props.onError({ currentTarget: replacement })
    await flush()
    assert.equal(image().props.src, '/posters/201.jpg', 'an old path can work again after the poster changes')
    assert.deepEqual(ui.requests, reads)
  })
}

test('movie selections and pending deletion previews cannot cross media libraries', async t => {
  const previews = []
  const dialog = { setup: (_props, { slots }) => () => Vue.h('section', { role: 'dialog' }, slots.default?.()) }
  const ui = await wall(t, 'movie', { components: { '../components/JzDialog.vue': { default: dialog } }, writeRequest(path, options) {
    assert.equal(path, '/api/movies/batch-delete')
    assert.deepEqual(JSON.parse(options.body), { ids: [1], dry_run: true })
    const request = deferred()
    previews.push(request)
    return request.promise
  } })
  ui.facetRequests[0].resolve(facets('剧情'))
  await flush()
  const select = () => ui.find(node => node.props['aria-label'] === '选择').props.onClick({ stopPropagation() {} })
  const toolbar = () => ui.find(node => node.props['aria-label'] === '多选操作')
  const remove = () => ui.find(node => node.props.title === '删除选中影片').props.onClick()
  const preview = { plans: [{ id: 1, title: '第一库预览' }], total_movies: 1 }
  select(); await flush(); remove()
  ui.switchMedia(9)
  await flush()
  assert.equal(toolbar(), undefined)
  select(); await flush(); remove()
  previews[0].resolve(preview)
  await flush()
  assert.equal(toolbar().props['aria-busy'], true, 'a late preview cannot release the new batch')
  assert.equal(ui.find(node => node.props.role === 'dialog'), undefined)
  previews[1].resolve({ ...preview, plans: [{ id: 1, title: '第二库预览' }] })
  await flush()
  assert.match(ui.text(), /第二库预览/)
  ui.switchMedia(11)
  await flush()
  assert.equal(toolbar(), undefined)
  assert.doesNotMatch(ui.text(), /第一库预览|第二库预览/)
  assert.equal(previews.length, 2, 'changing scopes never submits deletion')
})

test('TV shelf refresh waits for the final episode save and ignores a previous media library', async t => {
  let save, reloads = 0
  const ui = await wall(t, 'tv', { components: {
    '../components/ContinueWatchingRow.vue': { default: { setup(_props, { emit, expose }) {
      expose({ ready: async () => {}, reload: () => { reloads++ } })
      return () => Vue.h('button', { onClick: () => emit('resume', { title: '节目', progress: { version_id: 45 } }) }, '续播集')
    } } },
    '../components/PlayerModal.vue': { default: { setup(_props, { emit, expose }) {
      save = deferred()
      expose({ saveFinal: () => save.promise })
      return () => Vue.h('button', { onClick: () => emit('close') }, '关闭本集')
    } } },
  } })
  ui.facetRequests[0].resolve(facets('剧情'))
  await flush()
  async function click(text) { ui.find(node => node.text === text).props.onClick(); await flush() }
  await click('续播集')
  await click('关闭本集')
  assert.equal(reloads, 0)
  save.resolve()
  await flush()
  assert.equal(reloads, 1)
  await click('续播集')
  await click('关闭本集')
  ui.switchMedia(9)
  save.resolve()
  await flush()
  assert.equal(reloads, 1)
})

for (const kind of ['movie', 'tv']) {
  test(kind + ' route library switches send one wall request with the destination sort', async t => {
    const ui = await wall(t, kind)
    ui.facetRequests[0].resolve(facets('旧库'))
    await flush()
    const at = ui.requests.length
    ui.route.query = { media: '9', sort: 'rating', order: 'asc' }
    await flush()
    ui.switchMedia(9) // The global navigation acknowledges the successful route change.
    await flush()
    const wallRequests = ui.requests.slice(at).filter(path => path.startsWith(ui.listPath + '?'))
    assert.equal(wallRequests.length, 1)
    const params = new URLSearchParams(wallRequests[0].split('?')[1])
    assert.equal(params.get('media_library'), '9')
    assert.equal(params.get('sort'), 'rating')
    assert.equal(params.get('order'), 'asc')
    assert.equal(ui.facetRequests.length, 2)
  })
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
