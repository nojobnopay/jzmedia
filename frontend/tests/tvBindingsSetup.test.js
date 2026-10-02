import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { loadSfc } from './helpers/loadSfc.js'

test('directory binding dialog initializes and renders with its real composable', async () => {
  const filename = new URL('../src/components/TvBindingsDialog.vue', import.meta.url)
  const component = await loadSfc(filename)
  const app = Vue.createSSRApp(component, { libraryId: 3 })
  app.component('RouterLink', { template: '<a><slot /></a>' })
  const context = {}
  await renderToString(app, context)
  assert.match(context.teleports.body, /剧集归属与季号/)
  assert.match(context.teleports.body, /选择目录/)
  assert.match(context.teleports.body, /没有符合条件的剧集目录/)
})
