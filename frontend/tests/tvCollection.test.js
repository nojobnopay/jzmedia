import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { airingErrorText, airingLabel, collectionBadge, collectionCountText, collectionExplanation, collectionLabel, mergeTvSeasons, sourceEpisodePath, sourceSeasonPath, tvSeasonLabel, uniqueSeasonSources } from '../src/tvCollection.js'
import { useTvCollection } from '../src/useTvCollection.js'
import { renderHarness, deferred, flush } from './helpers/renderHarness.js'

test('season cards merge metadata and local seasons without using legacy official totals as ownership', () => {
  const rows = mergeTvSeasons([
    { season: 1, total: 12, distinct: 0, name: '旧季名' },
    { season: 4, total: 1, distinct: 1, name: '本地集' },
  ], [
    { season: 0, name: '特别篇', collection_state: 'uncollected', official_count: 2, collected_count: 0 },
    { season: 1, name: '第一季', collection_state: 'uncertain', official_count: 12, collected_count: 0 },
    { season: 2, collection_state: 'collected', official_count: 8, collected_count: 3 },
  ])
  assert.deepEqual(rows.map(row => row.season), [0, 1, 2, 4])
  assert.equal(rows[1].name, '第一季')
  assert.equal(rows[1].local.distinct, 0)
  assert.equal(collectionCountText(rows[0]), '已收藏 0 / 2 集')
  assert.equal(collectionCountText(rows[1]), '收藏信息待补全')
  assert.equal(collectionCountText(rows[2]), '已收藏 3 / 8 集')
  assert.equal(rows[3].collected_count, 1)
  assert.equal(rows[3].local_count, 1)
  assert.equal(mergeTvSeasons([{ season: 1, total: 12, distinct: 0 }])[0].collected_count, 0)
})

test('uncertain ownership preserves local or confirmed counts without an unsupported official denominator', () => {
  const season = { collection_state: 'uncertain', collection_reason: 'catalog_missing', local_count: 12, collected_count: 3, official_count: 20 }
  assert.equal(collectionCountText(season), '已收藏 12 集')
  assert.equal(collectionCountText({ ...season, local_count: 0 }), '已确认收藏 3 集')
  assert.equal(collectionCountText({ ...season, local_count: 0, collected_count: 0 }), '收藏信息待补全')
  assert.equal(collectionCountText({ ...season, collection_reason: 'match_review' }), '已收藏 12 集')
  assert.equal(collectionCountText({ ...season, collection_state: 'collected', official: false, collected_count: 0 }), '已收藏 12 集')
  assert.equal(collectionCountText({ collection_state: 'collected', collected_count: 3, official_count: null }), '已收藏 3 集', 'older responses without official remain compatible')
})

test('collection reasons distinguish missing metadata from numbering and match questions', () => {
  const season = { collection_state: 'uncertain', collection_reason: 'catalog_missing' }
  assert.equal(collectionLabel(season), '资料待补全')
  assert.equal(collectionBadge(season), '')
  assert.equal(collectionExplanation(season), '官方分集资料尚不完整；已有收藏仍保留，进入「全部分集」可补充目录，无需重新匹配。')
  assert.equal(collectionBadge({ ...season, collection_reason: 'numbering_unresolved' }), '分集编号待对照')
  assert.equal(collectionLabel({ ...season, collection_reason: 'match_review' }), '分集匹配需确认')
  assert.match(collectionExplanation({ ...season, collection_reason: 'match_review' }), /查看本季分集的匹配信息/)
  assert.match(collectionExplanation({ ...season, collection_reason: 'numbering_unresolved' }), /查看本季分集编号/)
  assert.equal(collectionBadge({ ...season, collection_reason: 'show_unconfirmed' }), '')
  assert.equal(collectionExplanation({ ...season, collection_reason: 'show_unconfirmed' }), '')
  assert.equal(collectionBadge({ ...season, collection_state: 'uncollected', collection_reason: '' }), '未收藏')
  const coverage = { ...season, collection_reason: 'coverage_incomplete', local_count: 3, official_count: 12 }
  assert.equal(collectionLabel(coverage), '收藏范围暂未确定')
  assert.equal(collectionBadge(coverage), '')
  assert.equal(collectionCountText(coverage), '已收藏 3 集')
  assert.equal(collectionExplanation(coverage), '本剧部分分集的对应范围尚未确定，暂不判断这些季是否缺集。')
})

test('airing and collection states stay separate, including SP and unknown dates', () => {
  assert.equal(tvSeasonLabel(0), '特别篇（SP）')
  assert.equal(airingLabel({ airing_state: 'upcoming', collection_state: 'uncollected' }), '尚未播出')
  assert.equal(airingLabel({ air_date: '' }), '播出时间未知')
  assert.equal(collectionCountText({ collection_state: 'collected', collected_count: 3, official_count: null }), '已收藏 3 集')
  assert.equal(airingErrorText('rate_limited'), '资料服务暂时限制请求，稍后会重试')
  assert.equal(airingErrorText({ code: 'not_configured' }), '尚未配置 TMDB 凭据')
  assert.match(airingErrorText('404 not found'), /重启服务后重试（404）/)
  assert.equal(airingErrorText('not_found'), '资料服务暂未找到对应内容')
  assert.equal(airingErrorText('404 该季暂无对应的官方分集目录'), '404 该季暂无对应的官方分集目录')
})

