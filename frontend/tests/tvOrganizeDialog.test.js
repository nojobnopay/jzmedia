import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope } from 'vue'
import { useTvOrganizeDialog } from '../src/useTvOrganizeDialog.js'

const hint = (extra = {}) => ({ show_id: 7, needs: true, absolute_risk: true,
  groups: [{ action: 'season', episodes: 19 }], params: { ids: [7], actions: ['season', 'rename'] }, ...extra })
function deferred() {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}
function setup(t, request, initial = hint(), options = {}) {
  const scope = effectScope()
  const state = scope.run(() => useTvOrganizeDialog(initial, { request, ...options }))
  t.after(() => scope.stop())
  return { state, scope }
}

test('two explicit steps, exact show scope, and absolute renaming stays off by default', async t => {
  const requests = []
  const pending = deferred()
  const { state } = setup(t, async (url, opts) => {
    requests.push({ url, body: opts?.body && JSON.parse(opts.body) })
    if (opts?.method === 'POST') return { job_id: 'job' }
    return pending.promise
  })
  await state.proceed()
  assert.equal(state.phase.value, 'confirm')
  assert.equal(requests.length, 0)
  await state.proceed()
  await state.proceed()
  assert.equal(state.running.value, true)
  assert.equal(requests.filter(r => r.body).length, 1)
  assert.deepEqual(requests[0].body, { ids: [7], actions: ['season', 'rename'], dry_run: false, allow_absolute_shows: [] })
  pending.resolve({ state: 'running' })
})

test('changing options invalidates the confirmation immediately; only the new preview can run', async t => {
  const requests = []
  const { state } = setup(t, async (url, opts) => {
    requests.push({ url, body: opts?.body && JSON.parse(opts.body) })
    if (opts?.method === 'POST') return { job_id: 'job' }
    if (url.includes('/organize-hint')) return hint({ groups: [{ action: 'season', episodes: 19 }] })
    return { state: 'done', summary: { moved: 19 } }
  })
  await state.proceed()
  state.selected.value = ['season']
  assert.equal(state.phase.value, 'preview')
  assert.equal(state.canProceed.value, false)
  await state.proceed()
  assert.equal(requests.length, 0)
  await state.refresh()
  assert.match(requests[0].url, /actions=season$/)
  await state.proceed()
  assert.equal(requests.length, 1)
  await state.proceed()
  assert.deepEqual(requests.find(r => r.body).body.actions, ['season'])
})

test('confirming absolute numbering refreshes the preview before enabling execution', async t => {
  const requests = []
  const { state } = setup(t, async (url, opts) => {
    requests.push({ url, body: opts?.body && JSON.parse(opts.body) })
    if (opts?.method === 'POST') return { job_id: 'job' }
    if (url.includes('/organize-hint')) return hint({ groups: [{ action: 'rename', episodes: 19 }] })
    return { state: 'done', summary: { renamed: 19 } }
  })
  state.allowAbsolute.value = true
  assert.equal(state.canProceed.value, false)
  await state.refresh()
  assert.match(requests[0].url, /allow_absolute=true/)
  assert.equal(state.plan.value.groups[0].action, 'rename')
  await state.proceed()
  await state.proceed()
  assert.deepEqual(requests.find(r => r.body).body.allow_absolute_shows, [7])
})

test('a late preview cannot overwrite the latest options or execution body', async t => {
  const old = deferred(), latest = deferred()
  let calls = 0
  const { state } = setup(t, () => (++calls === 1 ? old.promise : latest.promise))
  const first = state.refresh()
  state.selected.value = ['season']
  const second = state.refresh()
  latest.resolve(hint({ title: 'latest' }))
  await second
  old.resolve(hint({ title: 'stale' }))
  await first
  assert.equal(state.plan.value.title, 'latest')
  assert.equal(state.ready.value, true)
})

test('failed or empty previews cannot fall back to an old executable plan', async t => {
  const { state } = setup(t, async () => { throw new Error('offline') })
  await state.refresh()
  assert.equal(state.canProceed.value, false)
  assert.match(state.error.value, /offline/)
  state.selected.value = []
  await state.proceed()
  assert.equal(state.loading.value, false)
  assert.equal(state.phase.value, 'preview')
  assert.equal(state.canProceed.value, false)
})

test('blocked, read-only, no-op and wrong-show responses are never executable', t => {
  for (const extra of [{ blocked: true }, { reason: 'read_only' }, { needs: false }, { params: { ids: [8], actions: ['season'] } }]) {
    const { state } = setup(t, async () => {}, hint(extra))
    assert.equal(Boolean(state.canProceed.value), false)
  }
})

test('completed result stays visible and cannot be submitted twice', async t => {
  const finished = []
  const { state } = setup(t, async (_url, opts) => opts?.method === 'POST'
    ? { job_id: 'job' } : { state: 'done', summary: { moved: 19, renamed: 1, skipped: 2 } },
  hint(), { onFinished: result => finished.push(result) })
  await state.proceed()
  await state.proceed()
  await Promise.resolve()
  assert.equal(state.phase.value, 'done')
  assert.equal(state.result.value.moved, 19)
  assert.equal(state.canProceed.value, false)
  assert.equal(finished.length, 1)
})

test('closing the dialog discards late responses', async t => {
  const pending = deferred()
  const { state, scope } = setup(t, () => pending.promise)
  const request = state.refresh()
  scope.stop()
  pending.resolve(hint({ title: 'late' }))
  await request
  assert.equal(state.plan.value.title, undefined)
})


test('startup failure requires a fresh preview before another submission', async t => {
  let posts = 0
  const { state } = setup(t, async (_url, opts) => {
    if (opts?.method === 'POST') { posts++; throw new Error('busy') }
    return hint()
  })
  await state.proceed()
  await state.proceed()
  assert.equal(state.phase.value, 'failed')
  assert.match(state.error.value, /busy/)
  await state.proceed()
  assert.equal(posts, 1)
  await state.refresh()
  assert.equal(state.phase.value, 'preview')
  assert.equal(state.canProceed.value, true)
})

test('a failed background job leaves an actionable failure state', async t => {
  const { state } = setup(t, async (_url, opts) => opts?.method === 'POST'
    ? { job_id: 'job' } : { state: 'failed', error: 'storage offline' })
  await state.proceed()
  await state.proceed()
  await Promise.resolve()
  assert.equal(state.phase.value, 'failed')
  assert.match(state.error.value, /storage offline/)
  assert.equal(state.running.value, false)
  assert.equal(state.canProceed.value, false)
})
