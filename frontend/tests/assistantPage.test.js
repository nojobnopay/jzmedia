import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope, nextTick, ref } from 'vue'
import { assistantPage, useAssistantPage } from '../src/assistantPage.js'

test('the active route owns the tips while the previous route updates or unmounts', async () => {
  const library = effectScope(), online = effectScope()
  const ready = ref(false), playing = ref(false)
  try {
    library.run(() => useAssistantPage('movie', () => ({ tips: ready.value ? [{ id: 'filter' }] : [] })))
    assert.deepEqual(assistantPage.value, { channel: 'movie', tips: [], blocked: false })
    ready.value = true
    online.run(() => useAssistantPage('online', () => ({ tips: [], blocked: playing.value })))
    await nextTick()
    library.stop()
    assert.equal(assistantPage.value.channel, 'online')
    playing.value = true
    await nextTick()
    assert.equal(assistantPage.value.blocked, true)
  } finally {
    library.stop()
    online.stop()
  }
  assert.equal(assistantPage.value, null)
})
