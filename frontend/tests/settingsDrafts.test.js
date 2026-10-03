import test from 'node:test'
import assert from 'node:assert/strict'
import { ref } from 'vue'
import { createMemoryHistory, createRouter, isNavigationFailure } from 'vue-router'
import { createSettingsDraftGuard } from '../src/settingsDrafts.js'

function form(guard, section = 'sec-tmdb') {
  const dirty = ref(false), busy = ref(false)
  let discards = 0
  const unregister = guard.register({ section, label: section, dirty: () => dirty.value, busy: () => busy.value,
    discard() { dirty.value = false; discards++ } })
  return { dirty, busy, unregister, get discards() { return discards } }
}

test('only changed forms or active operations protect unload; section navigation checks its own drafts', async () => {
  const guard = createSettingsDraftGuard(), tmdb = form(guard), ai = form(guard, 'sec-ai')
  let prevented = 0
  const event = { preventDefault() { prevented++ } }
  guard.beforeUnload(event)
  assert.equal(prevented, 0)
  tmdb.dirty.value = true
  guard.beforeUnload(event)
  assert.equal(prevented, 1)
  assert.equal(event.returnValue, '')
  assert.equal(guard.requestLeave({}, 'sec-ai'), true)
  const blocked = guard.requestLeave({})
  assert.deepEqual(guard.dialogItems.value.map(item => item.label), ['sec-tmdb'])
  guard.stay()
  assert.equal(await blocked, false)
  assert.equal(tmdb.dirty.value, true)
  tmdb.dirty.value = false
  ai.busy.value = true
  assert.equal(guard.protectedState.value, true)
  ai.unregister()
  assert.equal(guard.protectedState.value, false)
})

test('discard is committed only after accepted navigation and never after a later guard refuses', async () => {
  const guard = createSettingsDraftGuard(), draft = form(guard)
  draft.dirty.value = true
  const failedTarget = {}, successTarget = {}
  const first = guard.requestLeave(failedTarget)
  guard.discardAndLeave()
  assert.equal(await first, true)
  assert.equal(draft.dirty.value, true, 'Approval is not a successful navigation')
  guard.finishNavigation(failedTarget, new Error('File review declined'))
  assert.equal(draft.discards, 0)
  const second = guard.requestLeave(successTarget)
  guard.discardAndLeave()
  assert.equal(await second, true)
  guard.finishNavigation(successTarget)
  assert.equal(draft.discards, 1)
  assert.equal(draft.dirty.value, false)
  guard.finishNavigation(successTarget)
  assert.equal(draft.discards, 1, 'Approval is consumed exactly once')
})

test('saving blocks discard, failed save retains its draft, and another destination cannot reuse the dialog', async () => {
  const guard = createSettingsDraftGuard(), draft = form(guard)
  draft.dirty.value = true
  draft.busy.value = true
  const target = {}, leaving = guard.requestLeave(target)
  assert.equal(guard.dialogBusy.value, true)
  assert.equal(guard.requestLeave({}), false)
  guard.discardAndLeave()
  assert.equal(guard.pending.value.to, target)
  assert.equal(draft.discards, 0)
  draft.busy.value = false // A failed request ends without changing the values.
  assert.equal(guard.dialogBusy.value, false)
  guard.stay()
  assert.equal(await leaving, false)
  assert.equal(draft.dirty.value, true)
  const another = guard.requestLeave({})
  guard.dispose()
  assert.equal(await another, false)
  assert.equal(draft.discards, 0)
  assert.equal(guard.protectedState.value, false)
})

test('a save finishing successfully closes the now-obsolete prompt and stays on the current page', async () => {
  const guard = createSettingsDraftGuard(), draft = form(guard)
  draft.dirty.value = true
  draft.busy.value = true
  const departure = guard.requestLeave({})
  draft.dirty.value = false
  assert.ok(guard.pending.value, 'The in-flight request still prevents leaving')
  draft.busy.value = false
  assert.equal(await departure, false)
  assert.equal(guard.pending.value, null)
  assert.equal(draft.discards, 0)
  assert.equal(guard.requestLeave({}), true)
})

test('the real router preserves drafts when file review rejects and clears only on completed navigation', async () => {
  const guard = createSettingsDraftGuard(), draft = form(guard)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/settings', component: {} }, { path: '/movies', component: {} },
  ] })
  await router.push('/settings')
  router.beforeEach(to => guard.requestLeave(to))
  let fileReviewAllows = false
  router.beforeEach(() => fileReviewAllows)
  router.afterEach((to, _from, failure) => guard.finishNavigation(to, failure))
  draft.dirty.value = true
  const waitForDialog = async () => {
    for (let i = 0; i < 30 && !guard.pending.value; i++) await Promise.resolve()
    assert.ok(guard.pending.value)
  }
  let navigation = router.push('/movies')
  await waitForDialog()
  guard.discardAndLeave()
  assert.ok(isNavigationFailure(await navigation))
  assert.equal(draft.dirty.value, true)
  assert.equal(router.currentRoute.value.path, '/settings')
  fileReviewAllows = true
  navigation = router.push('/movies')
  await waitForDialog()
  guard.discardAndLeave()
  assert.equal(await navigation, undefined)
  assert.equal(draft.dirty.value, false)
  assert.equal(router.currentRoute.value.path, '/movies')
})
