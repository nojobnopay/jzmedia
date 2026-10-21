import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

const bindText = (element, binding) => { element.props.value = binding.value }
const vue = { ...Vue, vShow: {}, vModelText: { mounted: bindText, updated: bindText }, vModelSelect: {}, vModelCheckbox: {} }
const blank = { setup: () => () => Vue.h('div') }
const button = (ui, label) => ui.find(node => node.type === 'button' && nodeText(node).trim() === label)
const item = (id, metadata = {}) => ({ id, title: '剧集' + id, library_id: 7, tmdb_id: null,
  genres: [], seasons: [], episodes: [], extras: [], episode_count: 2, watched_count: 0, ...metadata })
const writes = ui => ui.requests.filter(request => ['POST', 'PATCH'].includes(request.opts.method))
const searches = ui => ui.requests.filter(request => request.path.startsWith('/api/tv/search?'))
const environments = new WeakMap()

async function showPage(t, options = {}) {
  let environment = environments.get(t)
  if (!environment) {
    const descriptors = new Map(['window', 'document'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
    for (const key of descriptors.keys()) Object.defineProperty(globalThis, key, {
      configurable: true, value: { addEventListener() {}, removeEventListener() {} },
    })
    environment = { pages: [] }
    environments.set(t, environment)
    t.after(() => {
      for (const page of environment.pages) page.app.unmount()
      for (const [key, descriptor] of descriptors) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor)
        else delete globalThis[key]
      }
    })
  }
  const route = Vue.reactive({ path: options.path || '/tv/1', params: { id: '1' }, query: {} })
  const requests = []
  const component = await loadSfc(new URL('../src/views/TvShow.vue', import.meta.url), {
    vue, 'vue-router': { useRoute: () => route, useRouter: () => ({ push() {}, replace() {} }) },
    '../api.js': { posterUrl: path => path, scopedArtworkUrl: path => path, api: (path, opts = {}) => {
      const request = { path, opts }
      requests.push(request)
      const override = options.api?.(path, opts, request)
      if (override !== undefined) return override
      if (['POST', 'PATCH'].includes(opts.method) || path.startsWith('/api/tv/search?')) {
        Object.assign(request, deferred())
        return request.promise
      }
      const id = path.match(/^\/api\/tv\/shows\/(\d+)(?:\?verify=\d+)?$/)?.[1]
      if (id) return Promise.resolve(item(Number(id), options.metadata))
      if (path.endsWith('/similar')) return Promise.resolve({ items: [] })
      if (path.endsWith('/organize-hint')) return Promise.resolve({ needs: false })
      throw new Error('Unexpected API request: ' + path)
    } },
    '../libraries.js': { currentMediaId: () => 7, loadLibs: async () => {}, switchMedia: () => true },
    '../useTvCollection.js': { useTvCollection: () => ({ data: Vue.ref(null), error: Vue.ref(''), notice: Vue.ref(''),
      loading: Vue.ref(false), checking: Vue.ref(false), reload() {}, check() {} }) },
    ...Object.fromEntries(['ContentPolicyButton', 'PlayerModal', 'MediaBackdrop', 'MediaOverview', 'SimilarRow',
      'CastWall', 'CrewRow', 'TvBindingsDialog', 'TvOrganizeDialog', 'AiMatchSuggestions']
      .map(name => [`../components/${name}.vue`, { default: blank }])),
  })
  const ui = renderHarness(component)
  environment.pages.push(ui)
  await flush()
  return { ...ui, route, requests, navigate(id) { route.path = '/tv/' + id; route.params.id = String(id) } }
}
async function openMatching(ui, query = '搜索关键词') {
  ;(button(ui, '匹配资料') || button(ui, '重新匹配剧集')).props.onClick()
  await flush()
  ui.find(node => node.props['aria-label'] === '搜索剧集匹配').props['onUpdate:modelValue'](query)
  await flush()
}

