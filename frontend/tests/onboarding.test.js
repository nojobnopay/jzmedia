import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope } from 'vue'
import { checkReady, itemLink, uploadSummary } from '../src/onboarding.js'
import { uploadTargets } from '../src/libraries.js'
import { useOnboarding } from '../src/useOnboarding.js'

const deferred = () => {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}
function setup(t, request) {
  const scope = effectScope()
  const guide = scope.run(() => useOnboarding(request))
  t.after(() => scope.stop())
  return { guide, scope }
}
test('pinned upload never falls back across media libraries or media types', () => {
  const all = [{ id: 1, kind: 'movie' }, { id: 2, kind: 'movie' }, { id: 3, kind: 'tv' },
    { id: 4, kind: 'movie', read_only: true }, { id: 5, kind: 'movie', media_enabled: false }]
  assert.deepEqual(uploadTargets(all, [all[0]], 'movie', 2).map(l => l.id), [2])
  assert.deepEqual(uploadTargets(all, [all[0]], 'movie', 3), [])
  assert.deepEqual(uploadTargets(all, [all[0]], 'movie', 4), [])
  assert.deepEqual(uploadTargets(all, [all[0]], 'movie', 5), [])
  assert.deepEqual(uploadTargets(all, [all[0]], 'movie', 999), [])
  assert.deepEqual(uploadTargets(all, [all[0]], 'movie').map(l => l.id), [1])
})
test('connection requires readable parent and accessible video directory, readonly is allowed', () => {
  assert.equal(checkReady({ readable: true, writable: false, video: { ok: true } }), true)
  assert.equal(checkReady({ readable: true, video: { ok: false } }), false)
  assert.equal(checkReady({ readable: false, video: { ok: true } }), false)
})
test('result links open movie and episode details; transfer totals are not registration counts', () => {
  assert.equal(itemLink({ id: 4 }, 'movie'), '/m/4')
  assert.equal(itemLink({ id: 9, show_id: 3, season: 2 }, 'tv'), '/tv/3/s/2/e/9')
  assert.match(uploadSummary({ uploaded: 1, failed: 2, skipped: 3, cancelled: true }), /已取消.*失败 2.*仍需确认/)
})
test('a stale read cannot undo a saved step', async t => {
  const old = deferred()
  const { guide } = setup(t, (url, opts) => opts ? Promise.resolve({ step: 3 }) : old.promise)
  const read = guide.refresh()
  await guide.save({ step: 3 })
  old.resolve({ step: 1 })
  await read
  assert.equal(guide.state.value.step, 3)
})
test('duplicate saves are blocked and a failed save cannot advance', async t => {
  const wait = deferred()
  let calls = 0
  const { guide } = setup(t, () => { calls++; return wait.promise })
  const first = guide.save({ step: 3 })
  await guide.save({ step: 4 })
  assert.equal(calls, 1)
  wait.resolve({ step: 2 })
  await first
  assert.equal(guide.state.value.step, 2)
  const failed = setup(t, async () => { throw new Error('offline') }).guide
  await failed.save({ step: 4 })
  assert.equal(failed.state.value, null)
  assert.match(failed.error.value, /offline/)
})
test('leaving the page ignores pending responses', async t => {
  const wait = deferred()
  const { guide, scope } = setup(t, () => wait.promise)
  const request = guide.refresh()
  scope.stop(); wait.resolve({ step: 3 })
  await request
  assert.equal(guide.state.value, null)
})
