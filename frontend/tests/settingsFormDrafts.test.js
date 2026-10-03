import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'

// Exercise the actual SFC setup/watchers with reactive parent props. Browser
// coverage handles field interactions; these tests isolate async prop refreshes.
async function setupForm(t, name, initialProps) {
  const forms = [], entries = [], scope = Vue.effectScope()
  const component = await loadSfc(new URL(`../src/components/${name}.vue`, import.meta.url), {
    vue: { ...Vue, reactive(value) { const state = Vue.reactive(value); forms.push(state); return state } },
    '../settingsDrafts.js': { useSettingsDraft(entry) { entries.push(entry) } },
  })
  const props = Vue.reactive(initialProps)
  scope.run(() => component.setup(props, { expose() {}, emit() {} }))
  t.after(() => scope.stop())
  return { props, form: forms.find(state => 'videos' in state) || forms[0], states: forms, entry: entries[0] }
}

test('TMDB parent settings refresh retains edited fields and credentials while updating untouched defaults', async t => {
  const { props, form, entry } = await setupForm(t, 'TmdbSettingsPanel', { settings: {
    tmdb_proxy: 'http://old:8080', tmdb_language: 'zh-CN', tmdb_image_base: 'https://images.example',
  } })
  assert.equal(entry.dirty(), false)
  form.readToken = 'private-unsaved-token'
  form.proxy = 'http://draft:8888'
  props.settings = { ...props.settings, tmdb_language: 'en-US', jzmedia_token_masked: '***' }
  await Vue.nextTick()
  assert.equal(form.readToken, 'private-unsaved-token')
  assert.equal(form.proxy, 'http://draft:8888')
  assert.equal(form.language, 'en-US')
  assert.equal(entry.dirty(), true)
  entry.discard()
  assert.equal(entry.dirty(), false)
  assert.equal(form.readToken, '')
  assert.equal(form.proxy, 'http://old:8080')
  assert.equal(form.language, 'en-US')
})

test('same-library props refresh preserves video edits; discard restores latest saved fields', async t => {
  const { props, form, entry } = await setupForm(t, 'VideoLibraryForm', { library: {
    id: 7, name: '电影', subpath: 'Movies', kind: 'movie', movie_count: 0,
  } })
  assert.equal(entry.dirty(), false)
  form.name = '未保存名称'
  props.library = { ...props.library, subpath: 'Updated', movie_count: 1 }
  await Vue.nextTick()
  assert.equal(form.name, '未保存名称')
  assert.equal(form.subpath, 'Updated')
  assert.equal(entry.dirty(), true)
  entry.discard()
  assert.equal(entry.dirty(), false)
  assert.equal(form.name, '电影')
  assert.equal(form.subpath, 'Updated')
})

test('opening media creation is clean; editing configuration and explicit discard change the guard', async t => {
  const { form, states, entry } = await setupForm(t, 'MediaLibraryCreateForm', { kind: 'tv' })
  assert.equal(entry.dirty(), false)
  assert.equal(entry.busy(), false)
  form.name = '新媒体库草稿'
  form.videos[0].subpath = 'TV Shows'
  assert.equal(entry.dirty(), true)
  states[0].busy = true // Connection diagnostics are still in flight.
  assert.equal(entry.busy(), true)
  states[0].busy = false
  entry.discard()
  assert.equal(entry.dirty(), false)
  assert.equal(form.name, '')
  assert.equal(form.videos[0].subpath, '')
  assert.equal(form.videos[0].kind, 'tv')
})
