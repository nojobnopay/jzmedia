import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, reactive, ref } from 'vue'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { loadSfc } from './helpers/loadSfc.js'
import { deferred } from './helpers/renderHarness.js'
import { clearAiDiscovery, discoveryFilters, useAiDiscovery } from '../src/useAiDiscovery.js'

const reply = (overrides = {}) => ({ ok: true, session_id: 'test-session', intent: 'recommend', source: 'library',
  status: 'results', items: [{ key: 'movie-13', title: '虚构电影', kind: 'movie', in_library: true,
    detail_path: '/m/13', reason: '人物之间的关系贯穿全片。', watch_state: 'unseen' }], warnings: [], ...overrides })
function setup(t, request, context = {}) {
  const scope = effectScope()
  const props = { kind: 'movie', mediaId: ref(7), ordinaryQuery: ref('旧关键词'), selection: ref({ genres: ['剧情'], watched: 0 }),
    route: reactive({ path: '/', fullPath: '/?media=7' }), viewing: reactive({ mode: 'normal', generation: 2, revision: 1 }), request, ...context }
  const ai = scope.run(() => useAiDiscovery(props))
  t.after(() => { scope.stop(); clearAiDiscovery() })
  return { ai, props, scope }
}

test('smart mode takes over the same draft only on open and inherits filters by explicit choice', async t => {
  const sent = []
  const { ai, props } = setup(t, async (path, options) => {
    sent.push([path, JSON.parse(options.body)])
    return reply()
  })
  ai.open()
  assert.equal(ai.state.draft, '旧关键词')
  assert.equal(sent.length, 0)
  ai.changeDraft('今年评分最高的10部')
  await ai.submit()
  assert.deepEqual(sent[0], ['/api/ai/search/results', { kind: 'movie', media_library_id: 7, q: '今年评分最高的10部', source: 'library' }])
  assert.equal(props.ordinaryQuery.value, '旧关键词')
  ai.restart()
  ai.state.inherit = true
  await ai.submit('推荐文艺片')
  assert.deepEqual(sent[1][1].filters, { genre: ['剧情'], watched: 0 })
  assert.equal(sent[1][1].session_id, undefined)
  ai.close(false)
  assert.equal(ai.state.result, null)
  assert.equal(ai.state.open, false)
  assert.deepEqual(discoveryFilters({ years: ['2025'], decades: ['1990'], rating: 7, ratingSource: 'douban' }),
    { year: [2025], decade: [1990], min_rating: 7, rating_source: 'douban' })
})

test('follow-ups retain ranked results through failure, retries and pending public lookup', async t => {
  const sent = [], pending = deferred()
  const queue = [reply(), { ok: false, code: 'timeout', message: '服务超时' }, reply({ status: 'comparison' }), pending.promise]
  const { ai } = setup(t, async (_path, opts) => { sent.push(JSON.parse(opts.body)); return queue.shift() })
  ai.open()
  await ai.submit('推荐文学改编电影')
  await ai.submit('比较前两部')
  assert.equal(ai.state.error, '服务超时')
  assert.equal(ai.state.result.items[0].key, 'movie-13')
  await ai.retry()
  assert.equal(sent[2].session_id, sent[1].session_id)
  assert.equal(ai.state.initialQuestion, '推荐文学改编电影')
  const switching = ai.searchPublic()
  assert.equal(ai.state.result.status, 'comparison')
  assert.equal(ai.state.busy, true)
  assert.equal(sent[3].source, 'public')
  assert.equal(sent[3].reuse_plan, true)
  assert.equal(sent[3].session_id, 'test-session')
  pending.resolve(reply({ source: 'public' }))
  await switching
  assert.equal(ai.state.source, 'public')
})

test('cancel, scope switch and locking suppress late results; children cannot initiate a call', async t => {
  const calls = []
  const { ai, props } = setup(t, (_path, opts) => { const next = deferred(); calls.push({ ...next, signal: opts.signal }); return next.promise })
  ai.open()
  const old = ai.submit('第一个')
  ai.cancel()
  assert.equal(calls[0].signal.aborted, true)
  const newer = ai.submit('第二个')
  props.mediaId.value = 12
  assert.equal(calls[1].signal.aborted, true)
  calls.forEach(call => call.resolve(reply()))
  await Promise.all([old, newer])
  assert.equal(ai.state.result, null)
  assert.equal(ai.state.open, false)
  props.viewing.mode = 'children'
  ai.open()
  await ai.submit('儿童')
  assert.equal(ai.state.open, false)
  assert.equal(calls.length, 2)
  props.viewing.mode = 'private'
  ai.open()
  const final = ai.submit('隐私')
  props.viewing.locallyLocked = true
  calls[2].resolve(reply())
  await final
  assert.equal(ai.state.result, null)
})

