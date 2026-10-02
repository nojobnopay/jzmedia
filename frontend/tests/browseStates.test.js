import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

const vue = { ...Vue, vShow: {}, vModelText: {}, vModelSelect: {}, vModelCheckbox: {} }
const event = extra => ({ preventDefault() {}, stopPropagation() {}, ...extra })

async function collections(t) {
  const storageDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null } })
  t.after(() => {
    if (storageDescriptor) Object.defineProperty(globalThis, 'localStorage', storageDescriptor)
    else delete globalThis.localStorage
  })
  const libraryReady = deferred()
  const requests = []
  let media = 7, changed, unsubscribed = false
  const component = await loadSfc(new URL('../src/views/Collections.vue', import.meta.url), {
    vue,
    '../api.js': { posterUrl: path => path, api: path => {
      if (path.startsWith('/api/collections/suggest')) return Promise.resolve({})
      const request = { path, ...deferred() }
      requests.push(request)
      return request.promise
    } },
    '../libraries.js': {
      currentMediaId: () => media, mediaParam: () => media,
      loadLibs: () => libraryReady.promise,
      onLibChange: callback => { changed = callback; return () => { unsubscribed = true } },
    },
    '../usePolling.js': { usePolling: () => ({ start() {}, stop() {} }) },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  return { ...ui, requests, libraryReady,
    switchMedia(value) { media = value; changed() },
    get unsubscribed() { return unsubscribed },
  }
}

test('collections never flash empty while loading; failures offer retry, then successful empty/search results use distinct states', async t => {
  const ui = await collections(t)
  assert.match(ui.text(), /正在加载合集/)
  assert.doesNotMatch(ui.text(), /还没有合集|没有符合条件的合集/)
  ui.libraryReady.resolve()
  await flush()
  ui.requests[0].reject(new Error('服务暂不可用'))
  await flush()
  assert.match(ui.text(), /合集加载失败.*服务暂不可用/)
  assert.doesNotMatch(ui.text(), /还没有合集/)
  assert.ok(ui.find(node => node.props.role === 'alert'))
  ui.find(node => node.type === 'button' && nodeText(node) === '重新加载').props.onClick()
  await flush()
  assert.match(ui.text(), /正在加载合集/)
  ui.requests[1].resolve({ items: [] })
  await flush()
  assert.match(ui.text(), /还没有合集/)
  assert.doesNotMatch(ui.text(), /合集加载失败/)

  ui.find(node => node.type === 'input' && node.props['aria-label'] === '搜索合集').props['onUpdate:modelValue']('旅行')
  await flush()
  ui.find(node => node.type === 'form' && node.props.role === 'search').props.onSubmit(event())
  assert.equal(new URLSearchParams(ui.requests[2].path.split('?')[1]).get('q'), '旅行')
  ui.requests[2].resolve({ items: [] })
  await flush()
  assert.match(ui.text(), /没有符合条件的合集/)
  assert.doesNotMatch(ui.text(), /还没有合集/)
  ui.find(node => node.type === 'button' && nodeText(node) === '清除搜索').props.onClick()
  await flush()
  assert.equal(new URLSearchParams(ui.requests[3].path.split('?')[1]).has('q'), false)
})

test('collections discard a previous library response and clean the library listener on unmount', async t => {
  const ui = await collections(t)
  ui.libraryReady.resolve()
  await flush()
  ui.switchMedia(9)
  assert.match(ui.requests[1].path, /media_library=9/)
  ui.requests[1].resolve({ items: [{ id: 2, name: '新媒体库合集', member_count: 1 }] })
  await flush()
  assert.match(ui.text(), /新媒体库合集/)
  ui.requests[0].reject(new Error('旧媒体库请求失败'))
  await flush()
  assert.match(ui.text(), /新媒体库合集/)
  assert.doesNotMatch(ui.text(), /旧媒体库请求失败|合集加载失败/)
  ui.app.unmount()
  assert.equal(ui.unsubscribed, true)
})

test('shared search toolbar connects keyboard selection to combobox options and preserves input/composition events', async t => {
  const component = await loadSfc(new URL('../src/components/BrowseToolbar.vue', import.meta.url), { vue })
  const inputEvents = [], compositionEvents = []
  const props = Vue.reactive({
    id: 'test-search', label: '搜索电影', modelValue: '', suggestOpen: true, suggestIdx: -1,
    suggestItems: [{ id: 1, title: '合成影片', year: 2026 }], suggestPersons: [{ tmdb_id: 2, name: '示例演员', count: 3 }],
    filtersOpen: false, aiOpen: false,
    'onUpdate:modelValue': value => { props.modelValue = value },
    'onUpdate:filtersOpen': value => { props.filtersOpen = value },
    onInput: value => inputEvents.push(value), onCompositionend: value => compositionEvents.push(value),
    onMove: direction => { props.suggestIdx += direction },
  })
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  const input = () => ui.find(node => node.type === 'input')
  assert.equal(input().props['aria-controls'], 'test-search-suggestions')
  assert.equal(input().props['aria-activedescendant'], undefined)
  input().props.onKeydown[0](event({ key: 'ArrowDown' }))
  await flush()
  assert.equal(input().props['aria-activedescendant'], 'test-search-option-0')
  assert.equal(ui.find(node => node.props.id === 'test-search-option-0').props['aria-selected'], true)
  const typed = event({ target: { value: '电影' }, isComposing: true })
  input().props.onInput(typed)
  input().props.onCompositionend(typed)
  await flush()
  assert.equal(props.modelValue, '电影')
  assert.equal(inputEvents[0], typed)
  assert.equal(compositionEvents[0], typed)
  props.suggestOpen = false
  await flush()
  assert.equal(input().props['aria-expanded'], false)
  assert.equal(input().props['aria-activedescendant'], undefined)
  const filter = ui.find(node => node.type === 'button' && node.props['aria-controls'] === 'browse-filters')
  filter.props.onClick()
  await flush()
  assert.equal(filter.props['aria-expanded'], true)
})

test('episode detail retry recovers, and navigating between episodes cannot expose stale playable details or late errors', async t => {
  const route = Vue.reactive({ params: { epId: '1' } })
  const requests = []
  const blank = { setup: () => () => Vue.h('div') }
  const component = await loadSfc(new URL('../src/views/EpisodeView.vue', import.meta.url), {
    vue,
    'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
    '../api.js': { posterUrl: path => path, api: path => {
      const request = { path, ...deferred() }
      requests.push(request)
      return request.promise
    } },
    '../components/PlayerModal.vue': { default: blank },
    '../components/MediaBackdrop.vue': { default: blank },
    '../components/MediaOverview.vue': { default: blank },
    '../components/ActionMenu.vue': { default: blank },
    '../components/CastWall.vue': { default: blank },
    '../components/CrewRow.vue': { default: blank },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  const episode = id => ({ id, title: '示例分集' + id, show_title: '示例剧集', show_id: 7,
    season: 1, episode: Number(id), exists: true, cast: [] })
  assert.match(ui.text(), /正在加载分集/)
  requests[0].reject(new Error('连接中断'))
  await flush()
  assert.match(ui.text(), /分集加载失败.*连接中断/)
  ui.find(node => node.type === 'button' && nodeText(node) === '重新加载').props.onClick()
  requests[1].resolve(episode('1'))
  await flush()
  assert.match(ui.text(), /示例分集1/)

  route.params.epId = '2'
  await flush()
  assert.match(ui.text(), /正在加载分集/)
  assert.doesNotMatch(ui.text(), /示例分集1/)
  route.params.epId = '3'
  await flush()
  requests[3].resolve(episode('3'))
  await flush()
  requests[2].reject(new Error('旧分集加载失败'))
  await flush()
  assert.match(ui.text(), /示例分集3/)
  assert.doesNotMatch(ui.text(), /旧分集加载失败|示例分集1/)
})

test('shared result header preserves sort selection, direction toggling and movie search relevance mode', async t => {
  const component = await loadSfc(new URL('../src/components/BrowseResultsHeader.vue', import.meta.url), { vue })
  const selected = []
  const props = Vue.reactive({ title: '全部影片', count: '共 13 部', sort: { key: 'added', order: 'desc' },
    options: [{ key: 'added', label: '最近添加' }, { key: 'rating', label: '评分' }], ratingSource: 'douban',
    relevance: false, onSort: key => selected.push(key) })
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  assert.match(ui.text(), /全部影片.*共 13 部/)
  assert.equal(ui.find(node => node.type === 'option' && node.props.value === 'rating').text, '评分（豆瓣）')
  ui.find(node => node.type === 'select').props.onChange({ target: { value: 'rating' } })
  ui.find(node => node.type === 'button').props.onClick()
  assert.deepEqual(selected, ['rating', 'added'])
  assert.equal(ui.find(node => node.type === 'button').props['aria-label'], '当前降序，切换为升序')
  props.relevance = true
  await flush()
  assert.equal(ui.find(node => node.type === 'select'), undefined)
  assert.match(ui.text(), /按搜索相关度排序/)
})
