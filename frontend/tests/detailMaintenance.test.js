import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, nodeText } from './helpers/renderHarness.js'

const vue = { ...Vue, vShow: {}, vModelText: {}, vModelSelect: {}, vModelCheckbox: {} }
const blank = { setup: () => () => Vue.h('div') }
const button = (ui, label) => ui.find(node => node.type === 'button' && nodeText(node).trim() === label)

async function detail(t, kind, metadata = {}) {
  // Only native listener targets are replaced. The page, menu, notice and their
  // event handlers still run through the real Vue setup/template and renderer.
  const descriptors = new Map(['window', 'document'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  for (const key of descriptors.keys()) {
    Object.defineProperty(globalThis, key, {
      configurable: true, value: { addEventListener() {}, removeEventListener() {} },
    })
  }
  let ui
  t.after(() => {
    ui?.app.unmount()
    for (const [key, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  })

  const item = {
    id: 41, title: kind === 'movie' ? '合成电影' : '合成剧集', library_id: 7,
    tmdb_id: null, match_source: '', needs_review: false,
    genres: [], persons: [], versions: [], seasons: [], episodes: [], extras: [],
    watched_count: 0, episode_count: 0, ...metadata,
  }
  const route = Vue.reactive({ params: { id: String(item.id) }, query: {} })
  const requests = [], editorMounts = []
  const editor = {
    props: ['mode', 'movie', 'movieId'], emits: ['close'],
    setup(props, { emit }) {
      editorMounts.push({ mode: props.mode, movieId: props.movieId, movie: props.movie })
      return () => Vue.h('section', { 'data-test': 'movie-editor' }, [
        Vue.h('button', { onClick: () => emit('close') }, '关闭测试编辑器'),
      ])
    },
  }
  const mainPath = kind === 'movie' ? '/api/movies/41' : '/api/tv/shows/41'
  const component = await loadSfc(new URL(`../src/views/${kind === 'movie' ? 'Detail' : 'TvShow'}.vue`, import.meta.url), {
    vue,
    'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
    '../api.js': { posterUrl: path => path, api: async (path, options) => {
      requests.push({ path, options })
      if (path === mainPath) return item
      if (path === '/api/health') return { ffmpeg: true }
      if (path === '/api/stream/versions') return { versions: [], best_version_id: item.id }
      if (path.startsWith('/api/stream/progress?')) return null
      if (path.startsWith(mainPath + '/similar')) return { items: [] }
      if (path === mainPath + '/files' || path === mainPath + '/collection-hint') return null
      if (path.startsWith('/api/tv/search?')) return { items: [{ tmdb_id: 88, title: '合成匹配候选', year: 2026 }] }
      throw new Error(`Unexpected API request: ${path}`)
    } },
    '../libraries.js': { currentMediaId: () => 7, isRemoteVideoLib: () => false, loadLibs: async () => {}, switchMedia: () => true },
    '../caps.js': { getCaps: async () => ({}) },
    '../usePolling.js': { usePolling: () => ({ start() {}, stop() {} }) },
    '../useFocusTrap.js': { useFocusTrap() {} },
    '../components/MovieEditPanel.vue': { default: editor },
    ...Object.fromEntries([
      'PlayerModal', 'MediaBackdrop', 'MediaOverview', 'SimilarRow', 'CastWall', 'CrewRow',
      'MovieUploadPanel', 'MovieFileManager', 'MovieCollectionsPanel', 'TvBindingsDialog',
      'TvOrganizeDialog', 'AiMatchSuggestions',
    ].map(name => [`../components/${name}.vue`, { default: blank }])),
  })
  ui = renderHarness(component)
  await flush()
  assert.match(ui.text(), new RegExp(item.title))
  assert.ok(ui.find(node => node.type === 'summary' && nodeText(node).includes('更多操作')))
  return { ...ui, item, editorMounts, requests }
}

test('unmatched movie keeps its editor closed until requested and preserves matching/editing menu actions', async t => {
  const ui = await detail(t, 'movie')
  assert.match(ui.text(), /影片资料尚未匹配/)
  assert.equal(ui.editorMounts.length, 0)
  assert.equal(button(ui, '匹配正确'), undefined)
  assert.equal(button(ui, '匹配资料').props['aria-expanded'], false)

  button(ui, '匹配资料').props.onClick()
  await flush()
  assert.equal(ui.editorMounts.length, 1)
  assert.equal(ui.editorMounts[0].mode, 'match')
  assert.equal(ui.editorMounts[0].movieId, ui.item.id)
  assert.equal(ui.editorMounts[0].movie.title, ui.item.title)
  assert.ok(ui.find(node => node.props['data-test'] === 'movie-editor'))
  assert.equal(button(ui, '匹配资料').props['aria-expanded'], true)

  button(ui, '关闭测试编辑器').props.onClick()
  await flush()
  assert.equal(ui.find(node => node.props['data-test'] === 'movie-editor'), undefined)
  assert.equal(button(ui, '匹配资料').props['aria-expanded'], false)
  button(ui, '重新匹配').props.onClick()
  await flush()
  assert.equal(ui.editorMounts.at(-1).mode, 'match')
  button(ui, '编辑资料').props.onClick()
  await flush()
  assert.equal(ui.editorMounts.at(-1).mode, 'edit')
})

test('unmatched TV show reveals its real matching search on demand and can reopen it from more actions', async t => {
  const ui = await detail(t, 'tv')
  const matchingInput = () => ui.find(node => node.type === 'input' && node.props['aria-label'] === '搜索剧集匹配')
  assert.match(ui.text(), /剧集资料尚未匹配/)
  assert.equal(matchingInput(), undefined)
  assert.equal(button(ui, '匹配正确'), undefined)
  assert.equal(button(ui, '匹配资料').props['aria-expanded'], false)

  button(ui, '匹配资料').props.onClick()
  await flush()
  assert.ok(matchingInput())
  assert.equal(button(ui, '匹配资料').props['aria-expanded'], true)
  matchingInput().props['onUpdate:modelValue']('合成剧集')
  await flush()
  button(ui, '搜索').props.onClick()
  await flush()
  assert.ok(ui.requests.some(request => request.path === '/api/tv/search?q=' + encodeURIComponent('合成剧集')))
  assert.match(ui.text(), /合成匹配候选/)

  button(ui, '匹配资料').props.onClick()
  await flush()
  assert.equal(matchingInput(), undefined)
  assert.equal(button(ui, '匹配资料').props['aria-expanded'], false)
  button(ui, '重新匹配剧集').props.onClick()
  await flush()
  assert.ok(matchingInput())
  assert.match(ui.text(), /合成匹配候选/)
})

for (const kind of ['movie', 'tv']) {
  for (const needsReview of [false, true]) {
    test(`${kind} matched by an external source is not shown as unmatched${needsReview ? ' while awaiting review' : ''}`, async t => {
      const ui = await detail(t, kind, { match_source: 'douban', needs_review: needsReview })
      assert.doesNotMatch(ui.text(), /资料尚未匹配/)
      assert.equal(button(ui, '匹配资料'), undefined)
      assert.equal(ui.editorMounts.length, 0)
      assert.equal(ui.find(node => node.props['aria-label'] === '搜索剧集匹配'), undefined)
      if (needsReview) {
        assert.match(ui.text(), /请确认.*是否匹配正确/)
        assert.ok(button(ui, '匹配正确'))
      } else {
        assert.equal(button(ui, '匹配正确'), undefined)
        assert.doesNotMatch(ui.text(), /请确认.*是否匹配正确/)
      }
    })
  }
}