test('returning from a card rechecks before display, without repeating the model turn', async t => {
  const reads = [], pending = deferred()
  const request = async (path, opts) => {
    reads.push({ path, data: JSON.parse(opts.body) })
    return path.endsWith('/refresh') ? pending.promise : reply()
  }
  const first = setup(t, request)
  first.ai.open()
  first.ai.state.kinds = 'both'
  await first.ai.submit('电影和剧集')
  first.ai.remember('movie-13')
  first.scope.stop()
  const { ai, props } = setup(t, request, { ordinaryQuery: ref('') })
  const restoring = ai.restore()
  assert.equal(ai.state.result, null)
  assert.equal(ai.state.refreshing, true)
  assert.deepEqual(reads[1], { path: '/api/ai/search/results/refresh',
    data: { kind: 'movie', kinds: ['movie', 'tv'], media_library_id: 7, session_id: 'test-session' } })
  pending.resolve(reply({ status: 'empty', items: [] }))
  assert.equal(await restoring, true)
  assert.equal(ai.state.result.items.length, 0)
  assert.equal(ai.state.question, '电影和剧集')
  assert.equal(props.ordinaryQuery.value, '旧关键词', 'An ordinary draft that was never submitted survives the detail visit')
  assert.equal(reads.length, 2)
})

test('public rank cards and missing results have different accessible content', async () => {
  const panel = await loadSfc(new URL('../src/components/AiSearchPanel.vue', import.meta.url), {
    'vue-router': { useRoute: () => ({ path: '/', fullPath: '/' }) },
  })
  async function html(answer) {
    const app = Vue.createSSRApp(panel, { kind: 'movie', state: { result: answer, question: '今年高分电影' } })
    app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => Vue.h('a', { href: p.to.path }, slots.default()) })
    return renderToString(app)
  }
  const output = await html(reply({ source: 'public', intent: 'ranking', items: [
    { key: 'p1', in_library: false, title: '虚构的库外电影', kind: 'movie', rating: 7.800000000000001, rating_source: 'tmdb', watch_state: 'unknown', external_url: 'https://www.themoviedb.org/movie/1' },
  ] }))
  assert.match(output, /去在线搜索/)
  assert.match(output, /href="\/online"/)
  assert.match(output, /TMDB 7\.8 分/)
  assert.match(output, /观看状态未知/)
  assert.match(output, /来源资料/)
  assert.match(await html(reply({ items: [], status: 'empty' })), /查公开片目/)
})

test('media changes expire an online handoff even after switching back; same-library cross-kind returns survive a catalogue refresh', async t => {
  const { aiLocalDetailTarget, aiOnlineTarget, aiNavigationContext, clearAiNavigation, rememberAiNavigation } = await import('../src/aiDiscoveryNav.js')
  const { applyLibs, resetLibState, switchMedia } = await import('../src/libraries.js')
  resetLibState()
  t.after(() => { clearAiNavigation(); resetLibState() })
  const libraries = { default_id: 11, items: [{ id: 11, media_library_id: 7, kind: 'movie' },
    { id: 12, media_library_id: 7, kind: 'tv' }, { id: 13, media_library_id: 8, kind: 'movie' }] }
  applyLibs(libraries)
  switchMedia(7)
  const origin = { path: '/', fullPath: '/?media=7' }
  const external = { key: 'public-movie-1', title: '库外片名' }
  rememberAiNavigation(external, origin)
  const online = aiOnlineTarget(external, origin).state
  assert.equal(aiNavigationContext('/online', online).title, external.title)
  const television = { key: 'local-tv-20', title: '同库剧集', detail_path: '/tv/20' }
  rememberAiNavigation(television, origin)
  const local = aiLocalDetailTarget(television, origin).state
  applyLibs(libraries)
  switchMedia(7)
  assert.equal(aiNavigationContext('/tv/20', local).origin, origin.fullPath)
  switchMedia(8)
  switchMedia(7)
  assert.equal(aiNavigationContext('/online', online), null)
  assert.equal(aiNavigationContext('/tv/20', local), null)
  assert.deepEqual(aiOnlineTarget(external, origin).state, {})
})
