import test from 'node:test'
import assert from 'node:assert/strict'
import { h, provide, ref } from 'vue'
import { createSettingsDraftGuard, SETTINGS_DRAFTS } from '../src/settingsDrafts.js'
import { useSettingsTaskNavigation } from '../src/useSettingsTaskNavigation.js'
import { renderHarness, flush } from './helpers/renderHarness.js'

test('returning from a settings task preserves drafts until explicit discard', async t => {
  const guard = createSettingsDraftGuard(), active = ref(true), dirty = ref(true), busy = ref(false)
  let task, closed = 0
  guard.register({ section: 'sec-libraries', label: '新媒体库', dirty: () => dirty.value,
    discard: () => { dirty.value = false } })
  const child = { setup() {
    task = useSettingsTaskNavigation({ section: 'sec-libraries', active, busy: () => busy.value,
      close: () => { closed++; active.value = false } })
    return () => h('div')
  } }
  const ui = renderHarness({ setup() { provide(SETTINGS_DRAFTS, guard); return () => h(child) } })
  t.after(() => { ui.app.unmount(); guard.dispose() })

  const stay = task.requestClose()
  assert.equal(guard.dialogItems.value[0].label, '新媒体库')
  guard.stay()
  assert.equal(await stay, false)
  assert.equal(active.value, true)
  assert.equal(dirty.value, true)
  assert.equal(closed, 0)

  busy.value = true
  assert.equal(await task.requestClose(), false)
  assert.equal(guard.pending.value, null)
  busy.value = false
  const leave = task.requestClose()
  guard.discardAndLeave()
  assert.equal(await leave, true)
  await flush()
  assert.equal(active.value, false)
  assert.equal(dirty.value, false)
  assert.equal(closed, 1)
})
