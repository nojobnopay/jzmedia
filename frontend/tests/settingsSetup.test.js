import test from 'node:test'
import assert from 'node:assert/strict'
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

test('settings initializes matching drafts and grouped navigation without setup requests', async () => {
  const blank = { setup: () => () => Vue.h('div') }
  const filename = new URL('../src/views/Settings.vue', import.meta.url)
  const component = await loadSfc(filename, {
    'vue-router': { useRoute: () => ({ query: { sec: 'sec-matching', library: '7' } }), useRouter: () => ({}) },
    '../api.js': { api: () => { throw new Error('Rendering must not send API requests') }, setToken: () => {} },
    '../libraries.js': { currentMediaId: () => 2, listLibs: () => [
      { id: 7, name: '电影库', kind: 'movie', metadata_providers: '["tmdb","local"]' },
    ], loadLibs: async () => {} },
    '../components/TmdbSettingsPanel.vue': { default: blank },
    '../components/AiSettingsPanel.vue': { default: blank },
    '../components/LibrariesPanel.vue': { default: blank },
    '../components/LibraryToolsPanel.vue': { default: blank },
    '../components/TranscodeCachePanel.vue': { default: blank },
  })
  const app = Vue.createSSRApp(component)
  app.component('RouterLink', { setup: (_props, { slots }) => () => Vue.h('a', slots.default?.()) })
  const html = await renderToString(app)
  assert.match(html, /<h2[^>]*class="nav-group"[^>]*>媒体管理<\/h2>/)
  assert.match(html, /<optgroup label="资料与智能">/)
  assert.match(html, /测试当前视频库匹配/)
  assert.match(html, /TMDB → 本地索引/)
  assert.match(html, /文件管理/)
})
