import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope } from 'vue'
import { useTvBindings } from '../src/useTvBindings.js'

const directory = (path = '纵横') => ({ path, files: 2, episodes: [1, 2], selected: true })
function deferred() {
  let resolve, reject
  const promise = new Promise((a, b) => { resolve = a; reject = b })
  return { promise, resolve, reject }
}
function setup(t, request, opts = {}) {
  const scope = effectScope()
  const state = scope.run(() => useTvBindings(3, 7, { request, ...opts }))
  t.after(() => scope.stop())
  return { state, scope }
}
function ready(state) {
  state.rows.value = [{ ...directory(), checked: true, season: 2, override: false }]
  state.choose({ tmdb_id: 32231, title: '大秦帝国' })
}
const preview = { token: 'a'.repeat(32), can_apply: true, episodes: 2 }

test('preview carries exact library and directory scope; confirmation uses only the reviewed token', async t => {
  const calls = [], mutation = deferred()
  const { state } = setup(t, (url, opts) => {
    calls.push({ url, body: opts?.body && JSON.parse(opts.body) })
    if (url.endsWith('/preview/start')) return { state: 'done', result: preview }
    if (url.endsWith('/apply/start')) return mutation.promise.then(result => ({ state: 'done', result }))
    return { items: [] }
  })
  ready(state)
  await state.apply()
  assert.equal(calls.length, 0)
  await state.preview()
  assert.deepEqual(calls[0].body, { library_id: 3, tmdb_id: 32231, target_show_id: null,
    directories: [{ path: '纵横', season: 2, override_season: false }], replace_manual: false, allow_duplicates: false })
  const pending = state.apply()
  await state.apply()
  assert.equal(calls.filter(c => c.url.endsWith('/apply/start')).length, 1)
  assert.deepEqual(calls[1].body, { token: preview.token })
  mutation.resolve({ show_id: 20, directories: 1, episodes: 2 })
  await pending
  assert.equal(state.completed.value.show_id, 20)
  assert.equal(state.canApply.value, false)
})

test('every numbering or target change invalidates a previously executable preview', async t => {
  const { state } = setup(t, async () => ({ state: 'done', result: preview }))
  ready(state)
  await state.preview()
  state.rows.value[0].season = 3
  assert.equal(state.canApply.value, false)
  await state.preview()
  state.allowDuplicates.value = true
  assert.equal(state.canApply.value, false)
  await state.preview()
  state.choose({ tmdb_id: 114043, title: '大秦赋' })
  assert.equal(state.plan.value, null)
})

test('an obsolete preview cannot overwrite a later selection or a failed preview', async t => {
  const first = deferred(), next = deferred()
  let count = 0
  const { state } = setup(t, () => ++count === 1 ? first.promise : next.promise)
  ready(state)
  const old = state.preview()
  state.rows.value[0].override = true
  const fresh = state.preview()
  next.reject(new Error('资料来源暂不可用'))
  await fresh
  first.resolve({ state: 'done', result: preview })
  await old
  assert.equal(state.plan.value, null)
  assert.match(state.error.value, /暂不可用/)
})

test('getting suggestions never applies them; unique title/year evidence can prefill on explicit selection', async t => {
  const candidate = { tmdb_id: 32231, title: '大秦帝国', seasons: [{ season_number: 1 }, { season_number: 2 }],
    directories: [{ path: '纵横', suggestions: [{ season: 2, score: 9, reasons: ['季名相符'] }] }] }
  const { state } = setup(t, async () => ({ state: 'done', result: { items: [candidate], warnings: [] } }))
  ready(state)
  state.rows.value[0].season = ''
  await state.suggest()
  assert.equal(state.rows.value[0].season, '')
  state.choose(candidate, true)
  assert.equal(state.rows.value[0].season, 2)
  state.rows.value[0].season = ''
  candidate.directories[0].suggestions = [{ season: 2, score: 1 }, { season: 3, score: 1 }]
  state.choose(candidate, true)
  assert.equal(state.rows.value[0].season, '')
})

