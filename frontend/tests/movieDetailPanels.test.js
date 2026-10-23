import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

const vue = { ...Vue, vModelText: {}, vModelSelect: {} }
const button = (ui, label) => ui.find(node => node.type === 'button' && nodeText(node).trim() === label)

async function collectionsPanel(t) {
  const requests = [], events = []
  const component = await loadSfc(new URL('../src/components/MovieCollectionsPanel.vue', import.meta.url), {
    vue, '../api.js': { api(path, options = {}) {
      const request = { path, options }
      requests.push(request)
      if (!options.method) return Promise.resolve({ items: [{ id: 31, name: '模拟合集', member_count: 2 }] })
      Object.assign(request, deferred())
      return request.promise
    } }, '../libraries.js': { listLibs: () => [{ id: 7, media_library_id: 2 }], currentMediaId: () => 1 },
  })
  const ui = renderHarness(component, { movieId: 101, libraryId: 7, onChanged: () => events.push('changed') })
  t.after(() => ui.app.unmount())
  await flush()
  return { ...ui, requests, events }
}

test('movie collection membership stays on the original movie and stops following after its panel unmounts', async t => {
  const ui = await collectionsPanel(t)
  ui.find(node => node.type === 'select').props['onUpdate:modelValue'](31)
  await flush()
  const join = button(ui, '加入').props.onClick
  join(); join()
  const write = ui.requests.at(-1)
  assert.equal(ui.requests.length, 2)
  assert.equal(write.path, '/api/collections/31/members')
  assert.deepEqual(JSON.parse(write.options.body), { movie_ids: [101] })
  assert.equal(button(ui, '创建').props.disabled, true)
  ui.app.unmount()
  write.resolve({ ok: true })
  await flush()
  assert.equal(ui.requests.length, 2)
  assert.deepEqual(ui.events, [])
})

test('creating a movie collection snapshots its name and library, blocks repeats and refreshes after a normal save', async t => {
  const ui = await collectionsPanel(t)
  ui.find(node => node.type === 'input').props['onUpdate:modelValue'](' 合集甲 ')
  await flush()
  const create = button(ui, '创建').props.onClick
  create(); create()
  const write = ui.requests.at(-1)
  assert.equal(ui.requests.length, 2)
  assert.deepEqual(JSON.parse(write.options.body), { name: '合集甲', member_ids: [101], media_library_id: 2 })
  write.resolve({ ok: true })
  await flush()
  assert.equal(ui.requests.at(-1).path, '/api/collections?media_library=2')
  assert.deepEqual(ui.events, ['changed'])
  assert.match(ui.text(), /已创建/)
  assert.equal(button(ui, '加入').props.disabled, true)
  assert.equal(ui.find(node => node.type === 'input').props.disabled, false)
})

async function uploadPanel(t, protection = async () => true) {
  const uploads = [], events = [], protectionCalls = []
  const component = await loadSfc(new URL('../src/components/MovieUploadPanel.vue', import.meta.url), {
    vue, '../api.js': { apiUpload(path, file, options) {
      const upload = { path, file, options, ...deferred(), aborted: 0 }
      uploads.push(upload)
      return { promise: upload.promise, abort: () => { upload.aborted++ } }
    } }, '../viewing.js': { requestProtection(purpose) {
      protectionCalls.push(purpose)
      return protection()
    } },
  })
  const ui = renderHarness(component, { movieId: 101, onUploaded: () => events.push('uploaded') })
  t.after(() => ui.app.unmount())
  ui.find(node => node.type === 'input').files = [{ name: '模拟字幕.srt' }]
  return { ...ui, uploads, events, protectionCalls }
}

test('movie upload controls react to an active upload and prevent duplicate sends', async t => {
  const ui = await uploadPanel(t)
  const upload = button(ui, '上传').props.onClick
  upload(); upload()
  await flush()
  assert.equal(ui.uploads.length, 1)
  assert.equal(ui.uploads[0].path, '/api/movies/101/upload')
  assert.equal(button(ui, '上传').props.disabled, true)
  assert.equal(ui.find(node => node.type === 'input').props.disabled, true)
  button(ui, '取消').props.onClick()
  assert.equal(ui.uploads[0].aborted, 1)
  ui.uploads[0].resolve({ status: 'stored' })
  await flush()
  assert.equal(button(ui, '上传').props.disabled, false)
  assert.deepEqual(ui.events, ['uploaded'])
  assert.match(ui.text(), /已上传 模拟字幕.srt/)
})

test('upload callbacks after unmount cannot start scanning timers or refresh a different movie', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
  const ui = await uploadPanel(t)
  button(ui, '上传').props.onClick()
  await flush()
  const upload = ui.uploads[0]
  upload.options.onUploaded()
  ui.app.unmount()
  upload.options.onUploaded()
  upload.options.onProgress(100)
  upload.resolve({ status: 'scanned' })
  await flush()
  t.mock.timers.tick(60000)
  await flush()
  assert.deepEqual(ui.events, [])
  assert.equal(upload.aborted, 0, 'Leaving stops UI tracking without claiming to cancel a submitted upload')
})

test('an unmounted movie upload panel rejects late authorization and cannot open a new password prompt', async t => {
  const authorization = deferred()
  const ui = await uploadPanel(t, () => authorization.promise)
  button(ui, '上传').props.onClick()
  await flush()
  const upload = ui.uploads[0]
  assert.equal(upload.options.canStart(), true)
  const pendingAuthorization = upload.options.authorize()
  assert.deepEqual(ui.protectionCalls, ['unlock'])
  ui.app.unmount()
  assert.equal(upload.options.canStart(), false)
  assert.equal(await upload.options.authorize(), false)
  assert.deepEqual(ui.protectionCalls, ['unlock'], 'Leaving the panel prevents another global password prompt')
  authorization.resolve(true)
  assert.equal(await pendingAuthorization, false, 'Authorization completing after the panel unmounts must not start its file transfer')
  upload.resolve({ status: 'stored' })
  await flush()
  assert.deepEqual(ui.events, [])
})
