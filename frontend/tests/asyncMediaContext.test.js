import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

const bindTextValue = (element, binding) => { element.props.value = binding.value }
const vue = { ...Vue, vShow: {}, vModelText: { mounted: bindTextValue, updated: bindTextValue }, vModelSelect: {}, vModelCheckbox: {} }
const blank = { setup: () => () => Vue.h('div') }
const button = (ui, text) => ui.find(node => node.type === 'button' && nodeText(node) === text)
const person = (id, biography = '已有简介') => ({ tmdb_id: id, name: '人物' + id, biography,
  acting: [], directing: [], tv_works: [] })

async function personPage(t) {
  const route = Vue.reactive({ params: { tmdb_id: '1' }, query: {} })
  const requests = []
  let media = 7, changed
  const component = await loadSfc(new URL('../src/views/Person.vue', import.meta.url), {
    vue, 'vue-router': { useRoute: () => route },
    '../api.js': { posterUrl: path => path, api: (path, opts = {}) => {
      const request = { path, opts, ...deferred() }
      requests.push(request)
      return request.promise
    } },
    '../libraries.js': { mediaParam: () => media, onLibChange: callback => { changed = callback; return () => {} } },
    '../components/MediaOverview.vue': { default: { props: ['text'], setup: props => () => Vue.h('p', props.text) } },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  return { ...ui, requests, route, switchMedia(id) { media = id; changed() } }
}

test('person automatic and manual biography refresh use the scoped refresh endpoint and keep failures retryable', async t => {
  const ui = await personPage(t)
  ui.requests[0].resolve(person(1, ''))
  await flush()
  assert.match(ui.text(), /简介加载中/)
  assert.equal(ui.requests[1].path, '/api/persons/1/refresh?media_library=7')
  assert.equal(ui.requests[1].opts.method, 'POST')
  ui.requests[1].reject(new Error('资料服务暂不可用'))
  await flush()
  assert.match(ui.text(), /简介加载失败.*资料服务暂不可用/)
  assert.equal(button(ui, '更新简介').props.disabled, false)
  button(ui, '更新简介').props.onClick()
  assert.equal(ui.requests[2].path, '/api/persons/1/refresh?media_library=7')
  ui.requests[2].resolve(person(1, '更新后的简介'))
  await flush()
  assert.match(ui.text(), /更新后的简介.*已更新|已更新.*更新后的简介/)
})

test('person navigation discards late success, late errors and stale ensure writes', async t => {
  const ui = await personPage(t)
  ui.route.params.tmdb_id = '2'
  ui.route.query = { name: '第二位演员', profile: '/actor-two.jpg' }
  await flush()
  assert.equal(ui.requests[0].opts.signal.aborted, true)
  ui.requests[1].resolve(person(2))
  await flush()
  ui.requests[0].reject(new Error('404 person not found'))
  await flush()
  assert.equal(ui.requests.length, 2, 'old 404 cannot create the previous actor using the current query')
  assert.match(ui.text(), /人物2/)
  assert.doesNotMatch(ui.text(), /人物不存在|建档/)

  ui.route.params.tmdb_id = '3'
  await flush()
  ui.route.params.tmdb_id = '4'
  await flush()
  ui.requests[3].resolve(person(4))
  await flush()
  ui.requests[2].resolve(person(3))
  await flush()
  assert.match(ui.text(), /人物4/)
  assert.doesNotMatch(ui.text(), /人物3/)
})

test('person manual refresh and same-person library changes cannot restore old works or status', async t => {
  const ui = await personPage(t)
  ui.requests[0].resolve(person(1))
  await flush()
  button(ui, '更新简介').props.onClick()
  ui.switchMedia(9)
  await flush()
  assert.equal(ui.requests[1].opts.signal.aborted, true)
  ui.requests[2].resolve({ ...person(1), acting: [{ id: 9, title: '当前库影片' }] })
  await flush()
  ui.requests[1].resolve({ ...person(1), acting: [{ id: 7, title: '旧库影片' }] })
  await flush()
  assert.match(ui.text(), /当前库影片/)
  assert.doesNotMatch(ui.text(), /旧库影片|已更新/)
  button(ui, '更新简介').props.onClick()
  ui.app.unmount()
  assert.equal(ui.requests[3].opts.signal.aborted, true)
  ui.requests[3].reject(new Error('卸载后的错误'))
  await flush()
})

async function collectionPage(t, options = {}) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null } })
  t.after(() => descriptor ? Object.defineProperty(globalThis, 'localStorage', descriptor) : delete globalThis.localStorage)
  let media = 7, changed, tick, starts = 0, statusCount = 0
  const requests = []
  const component = await loadSfc(new URL('../src/views/Collections.vue', import.meta.url), {
    vue,
    '../api.js': { posterUrl: path => path, api: (path, opts = {}) => {
      requests.push({ path, opts })
      if (opts.method === 'POST') return options.post?.(path, opts) ?? Promise.resolve({ job_id: 'job7', total: 50, media_library_id: media, force: false })
      if (path.startsWith('/api/collections/suggest/backfill/status')) {
        return options.status?.(++statusCount, path, opts) ?? Promise.resolve({ state: 'idle' })
      }
      if (path.startsWith('/api/collections/suggest')) return options.suggest?.(media, opts) ?? Promise.resolve({ items: [], coverage: { unchecked: 1 } })
      return Promise.resolve({ items: [] })
    } },
    '../libraries.js': { currentMediaId: () => media, mediaParam: () => media, loadLibs: async () => {},
      onLibChange: callback => { changed = callback; return () => {} } },
    '../usePolling.js': { usePolling: callback => { tick = callback; return { start() { starts++ }, stop() {} } } },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  await flush()
  return { ...ui, requests, tick: () => tick(), switchMedia(id) { media = id; changed() }, get starts() { return starts } }
}
const runningJob = { state: 'running', total: 50, done: 1, job_id: 'job7', media_library_id: 7, force: false }
const doneJob = { ...runningJob, state: 'done', done: 50 }
const suggestion = (id, name) => ({ items: [{ collection_tmdb_id: id, collection_name: name,
  members: [{ id, title: name }], member_count: 1 }], coverage: { unchecked: 1 } })

test('collection suggestions clear immediately on a library switch and reject a previous response', async t => {
  const pending = []
  const ui = await collectionPage(t, { suggest: (media, opts) => {
    const request = { media, opts, ...deferred() }
    pending.push(request)
    return request.promise
  } })
  ui.switchMedia(9)
  await flush()
  assert.equal(pending[0].opts.signal.aborted, true)
  pending[1].resolve(suggestion(9, '当前库推荐'))
  await flush()
  pending[0].resolve(suggestion(7, '旧库推荐'))
  await flush()
  assert.match(ui.text(), /当前库推荐/)
  assert.doesNotMatch(ui.text(), /旧库推荐/)
  ui.switchMedia(10)
  await flush()
  assert.doesNotMatch(ui.text(), /当前库推荐/)
  pending[2].resolve({ items: [] })
})

test('collection backfill finishing in another library or after unmount cannot continue its chain', async t => {
  for (const leave of ['switch', 'unmount']) {
    const pending = deferred()
    const ui = await collectionPage(t, { status: count => Promise.resolve(count === 1 ? runningJob : pending.promise) })
    const poll = ui.tick()
    if (leave === 'switch') ui.switchMedia(9)
    else ui.app.unmount()
    pending.resolve(doneJob)
    await poll
    await flush()
    assert.equal(ui.requests.filter(request => request.opts.method === 'POST').length, 0)
  }
})

test('collection backfill also checks scope after refreshing completion recommendations', async t => {
  const completing = deferred()
  let suggests = 0
  const ui = await collectionPage(t, {
    status: count => Promise.resolve(count === 1 ? runningJob : doneJob),
    suggest: () => ++suggests === 2 ? completing.promise : Promise.resolve({ items: [], coverage: { unchecked: 1 } }),
  })
  const poll = ui.tick()
  await flush()
  ui.switchMedia(9)
  completing.resolve({ items: [], coverage: { unchecked: 50 } })
  await poll
  assert.equal(ui.requests.filter(request => request.opts.method === 'POST').length, 0)
})

test('collection same-scope backfill continues with its original scope; forced and legacy resumes only track progress', async t => {
  for (const mode of ['normal', 'forced', 'legacy']) {
    const initial = { ...runningJob, force: mode === 'forced' }
    if (mode === 'legacy') { delete initial.media_library_id; delete initial.force }
    const ui = await collectionPage(t, { status: count => Promise.resolve(count === 1 ? initial : { ...initial, state: 'done', done: 50 }) })
    await ui.tick()
    const posts = ui.requests.filter(request => request.opts.method === 'POST')
    assert.equal(posts.length, mode === 'normal' ? 1 : 0)
    if (mode === 'normal') assert.deepEqual(JSON.parse(posts[0].opts.body), { limit: 50, force: false, media_library: 7 })
    ui.app.unmount()
  }
})

test('collection backfill startup and resume responses cannot restart polling after the scope changes', async t => {
  const startup = deferred()
  const ui = await collectionPage(t, { post: () => startup.promise })
  button(ui, '补全系列信息').props.onClick()
  ui.switchMedia(9)
  startup.resolve({ job_id: 'job7', total: 50, media_library_id: 7, force: false })
  await flush()
  assert.equal(ui.starts, 0)
  const resume = deferred()
  const other = await collectionPage(t, { status: () => resume.promise })
  other.switchMedia(9)
  resume.resolve(runningJob)
  await flush()
  assert.equal(other.starts, 0)
})

test('a missing backfill job stops polling and allows a fresh scoped start', async t => {
  const missing = Object.assign(new Error('404 补全任务不存在'), { status: 404 })
  const ui = await collectionPage(t, {
    status: count => count === 1 ? Promise.resolve(runningJob) : Promise.reject(missing),
  })
  await ui.tick()
  await flush()
  assert.match(ui.text(), /补全任务已失效，可重新开始/)
  assert.equal(button(ui, '取消补全'), undefined)
  assert.equal(button(ui, '补全系列信息').props.disabled, false)
  const count = ui.requests.length
  await ui.tick()
  assert.equal(ui.requests.length, count, 'a missing explicit job must not keep polling the latest job')
  button(ui, '补全系列信息').props.onClick()
  await flush()
  const start = ui.requests.find(request => request.opts.method === 'POST')
  assert.deepEqual(JSON.parse(start.opts.body), { limit: 50, force: false, media_library: 7 })
})

test('cancelling an expired explicit job clears tracking without querying or cancelling another job', async t => {
  const missing = Object.assign(new Error('404 补全任务不存在'), { status: 404 })
  const ui = await collectionPage(t, {
    status: () => Promise.resolve(runningJob),
    post: () => Promise.reject(missing),
  })
  button(ui, '取消补全').props.onClick()
  await flush()
  assert.match(ui.text(), /补全任务已失效，可重新开始/)
  assert.equal(button(ui, '取消补全'), undefined)
  const cancel = ui.requests.find(request => request.opts.method === 'POST')
  assert.equal(cancel.path, '/api/collections/suggest/backfill/cancel')
  assert.deepEqual(JSON.parse(cancel.opts.body), { job_id: 'job7' })
  assert.equal(ui.requests.filter(request => request.path.startsWith('/api/collections/suggest/backfill/status')).length, 1)
  const count = ui.requests.length
  await ui.tick()
  assert.equal(ui.requests.length, count)
})

async function episodePage(t) {
  const route = Vue.reactive({ params: { epId: '1' } })
  const requests = []
  const component = await loadSfc(new URL('../src/views/EpisodeView.vue', import.meta.url), {
    vue, 'vue-router': { useRoute: () => route, useRouter: () => ({ push() {} }) },
    '../api.js': { posterUrl: path => path, scopedArtworkUrl: path => path, api: (path, opts = {}) => {
      const request = { path, opts, ...deferred() }
      requests.push(request)
      return request.promise
    } },
    '../components/PlayerModal.vue': { default: blank }, '../components/MediaBackdrop.vue': { default: blank },
    '../components/MediaOverview.vue': { default: blank }, '../components/CastWall.vue': { default: blank },
    '../components/CrewRow.vue': { default: blank },
    '../components/ActionMenu.vue': { default: { setup: (_props, { slots }) => () => Vue.h('div', slots.default?.()) } },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  return { ...ui, route, requests }
}
const episode = (id, season = 1) => ({ id, season, episode: 1, show_id: 7, show_title: '示例剧集',
  title: '分集' + id, exists: true, needs_review: true, cast: [] })
const candidate = (season = 1) => ({ tmdb_episode_id: season * 100, season, episode: 1, title: '候选第' + season + '季' })

test('episode navigation invalidates matching drafts and late candidates, then binds only the new selection', async t => {
  const ui = await episodePage(t)
  ui.requests[0].resolve(episode(1))
  await flush()
  button(ui, '匹配集号').props.onClick()
  await flush()
  ui.find(node => node.type === 'input' && node.props['aria-label'] === '本地集名').props['onUpdate:modelValue']('上一集草稿')
  button(ui, '查询该季').props.onClick()
  ui.route.params.epId = '2'
  await flush()
  assert.equal(ui.requests[1].opts.signal.aborted, true)
  ui.requests[2].resolve(episode(2, 2))
  ui.requests[1].resolve({ items: [candidate()] })
  await flush()
  assert.doesNotMatch(ui.text(), /候选第1季|指定 TMDB 集/)
  button(ui, '匹配集号').props.onClick()
  await flush()
  assert.equal(ui.find(node => node.type === 'input' && node.props['aria-label'] === '本地集名').props.value, '')
  button(ui, '查询该季').props.onClick()
  assert.equal(ui.requests[3].path, '/api/tv/shows/7/tmdb-episodes?season=2')
  ui.requests[3].resolve({ items: [candidate(2)] })
  await flush()
  button(ui, '匹配此集').props.onClick()
  assert.equal(ui.requests[4].path, '/api/tv/episodes/2/match-episode')
  assert.deepEqual(JSON.parse(ui.requests[4].opts.body), { tmdb_episode_id: 200, season: 2 })
  ui.requests[4].resolve({ ok: true })
  await flush()
  ui.requests[5].resolve(episode(2, 2))
  await flush()
  assert.doesNotMatch(ui.text(), /指定 TMDB 集/)
})

test('changing candidate season and closing the panel invalidate old responses and errors', async t => {
  const ui = await episodePage(t)
  ui.requests[0].resolve(episode(1))
  await flush()
  button(ui, '匹配集号').props.onClick()
  await flush()
  button(ui, '查询该季').props.onClick()
  ui.find(node => node.type === 'input' && node.props.type === 'number').props['onUpdate:modelValue'](2)
  ui.requests[1].resolve({ items: [candidate()] })
  await flush()
  assert.doesNotMatch(ui.text(), /候选第1季/)
  button(ui, '查询该季').props.onClick()
  button(ui, '收起集号匹配').props.onClick()
  ui.requests[2].reject(new Error('旧查询错误'))
  await flush()
  assert.doesNotMatch(ui.text(), /旧查询错误|候选查询失败/)
})
