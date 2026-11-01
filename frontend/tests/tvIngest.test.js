import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, nextTick, ref } from 'vue'
import { createTvIngestFlow, normalizeIngestSummary, TV_INGEST_ACTIONS, useTvIngestFlow } from '../src/useTvIngestFlow.js'
import { useTvIngestReview } from '../src/useTvIngestReview.js'
const plan = (show_id = 7, extra = {}) => ({ show_id, library_id: 2, title: '合成剧集' + show_id,
  fingerprint: 'version1', groups: [{ action: 'rename', episodes: 8 }], metadata: { filled: 8, issues: [] }, ...extra })
const summary = (extra = {}) => ({ preview_id: 'preview', plans: [plan(), plan(8)], auto_prompt: true,
  auto_prompt_ids: [7], params: { ids: [7, 8], actions: TV_INGEST_ACTIONS, allow_torrent: false, allow_absolute_shows: [] }, ...extra })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick() }
function flowFor(request = async () => ({})) { const flow = createTvIngestFlow({ request, onChanged() {}, pollDelay: 0, pause: async () => {} }); flow.setContext('tv/7|media1', true, [2]); return flow }
function setup(t, flow, value = summary()) {
  const scope = effectScope()
  flow.enqueue(value); flow.takeNext()
  const state = scope.run(() => useTvIngestReview(value, { flow, pollDelay: 1 }))
  t.after(() => scope.stop())
  return { state, scope }
}
test('one batch includes all manual plans while automatic prompts honor per-show decisions', () => {
  assert.deepEqual(normalizeIngestSummary(summary(), true).plans.map(row => row.show_id), [7])
  assert.equal(normalizeIngestSummary(summary()).plans.length, 2)
  assert.equal(normalizeIngestSummary(summary({ plans: [plan(7, { groups: [], metadata: { issues: [] } })] })).plans.length, 0)
  assert.equal(normalizeIngestSummary(summary({ plans: [plan(7, { groups: [], metadata: { issues: [{ code: 'offline' }] } })] })).plans.length, 1)
})
test('queue deduplicates a batch, excludes other libraries and clears when route or permissions change', () => {
  const flow = flowFor()
  flow.enqueue(summary()); flow.enqueue(summary())
  assert.equal(flow.queue.value.length, 1)
  flow.takeNext(); assert.equal(flow.review.value.plans.length, 2)
  flow.setContext('tv/8|media1', true, [2]); assert.equal(flow.review.value, null)
  assert.equal(flow.enqueue(summary({ plans: [plan(7, { library_id: 9 })] })), false)
  flow.setContext('tv/8|media1', false, [2]); assert.equal(flow.enqueue(summary()), false)
})
test('a queued preview is revalidated after switching the active video library under an existing modal', () => {
  const flow = flowFor(), activeLibrary = ref(2)
  flow.enqueue(summary(), { valid: () => activeLibrary.value === 2 })
  assert.equal(flow.queue.value.length, 1)
  activeLibrary.value = 4
  flow.takeNext()
  assert.equal(flow.review.value, null)
  assert.equal(flow.queue.value.length, 0)
})
test('two prepares for the same unseen version leave one current prompt and preserve other shows', () => {
  const flow = flowFor()
  flow.enqueue(summary())
  flow.enqueue(summary({ preview_id: 'newer-preview', plans: [plan(7)] }))
  assert.deepEqual(flow.queue.value.map(batch => batch.plans.map(row => row.show_id)), [[8], [7]])
  flow.enqueue(summary({ preview_id: 'latest-preview', plans: [plan(7)] }))
  assert.deepEqual(flow.queue.value.map(batch => batch.preview_id), ['preview', 'latest-preview'])
})
test('an active draft ignores duplicate versions and a deferred old version keeps genuinely new files queued', async () => {
  const flow = flowFor(async () => ({ ok: true }))
  flow.enqueue(summary({ plans: [plan(7)] })); flow.takeNext()
  flow.enqueue(summary({ preview_id: 'duplicate', plans: [plan(7)] }))
  assert.equal(flow.queue.value.length, 0)
  flow.enqueue(summary({ preview_id: 'new-files', plans: [plan(7, { fingerprint: 'version2' })] }))
  await flow.decide('deferred')
  assert.equal(flow.queue.value.length, 1)
  flow.takeNext(); assert.equal(flow.review.value.preview_id, 'new-files')
})
test('metadata/preview are one job and queued work is polled before any review becomes executable', async () => {
  const calls = [], statuses = [{ state: 'queued', phase: 'queued' }, { state: 'running', phase: 'metadata' }, { state: 'done', summary: summary() }]
  const flow = flowFor(async (url, opts) => { calls.push({ url, opts }); return opts?.method === 'POST' ? { job_id: 'job' } : statuses.shift() })
  const stages = []
  const result = await flow.prepare({ ids: [7], source: 'upload', retry_metadata: false }, { automatic: true, progress: value => stages.push(value) })
  assert.equal(result.preview_id, 'preview')
  assert.equal(calls.length, 4)
  assert.ok(calls.every(call => call.opts.silentViewing && call.opts.silentAuth))
  assert.deepEqual(JSON.parse(calls[0].opts.body), { ids: [7], source: 'upload', retry_metadata: false })
  assert.ok(stages.includes('正在补全剧集资料'))
  assert.equal(flow.review.value, null)
})
test('a submitted job may continue on the server but cannot read or show results after its scope changes', async () => {
  const start = deferred(), calls = []
  const flow = flowFor(async url => { calls.push(url); return start.promise })
  const pending = flow.prepare({ ids: [7], source: 'detail' })
  flow.setContext('tv/8|media2', true, [4]); start.resolve({ job_id: 'old' })
  assert.equal(await pending, null)
  assert.equal(calls.length, 1)
  assert.equal(flow.queue.value.length, 0)
})
test('slightly later and ignored decisions are durable and cannot dismiss a newer scope', async () => {
  const pending = deferred(), calls = []
  const flow = flowFor(async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return pending.promise })
  flow.enqueue(summary()); flow.takeNext()
  const action = flow.decide('deferred')
  assert.deepEqual(calls[0].body, { preview_id: 'preview', ids: [7, 8], decision: 'deferred' })
  flow.setContext('tv/8|media1', true, [2]); flow.enqueue(summary({ preview_id: 'new' })); flow.takeNext()
  pending.resolve({ ok: true }); await action
  assert.equal(flow.review.value.preview_id, 'new')
})
test('automatic filtered decisions affect only the shows displayed to the user', async () => {
  let body
  const flow = flowFor(async (_url, options) => { body = JSON.parse(options.body); return { ok: true } })
  flow.enqueue(summary(), { automatic: true }); flow.takeNext()
  assert.deepEqual(flow.review.value.plans.map(row => row.show_id), [7])
  await flow.decide('ignored')
  assert.deepEqual(body.ids, [7])
})
test('metadata-only completion reports the filled count without opening a physical preview', async t => {
  const flow = flowFor(async (_url, options) => options?.method === 'POST' ? { job_id: 'job' }
    : { state: 'done', summary: summary({ plans: [], metadata_results: [{ show_id: 7, filled: 8 }] }) })
  const scope = effectScope(), state = scope.run(() => useTvIngestFlow({ flow }))
  t.after(() => scope.stop())
  await state.start({ ids: [7] })
  assert.match(state.message.value, /已补全 8 集资料/)
  assert.equal(flow.queue.value.length, 0)
})
test('file replacement and cleanup feedback remains visible when there are no physical organize actions', async t => {
  const flow = flowFor(async (_url, options) => options?.method === 'POST' ? { job_id: 'job' }
    : { state: 'done', summary: summary({ plans: [], metadata_results: [{ show_id: 7, filled: 0, files: { replaced: 1, changed: 2, removed: 3 } }] }) })
  const scope = effectScope(), state = scope.run(() => useTvIngestFlow({ flow }))
  t.after(() => scope.stop())
  await state.start({ ids: [7] })
  assert.match(state.message.value, /识别替换 1 集 · 更新片源 2 集 · 清理失效记录 3 条/)
  assert.equal(flow.queue.value.length, 0)
})
test('pending entries including unprepared and ignored versions are rechecked together', async t => {
  const calls = [], flow = flowFor(async (url, opts) => {
    calls.push({ url, opts })
    if (opts?.method === 'POST') return { job_id: 'job' }
    return { state: 'done', summary: summary() }
  })
  const scope = effectScope(), state = scope.run(() => useTvIngestFlow({ flow }))
  t.after(() => scope.stop())
  await state.pending({ library_id: 2 }, { includeIgnored: true })
  assert.equal(calls[0].url, '/api/jobs/tv-ingest')
  assert.deepEqual(JSON.parse(calls[0].opts.body), { library_id: 2, source: 'manual', retry_metadata: false,
    pending_only: true, include_ignored: true })
  assert.equal(flow.queue.value.length, 1)
})
test('an empty server-frozen pending scope reports no work without checking the entire library', async t => {
  const calls = [], flow = flowFor(async (url, opts) => {
    calls.push({ url, opts })
    return opts?.method === 'POST' ? { job_id: 'empty' }
      : { state: 'done', summary: { plans: [], params: { ids: [] }, metadata_results: [] } }
  })
  const scope = effectScope(), state = scope.run(() => useTvIngestFlow({ flow }))
  t.after(() => scope.stop())
  await state.pending({ library_id: 2 })
  assert.equal(calls.length, 2)
  assert.match(state.message.value, /没有待处理/)
  assert.equal(flow.queue.value.length, 0)
})
test('scope changes release local busy state and old failures do not replace a new request', async t => {
  const old = deferred(), key = ref('first')
  const flow = flowFor(() => old.promise), scope = effectScope()
  const state = scope.run(() => useTvIngestFlow({ flow, scopeKey: () => key.value }))
  t.after(() => scope.stop())
  const request = state.start({ ids: [7] })
  key.value = 'second'; assert.equal(state.busy.value, false)
  old.reject(new Error('old')); await request
  assert.equal(state.message.value, '')
})
test('one explicit confirmation submits the exact prepared batch once and retains completion', async t => {
  const calls = [], flow = flowFor(async (url, opts) => { calls.push({ url, opts }); return opts?.method === 'POST' ? { job_id: 'organize' } : { state: 'done', summary: { moved: 16 } } })
  const { state } = setup(t, flow)
  await state.confirm(); await state.confirm(); await flush()
  const writes = calls.filter(call => call.opts?.method === 'POST')
  assert.equal(writes.length, 1)
  assert.deepEqual(JSON.parse(writes[0].opts.body), { preview_id: 'preview', ids: [7, 8], actions: TV_INGEST_ACTIONS,
    allow_torrent: false, allow_absolute_shows: [], dry_run: false })
  assert.equal(state.phase.value, 'done'); assert.equal(state.result.value.moved, 16)
})
test('changing actions or absolute risk invalidates execution until the new preview returns', async t => {
  const next = deferred(), flow = flowFor(), { state } = setup(t, flow)
  flow.prepare = async body => { assert.deepEqual(body.actions, ['rename']); assert.deepEqual(body.allow_absolute_shows, [7]); return next.promise }
  state.actions.value = ['rename']; state.allowAbsolute.value = [7]
  assert.equal(Boolean(state.canConfirm.value), false)
  const updating = state.refresh(); next.resolve(summary({ preview_id: 'updated', params: { actions: ['rename'], allow_absolute_shows: [7] } })); await updating
  assert.equal(Boolean(state.canConfirm.value), true)
  assert.equal(state.summary.value.preview_id, 'updated')
})
test('review scope and selection survive plans disappearing while actions change', async t => {
  const calls = [], flow = flowFor(), { state } = setup(t, flow)
  state.selected.value = [7]
  flow.prepare = async body => {
    calls.push(body)
    return normalizeIngestSummary(summary({ preview_id: calls.length === 1 ? 'filtered' : 'restored',
      plans: calls.length === 1 ? [plan(8)] : [plan(7), plan(8)] }))
  }
  state.actions.value = ['root']; await state.refresh()
  assert.deepEqual(state.plans.value.map(row => row.show_id), [8])
  assert.deepEqual(state.selected.value, [7])
  assert.equal(state.selectedPlans.value.length, 0)
  state.actions.value = ['root', 'rename']; await state.refresh()
  assert.deepEqual(calls.map(body => body.ids), [[7, 8], [7, 8]])
  assert.deepEqual(calls.map(body => body.scope_preview_id), ['preview', 'filtered'])
  assert.deepEqual(state.selectedPlans.value.map(row => row.show_id), [7])
  assert.ok(state.canConfirm.value)
})
test('an empty refreshed preview can restore its original scope or close without an empty decision', async t => {
  for (const close of ['close', 'decide']) {
    const writes = [], flow = flowFor(async (url, opts) => { writes.push({ url, opts }); return {} })
    const { state } = setup(t, flow, summary({ plans: [plan(7)] }))
    const calls = []
    flow.prepare = async body => {
      calls.push(body)
      return normalizeIngestSummary(summary({ preview_id: 'empty', plans: [plan(7, { groups: [] })] }))
    }
    state.actions.value = ['root']; await state.refresh()
    assert.equal(state.noWork.value, true)
    assert.deepEqual(state.selected.value, [7])
    assert.equal(state.plans.value.length, 0)
    assert.ok(flow.review.value)
    flow.prepare = async body => { calls.push(body); return summary({ preview_id: 'restored', plans: [plan(7)] }) }
    state.actions.value = ['root', 'rename']; await state.refresh()
    assert.deepEqual(calls[1].ids, [7])
    assert.equal(calls[1].scope_preview_id, 'empty')
    assert.equal(state.noWork.value, false)
    flow.prepare = async () => normalizeIngestSummary(summary({ preview_id: 'empty-again', plans: [] }))
    await state.refresh()
    if (close === 'close') state.close()
    else await state.decide('ignored')
    assert.equal(flow.review.value, null)
    assert.deepEqual(writes, [])
  }
})
test('refresh freezes the host-filtered scope and uses a snapshot for more than 500 shows', async t => {
  const plans = Array.from({ length: 501 }, (_, i) => plan(i + 1))
  const flow = flowFor()
  flow.enqueue(summary({ plans: [...plans, plan(900, { library_id: 9 })], auto_prompt_ids: plans.map(row => row.show_id) }), { automatic: true })
  flow.takeNext()
  const scope = effectScope(), state = scope.run(() => useTvIngestReview(flow.review.value, { flow }))
  t.after(() => scope.stop())
  let body
  flow.prepare = async value => { body = value; return summary({ plans }) }
  await state.refresh()
  assert.equal(body.ids.length, 501)
  assert.ok(!body.ids.includes(900))
  assert.equal(body.scope_preview_id, 'preview')
})
test('409 keeps show selection but requires a fresh preview before retry', async t => {
  const flow = flowFor(async () => { throw Object.assign(new Error('stale'), { status: 409 }) })
  const { state } = setup(t, flow); state.selected.value = [8]
  await state.confirm()
  assert.deepEqual(state.selected.value, [8]); assert.equal(state.ready.value, false)
  assert.match(state.error.value, /重新预览/)
  assert.equal(Boolean(state.canConfirm.value), false)
  flow.prepare = async () => summary({ preview_id: 'new' })
  await state.refresh(); assert.deepEqual(state.selected.value, [8]); assert.equal(Boolean(state.canConfirm.value), true)
})
test('failed repreview never restores the old executable snapshot', async t => {
  const flow = flowFor(), { state } = setup(t, flow)
  flow.prepare = async () => { throw new Error('offline') }
  await state.refresh(); await state.confirm()
  assert.equal(state.ready.value, false); assert.match(state.error.value, /offline/)
})
test('read-only and torrent protection cannot be submitted; absolute permission starts off', t => {
  const flow = flowFor(), { state } = setup(t, flow, summary({ plans: [plan(7, { blocked: true }), plan(8, { reason: 'read_only' })] }))
  assert.deepEqual(state.selected.value, []); assert.equal(Boolean(state.canConfirm.value), false)
  assert.deepEqual(state.allowAbsolute.value, [])
})
test('closing an unexecuted preview saves deferred; busy decisions cannot duplicate', async t => {
  const pending = deferred(), flow = flowFor(), calls = []
  flow.decide = async value => { calls.push(value); return pending.promise }
  const { state } = setup(t, flow)
  state.close(); await state.decide('ignored'); assert.deepEqual(calls, ['deferred'])
  pending.resolve(); await flush(); assert.equal(state.busy.value, false)
})
test('a decision network failure leaves the existing pending review and permits closing it', async t => {
  const flow = flowFor(async () => { throw new Error('offline') }), { state } = setup(t, flow)
  await state.decide('deferred')
  assert.equal(state.decisionFailed.value, true)
  assert.ok(flow.review.value)
  assert.match(state.error.value, /保留待办/)
  state.closePending(); assert.equal(flow.review.value, null)
})
test('disposing a review prevents a late result from updating or scheduling more polls', async t => {
  const pending = deferred(), flow = flowFor(async (_url, opts) => opts?.method === 'POST' ? { job_id: 'job' } : pending.promise)
  const { state, scope } = setup(t, flow); await state.confirm(); scope.stop()
  pending.resolve({ state: 'done', summary: { moved: 16 } }); await flush()
  assert.equal(state.result.value, null)
})
