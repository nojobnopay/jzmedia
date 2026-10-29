import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush } from './helpers/renderHarness.js'

test('online posters only request opaque same-origin leases and fall back on failure', async t => {
  const component = await loadSfc(new URL('../src/components/OnlineArtwork.vue', import.meta.url))
  const props = Vue.reactive({ title: '合成海报', path: 'https://untrusted.invalid/poster.jpg' })
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  assert.equal(ui.find(node => node.type === 'img'), undefined)
  props.path = '/api/online/catalog/artwork/src_' + '1'.repeat(32) + '/' + '2'.repeat(32)
  await flush()
  assert.equal(ui.find(node => node.type === 'img').props.src, props.path)
  ui.find(node => node.type === 'img').props.onError()
  await flush()
  assert.equal(ui.find(node => node.type === 'img'), undefined)
  assert.equal(ui.text().includes('合成海报'), true)
})
