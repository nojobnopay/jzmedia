import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { loadSfc } from './helpers/loadSfc.js'
import { parseSmbInput, smbUrlOf } from '../src/smb.js'

test('media library form initializes and renders before any connection is loaded', async () => {
  // Compiling alone misses watchers that read reactive state before initialization.
  const filename = new URL('../src/components/LibrariesPanel.vue', import.meta.url)
  const component = await loadSfc(filename, {
    '../api.js': { api: () => { throw new Error('Rendering must not send API requests') } },
    '../libraries.js': { loadLibs: async () => {} },
    '../smb.js': { parseSmbInput, smbUrlOf },
  })
  const html = await renderToString(Vue.createSSRApp(component))
  assert.match(html, /媒体库列表/)
  assert.match(html, /创建媒体库/)
  assert.match(html, /jz-lib-name/)
})

test('library action controls keep the table cell layout', () => {
  const filename = new URL('../src/components/LibrariesPanel.vue', import.meta.url)
  const source = readFileSync(filename, 'utf8')
  assert.equal((source.match(/<td class="ops">\s*<div class="ops-wrap">/g) || []).length, 2)
  assert.match(source, /\.lib-table \.ops-wrap \{ display: flex;/)
  assert.doesNotMatch(source, /\.lib-table \.ops \{[^}]*display:\s*flex;/)
})