test('changing directories discards late suggestions for the old scope', async t => {
  const pending = deferred()
  const { state } = setup(t, () => pending.promise)
  ready(state)
  const p = state.suggest()
  state.rows.value[0].checked = false
  pending.resolve({ state: 'done', result: { items: [{ tmdb_id: 123 }], warnings: [] } })
  await p
  assert.deepEqual(state.candidates.value, [])
})

test('conflicted previews cannot execute and failed mutations retain the token for an idempotent retry', async t => {
  let conflict = true
  const { state } = setup(t, async url => {
    if (url.endsWith('/preview/start')) return { state: 'done', result: { ...preview, can_apply: !conflict } }
    throw new Error('连接中断')
  })
  ready(state)
  await state.preview()
  assert.equal(state.canApply.value, false)
  conflict = false
  await state.preview()
  await state.apply()
  assert.equal(state.plan.value.token, preview.token)
  assert.equal(state.applying.value, false)
  assert.match(state.error.value, /连接中断/)
})

test('undo requires a separate preview and confirmation', async t => {
  const calls = []
  const { state } = setup(t, async (url, opts) => {
    const body = opts?.body && JSON.parse(opts.body)
    if (url.endsWith('/undo')) { calls.push(body); return { title: '大秦帝国', episodes: 2, directories: ['纵横'] } }
    return { items: [], shows: [] }
  })
  await state.undo()
  assert.equal(calls.length, 0)
  await state.previewUndo({ id: 'b'.repeat(32) })
  assert.deepEqual(calls.map(c => c.dry_run), [true])
  await state.undo()
  assert.deepEqual(calls.map(c => c.dry_run), [true, false])
  assert.equal(state.completed.value.undone, true)
})

test('long operations start once and are completed through job polling', async t => {
  let polls = 0
  const { state } = setup(t, async url => {
    if (url.endsWith('/preview/start')) return { state: 'running', job_id: 'job-1' }
    if (url.endsWith('/jobs/job-1')) {
      polls++
      return polls === 1 ? { state: 'running' } : { state: 'done', result: preview }
    }
    throw new Error(`unexpected request: ${url}`)
  }, { pollDelay: 0 })
  ready(state)
  await state.preview()
  assert.equal(polls, 2)
  assert.equal(state.plan.value.token, preview.token)
  assert.equal(state.previewing.value, false)
})

test('unmount aborts pending reads and ignores late responses', async t => {
  const pending = deferred(), signals = []
  const { state, scope } = setup(t, (_url, opts) => { signals.push(opts.signal); return pending.promise })
  ready(state)
  const p = state.preview()
  scope.stop()
  assert.ok(signals.every(s => s.aborted))
  pending.resolve({ state: 'done', result: preview })
  await p
  assert.equal(state.plan.value, null)
})

test('an unchanged storage refresh preserves the reviewed preview and manual choices', async t => {
  const refreshed = deferred()
  const row = { ...directory(), binding: { season: 2 }, selected: true }
  const { state } = setup(t, async url => {
    if (url.includes('/directories?')) return { items: [row], shows: [] }
    if (url.includes('/history?')) return { items: [] }
    if (url.endsWith('/directories/refresh')) return { state: 'running', job_id: 'inventory-1' }
    if (url.endsWith('/jobs/inventory-1')) return refreshed.promise
    if (url.endsWith('/preview/start')) return { state: 'done', result: preview }
    throw new Error(`unexpected request: ${url}`)
  }, { pollDelay: 0 })
  await state.load()
  state.choose({ tmdb_id: 32231, title: '大秦帝国' })
  state.rows.value[0].season = 2
  await state.preview()
  assert.equal(state.plan.value.token, preview.token)
  refreshed.resolve({
    state: 'done',
    result: { items: [{ ...row, files: 3 }], shows: [] },
  })
  while (state.refreshing.value) await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(state.plan.value.token, preview.token)
  assert.equal(state.rows.value[0].files, 3)
})