test('TV details accept a trailing slash and keep normal matching/reopening behavior', async t => {
  const ui = await showPage(t, { path: '/tv/1/', metadata: { tmdb_id: 100, overview: '已有简介' } })
  assert.match(ui.text(), /剧集1/)
  await openMatching(ui)
  button(ui, '搜索').props.onClick()
  searches(ui)[0].resolve({ items: [{ tmdb_id: 111, title: '正常候选' }] })
  await flush()
  button(ui, '收起匹配').props.onClick()
  await flush()
  button(ui, '重新匹配剧集').props.onClick()
  await flush()
  assert.match(ui.text(), /正常候选/)
  const match = button(ui, '匹配').props.onClick
  match(); match()
  await flush()
  assert.equal(writes(ui).length, 1)
  assert.equal(writes(ui)[0].path, '/api/tv/shows/1/match')
  assert.deepEqual(JSON.parse(writes(ui)[0].opts.body), { tmdb_id: 111 })
  assert.equal(button(ui, '匹配').props.disabled, true)
  assert.equal(writes(ui)[0].opts.signal, undefined, 'submitted writes are not presented as cancelled')
  writes(ui)[0].resolve({ media: { artwork: { ok: true } } })
  await flush()
  assert.equal(ui.find(node => node.props['aria-label'] === '搜索剧集匹配'), undefined)
  assert.ok(ui.requests.some(request => request.path === '/api/tv/shows/1/organize-hint'))
})

test('TV search and matching drafts cannot carry old candidates into a different show', async t => {
  const ui = await showPage(t)
  await openMatching(ui, '旧剧搜索')
  button(ui, '搜索').props.onClick()
  const old = searches(ui)[0]
  ui.navigate(2)
  await flush()
  assert.equal(old.opts.signal.aborted, true)
  assert.equal(ui.find(node => node.props['aria-label'] === '搜索剧集匹配'), undefined)
  await openMatching(ui, '当前剧搜索')
  button(ui, '搜索').props.onClick()
  old.resolve({ items: [{ tmdb_id: 111, title: '旧剧候选' }] })
  await flush()
  assert.doesNotMatch(ui.text(), /旧剧候选/)
  assert.equal(button(ui, '搜索').props.disabled, true, 'old completion cannot release a newer search')
  searches(ui)[1].resolve({ items: [{ tmdb_id: 222, title: '当前剧候选' }] })
  await flush()
  button(ui, '匹配').props.onClick()
  assert.equal(writes(ui)[0].path, '/api/tv/shows/2/match')
  assert.deepEqual(JSON.parse(writes(ui)[0].opts.body), { tmdb_id: 222 })
})

test('editing a TV search or closing it while pending discards old results and errors', async t => {
  const ui = await showPage(t)
  await openMatching(ui, '旧搜索')
  button(ui, '搜索').props.onClick()
  ui.find(node => node.props['aria-label'] === '搜索剧集匹配').props['onUpdate:modelValue']('新搜索')
  button(ui, '搜索').props.onClick()
  searches(ui)[0].resolve({ items: [{ tmdb_id: 111, title: '旧结果' }] })
  await flush()
  assert.doesNotMatch(ui.text(), /旧结果/)
  button(ui, '收起匹配').props.onClick()
  searches(ui)[1].reject(new Error('旧搜索失败'))
  await flush()
  assert.doesNotMatch(ui.text(), /旧搜索失败|搜索失败/)
  button(ui, '重新匹配剧集').props.onClick()
  await flush()
  assert.doesNotMatch(ui.text(), /旧结果/)
  assert.equal(button(ui, '搜索').props.disabled, false)
})

test('a late TV match cannot close a newer form, report success or continue metadata reads', async t => {
  for (const operation of ['match', 'external']) {
    const ui = await showPage(t)
    await openMatching(ui)
    button(ui, '搜索').props.onClick()
    searches(ui)[0].resolve({ items: [operation === 'match' ? { tmdb_id: 111, title: '旧剧候选' }
      : { source: 'tvmaze', source_id: '111', title: '旧剧外源候选' }] })
    await flush()
    button(ui, operation === 'match' ? '匹配' : '绑定外源').props.onClick()
    const write = writes(ui)[0]
    ui.navigate(2)
    await flush()
    await openMatching(ui, '当前剧草稿')
    const count = ui.requests.length
    write.resolve({ episodes_filled: 12, media: { queued: true } })
    await flush()
    assert.equal(ui.requests.length, count)
    assert.equal(ui.find(node => node.props['aria-label'] === '搜索剧集匹配').props.value, '当前剧草稿')
    assert.doesNotMatch(ui.text(), /已匹配成功|已绑定外源|回填 12 集/)
  }
})