test('confirming an existing TMDB match reloads the snapshot and a bounded manual check reports background continuation', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const show = Vue.ref({ id: 41, tmdb_id: 101, needs_review: 1 })
  const calls = []
  let state
  const component = { setup() {
    state = useTvCollection(() => show.value, async (path, options) => {
      calls.push({ path, options })
      if (options.method === 'POST') return { running: false, pending: 1 }
      return { show_id: 41, tmdb_id: 101, checked_at: 10, seasons: [], confirmed: !show.value.needs_review }
    })
    return () => null
  } }
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  await flush()
  assert.equal(state.data.value.confirmed, false)
  show.value.needs_review = 0
  await flush()
  assert.equal(state.data.value.confirmed, true)
  const count = calls.length
  const checking = state.check()
  await flush()
  for (let i = 0; i < 5; i++) { t.mock.timers.tick(2000); await flush() }
  await checking
  assert.equal(state.checking.value, false)
  assert.match(state.notice.value, /检查已排队，将在后台继续/)
  assert.equal(calls.length - count, 7, 'one manual request and at most six local snapshot reads')
  t.mock.timers.tick(60000)
  await flush()
  assert.equal(calls.length - count, 7)
})

test('source links use only real local identifiers and retain the actual source season', () => {
  const source = { show_id: 82, season: 3, episode_id: 910, episode: 6 }
  assert.equal(sourceSeasonPath(source), '/tv/82/s/3')
  assert.equal(sourceEpisodePath(source), '/tv/82/s/3/e/910')
  assert.equal(sourceEpisodePath({ ...source, episode_id: undefined, tmdb_episode_id: 910 }), '')
  assert.equal(sourceSeasonPath({ season: 0 }), '')
  assert.equal(uniqueSeasonSources([source, { ...source, episode_id: 911 }, { show_id: 82, season: 0 }]).length, 2)
})

test('collection requests cancel and ignore late results when the same show is rematched', async t => {
  const show = Vue.ref({ id: 41, tmdb_id: 100, media_library_id: 7 })
  const calls = [], pending = []
  let state
  const component = { setup() {
    state = useTvCollection(() => show.value, (path, options) => {
      const result = deferred(); pending.push(result); calls.push({ path, options }); return result.promise
    })
    return () => null
  } }
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  show.value = { id: 41, tmdb_id: 200, media_library_id: 7 }
  await flush()
  assert.equal(calls[0].options.signal.aborted, true)
  pending[1].resolve({ show_id: 41, tmdb_id: 200, seasons: [{ season: 2 }], checked_at: 20 })
  await flush()
  pending[0].resolve({ show_id: 41, tmdb_id: 100, seasons: [{ season: 1 }], checked_at: 10 })
  await flush()
  assert.equal(state.data.value.tmdb_id, 200)
  assert.equal(state.data.value.seasons[0].season, 2)
  assert.equal(state.loading.value, false)
  assert.ok(calls.every(call => call.path.endsWith('/collection')), 'entering detail only reads the local snapshot')
})

test('manual checking stops after a fresh snapshot and an unmounted page cannot continue its request chain', async () => {
  const calls = [], post = deferred()
  let state, checked = 10
  const component = { setup() {
    state = useTvCollection(() => ({ id: 41, tmdb_id: 100 }), async (path, options) => {
      calls.push({ path, options })
      if (options?.method === 'POST') return post.promise
      return { show_id: 41, tmdb_id: 100, seasons: [], checked_at: checked }
    })
    return () => null
  } }
  const ui = renderHarness(component)
  await flush()
  const checking = state.check()
  checked = 20
  post.resolve({ status: 'queued' })
  await checking
  assert.equal(state.checking.value, false)
  assert.equal(state.data.value.checked_at, 20)
  assert.equal(calls.filter(call => call.path.endsWith('/collection')).length, 2)
  assert.deepEqual(JSON.parse(calls.find(call => call.options.method === 'POST').options.body), { show_id: 41 })
  ui.app.unmount()
  const count = calls.length
  await state.check()
  assert.equal(calls.length, count)
})

test('an earlier snapshot failure does not stop an accepted manual check before its fresh result', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let state, reads = 0
  const ui = renderHarness({ setup() {
    state = useTvCollection(() => ({ id: 41, tmdb_id: 100 }), async (_path, options) => {
      if (options.method === 'POST') return { status: 'queued' }
      reads++
      return { show_id: 41, tmdb_id: 100, seasons: [], checked_at: reads < 3 ? 10 : 20, error: reads < 3 ? 'timeout' : '' }
    })
    return () => null
  } })
  t.after(() => ui.app.unmount())
  await flush()
  const checking = state.check()
  await flush()
  assert.equal(state.checking.value, true)
  t.mock.timers.tick(2000)
  await checking
  assert.equal(reads, 3)
  assert.equal(state.data.value.error, '')
  assert.equal(state.checking.value, false)
})
