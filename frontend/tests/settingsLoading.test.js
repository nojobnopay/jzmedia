import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'

const flush = async () => {
  for (let i = 0; i < 10; i++) { await Promise.resolve(); await Vue.nextTick() }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const sampleLibraries = [
  { id: 7, name: '电影', kind: 'movie', media_library_id: 2 },
  { id: 9, name: '剧集', kind: 'tv', media_library_id: 3 },
]

async function mountSettings(t) {
  const previousWindow = globalThis.window
  const listeners = new Map()
  globalThis.window = {
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, new Set())
      listeners.get(name).add(callback)
    },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback) },
    dispatchEvent(event) { for (const callback of listeners.get(event.type) || []) callback(event) },
    scrollTo() {},
  }
  const libraryRequest = deferred()
  const otherRequests = new Map(['/api/settings', '/api/jobs/stats', '/api/tv/stats', '/api/metadata/providers']
    .map(path => [path, deferred()]))
  const requested = [], libraryLoads = [], focusCalls = []
  let libraries = [], currentMedia = null, toolProps, emitLibraryChange
  const route = Vue.reactive({ path: '/settings', query: { sec: 'sec-files', library: '7', media: '2' } })
  const router = { afterEach: () => () => {}, push: async target => { route.query = target.query } }
  const blank = { setup: () => () => Vue.h('div') }
  const tools = {
    props: ['libs', 'currentMediaId', 'librariesReady', 'librariesError', 'active'],
    setup(props, { expose }) {
      toolProps = props
      expose({ focus: async target => { focusCalls.push({ ...target }) } })
      return () => Vue.h('div')
    },
  }
  const libraryPanel = {
    emits: ['changed'],
    setup(_props, { emit, expose }) {
      emitLibraryChange = () => emit('changed')
      expose({ ensure() {} })
      return () => Vue.h('div')
    },
  }
  const component = await loadSfc(new URL('../src/views/Settings.vue', import.meta.url), {
    vue: { ...Vue, vShow: {}, vModelCheckbox: {}, vModelSelect: {}, vModelText: {} },
    'vue-router': { useRoute: () => route, useRouter: () => router, onBeforeRouteLeave() {}, onBeforeRouteUpdate() {} },
    '../api.js': { api: path => {
      requested.push(path)
      assert.ok(otherRequests.has(path), 'Unexpected settings request: ' + path)
      return otherRequests.get(path).promise
    }, setToken() {} },
    '../libraries.js': {
      listLibs: () => libraries,
      currentMediaId: () => currentMedia,
      loadLibs: async (_api, options) => {
        libraryLoads.push(options)
        libraries = await libraryRequest.promise
        currentMedia = libraries[0]?.media_library_id ?? null
      },
    },
    '../components/TmdbSettingsPanel.vue': { default: blank },
    '../components/AiSettingsPanel.vue': { default: blank },
    '../components/LibrariesPanel.vue': { default: libraryPanel },
    '../components/LibraryToolsPanel.vue': { default: tools },
    '../components/TranscodeCachePanel.vue': { default: blank },
  })
  const element = () => ({ children: [], parent: null })
  const remove = node => {
    if (!node.parent) return
    const siblings = node.parent.children
    const index = siblings.indexOf(node)
    if (index >= 0) siblings.splice(index, 1)
    node.parent = null
  }
  const renderer = Vue.createRenderer({
    createElement: element, createText: element, createComment: element,
    setText() {}, setElementText() {}, patchProp() {},
    insert(node, parent, anchor) {
      remove(node)
      node.parent = parent
      const index = parent.children.indexOf(anchor)
      if (index < 0) parent.children.push(node)
      else parent.children.splice(index, 0, node)
    },
    remove, parentNode: node => node.parent,
    nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  })
  const app = renderer.createApp(component)
  app.component('RouterLink', { setup: (_props, { slots }) => () => Vue.h('a', slots.default?.()) })
  app.mount(element())
  const finishOtherRequests = () => { for (const pending of otherRequests.values()) pending.resolve({}) }
  t.after(async () => {
    try {
      libraryRequest.resolve([])
      finishOtherRequests()
      await flush()
      app.unmount()
      assert.equal(listeners.get('jzmedia:libraries-changed')?.size || 0, 0, 'Unmount removes the recovery listener')
      assert.equal(listeners.get('beforeunload')?.size || 0, 0, 'Unmount removes the draft refresh guard')
    } finally { globalThis.window = previousWindow }
  })
  await flush()
  return {
    route, libraryRequest, requested, libraryLoads, focusCalls, finishOtherRequests,
    get toolProps() { return toolProps },
    notifyPanelChanged: () => emitLibraryChange(),
    publishLibraries(items, media) {
      libraries = items
      currentMedia = media
      window.dispatchEvent({ type: 'jzmedia:libraries-changed' })
    },
  }
}