test('TV rename snapshots its target and a late save cannot clear the next show draft or busy state', async t => {
  const ui = await showPage(t)
  const rename = async text => {
    button(ui, '修改剧名').props.onClick()
    await flush()
    ui.find(node => node.props['aria-label'] === '剧名').props['onUpdate:modelValue'](text)
    const form = ui.find(node => node.type === 'form')
    form.props.onSubmit({ preventDefault() {} })
    form.props.onSubmit({ preventDefault() {} })
    await flush()
  }
  await rename('旧剧的新名')
  assert.equal(writes(ui).length, 1)
  ui.navigate(2)
  await flush()
  await rename('当前剧的新名')
  writes(ui)[0].resolve({ ok: true })
  await flush()
  assert.equal(ui.find(node => node.props['aria-label'] === '剧名').props.value, '当前剧的新名')
  assert.equal(button(ui, '保存剧名').props.disabled, true)
  assert.doesNotMatch(ui.text(), /剧名已保存/)
  assert.deepEqual(writes(ui).map(request => ({ path: request.path, body: JSON.parse(request.opts.body) })), [
    { path: '/api/tv/shows/1', body: { title: '旧剧的新名' } },
    { path: '/api/tv/shows/2', body: { title: '当前剧的新名' } },
  ])
  writes(ui)[1].resolve({ ok: true })
  await flush()
  assert.match(ui.text(), /剧名已保存/)
  assert.equal(button(ui, '修改剧名').props.disabled, false)
})

test('TV watched, confirmation and refresh writes do not leak late errors or unlock a newer operation', async t => {
  for (const label of ['标记整剧已看', '匹配正确', '更新剧集资料']) {
    const ui = await showPage(t, { metadata: { tmdb_id: 100, needs_review: true, overview: '已有简介' } })
    const action = button(ui, label).props.onClick
    action(); action()
    assert.equal(writes(ui).length, 1)
    ui.navigate(2)
    await flush()
    button(ui, label).props.onClick()
    const count = ui.requests.length
    writes(ui)[0].reject(new Error('上一部剧写入错误'))
    await flush()
    assert.equal(ui.requests.length, count)
    assert.doesNotMatch(ui.text(), /上一部剧写入错误/)
    assert.equal(button(ui, label).props.disabled, true)
    assert.ok(writes(ui)[1].path.startsWith('/api/tv/shows/2'))
    writes(ui)[1].resolve({ ok: true })
    await flush()
    assert.equal(button(ui, label).props.disabled, false)
    ui.app.unmount()
  }
})

test('TV directory discovery cannot request an old organize hint after its detail refresh becomes stale', async t => {
  const reloaded = deferred()
  let reads = 0
  const ui = await showPage(t, { api: path => path === '/api/tv/shows/1' && ++reads > 1 ? reloaded.promise : undefined })
  button(ui, '整理剧集目录').props.onClick()
  writes(ui)[0].resolve({ added: 1 })
  await flush()
  ui.navigate(2)
  await flush()
  const count = ui.requests.length
  reloaded.resolve(item(1))
  await flush()
  assert.equal(ui.requests.length, count)
  assert.ok(!ui.requests.some(request => request.path.endsWith('/organize-hint')))
  assert.match(ui.text(), /剧集2/)
})

test('unmounting a TV detail after a submitted write prevents its follow-up reads', async t => {
  const ui = await showPage(t)
  button(ui, '更新剧集资料').props.onClick()
  const write = writes(ui)[0]
  ui.app.unmount()
  const count = ui.requests.length
  write.resolve({ ok: true })
  await flush()
  assert.equal(ui.requests.length, count)
})

test('late artwork polling cannot replace a new show or request its organize hint', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const pendingArtwork = deferred()
  let reads = 0
  const ui = await showPage(t, {
    metadata: { tmdb_id: 100 },
    api: path => path === '/api/tv/shows/1' && ++reads === 3 ? pendingArtwork.promise : undefined,
  })
  button(ui, '更新剧集资料').props.onClick()
  writes(ui)[0].resolve({ ok: true })
  await flush()
  t.mock.timers.tick(2000)
  await flush()
  assert.equal(reads, 3)
  ui.navigate(2)
  await flush()
  const count = ui.requests.length
  pendingArtwork.resolve(item(1, { tmdb_id: 100, title: '过期海报资料', overview: '已经补齐' }))
  await flush()
  t.mock.timers.tick(20000)
  await flush()
  assert.match(ui.text(), /剧集2/)
  assert.doesNotMatch(ui.text(), /过期海报资料/)
  assert.equal(ui.requests.length, count)
})