test('cold library failure stays distinct from empty; a successful global refresh restores the current deep link', async t => {
  const ui = await mountSettings(t)
  assert.equal(ui.toolProps.librariesReady, false)
  assert.equal(ui.toolProps.librariesError, '')
  assert.deepEqual(ui.focusCalls, [])
  ui.libraryRequest.reject(new Error('NAS 连接暂不可用'))
  await flush()
  assert.equal(ui.toolProps.librariesReady, false, 'A failed request must not become a loaded empty library list')
  assert.equal(ui.toolProps.librariesError, 'NAS 连接暂不可用')
  assert.deepEqual(ui.toolProps.libs, [])
  assert.deepEqual(ui.focusCalls, [])

  // LibrariesPanel emits changed after both successful and failed operations.
  // That notification alone must not certify that the global library list loaded.
  ui.route.query = { sec: 'sec-libraries' }
  await flush()
  ui.notifyPanelChanged()
  await flush()
  assert.equal(ui.toolProps.librariesReady, false)
  assert.equal(ui.toolProps.librariesError, 'NAS 连接暂不可用')
  ui.route.query = { sec: 'sec-files', library: '9', media: '3' }
  await flush()
  assert.deepEqual(ui.focusCalls, [])

  ui.publishLibraries(sampleLibraries, 3)
  await flush()
  assert.equal(ui.toolProps.librariesReady, true)
  assert.equal(ui.toolProps.librariesError, '')
  assert.deepEqual(ui.toolProps.libs.map(library => library.id), [7, 9])
  assert.equal(ui.toolProps.currentMediaId, 3)
  assert.deepEqual(ui.focusCalls, [{ page: 'sec-files', sec: 'sec-files', library: 9, media: 3, ids: [] }])
  assert.equal(ui.libraryLoads.length, 1, 'The successful event supplies the already-loaded list without another request')

  ui.route.query = { sec: 'sec-restore', library: '7', media: '2', ids: '11,12' }
  await flush()
  assert.deepEqual(ui.focusCalls.at(-1), { page: 'sec-libtools', sec: 'sec-restore', library: 7, media: 2, ids: [11, 12] })
})

test('library tools focus as soon as their list loads, while unrelated settings and statistics remain pending', async t => {
  const ui = await mountSettings(t)
  assert.deepEqual(new Set(ui.requested), new Set(['/api/settings', '/api/jobs/stats', '/api/tv/stats', '/api/metadata/providers']))
  ui.libraryRequest.resolve(sampleLibraries)
  await flush()
  assert.equal(ui.toolProps.librariesReady, true)
  assert.equal(ui.toolProps.librariesError, '')
  assert.deepEqual(ui.focusCalls, [{ page: 'sec-files', sec: 'sec-files', library: 7, media: 2, ids: [] }],
    'Tool focus cannot wait for Promise.all of settings, stats and provider status')
  ui.finishOtherRequests()
  await flush()
  assert.equal(ui.focusCalls.length, 1, 'Finishing unrelated requests must not reset the current tool again')
})

test('a successfully loaded empty library list can show the empty state', async t => {
  const ui = await mountSettings(t)
  ui.libraryRequest.resolve([])
  await flush()
  assert.equal(ui.toolProps.librariesReady, true)
  assert.equal(ui.toolProps.librariesError, '')
  assert.deepEqual(ui.toolProps.libs, [])
})
