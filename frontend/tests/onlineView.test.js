import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred } from './helpers/renderHarness.js'
import * as catalog from '../src/onlineCatalog.js'

const SOURCE_A = 'src_' + '1'.repeat(32), SOURCE_B = 'src_' + '2'.repeat(32)
const sources = { enabled: true, items: [{ id: SOURCE_A, name: '来源 A', provider: 'maccms' }, { id: SOURCE_B, name: '来源 B', provider: 'maccms' }] }
const rows = { items: [{ external_id: 'movie-1', title: '合成电影', kind: 'movie', year: '2026' }], page: 1, page_count: 1, total: 1 }
const vue = { ...Vue, vModelText: {}, vModelSelect: {}, vModelCheckbox: {}, vShow: {} }
async function setup(t, request) {
  let cleanup = () => {}
  const component = await loadSfc(new URL('../src/views/Online.vue', import.meta.url), {
    vue,
    '../onlineCatalog.js': { ...catalog, createOnlineCatalogClient: () => catalog.createOnlineCatalogClient({ request }) },
    '../viewing.js': { isChildren: Vue.ref(false), registerViewingPlaybackCleanup: callback => { cleanup = callback; return () => {} } },
    '../components/OnlinePlayerModal.vue': { default: { setup: () => () => Vue.h('div', { 'data-stub-player': true }) } },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  await flush()
  return { ui, cleanup: () => cleanup() }
}
function search(ui, query) {
  ui.find(node => node.props.id === 'online-query').props['onUpdate:modelValue'](query)
  ui.find(node => node.props['data-online-search'] !== undefined).props.onSubmit({ preventDefault() {} })
}

test('changing a source cancels an old search without showing its late error on the new source', async t => {
  const pending = deferred(), calls = []
  const { ui } = await setup(t, async (path, options) => {
    calls.push({ path, options })
    return path.endsWith('/sources') ? sources : pending.promise
  })
  search(ui, '旧片名'); await flush()
  const select = ui.find(node => node.props['data-online-source'] !== undefined)
  select.props['onUpdate:modelValue'](SOURCE_B); select.props.onChange()
  pending.reject(new Error('old source failure'))
  await flush()
  assert.equal(calls.at(-1).options.signal.aborted, true)
  assert.equal(ui.text().includes('搜索失败'), false)
  assert.equal(ui.text().includes('查找想看的内容'), true)
})

test('returning from an unfinished detail keeps search rows and discards its late response', async t => {
  const pending = deferred()
  const { ui } = await setup(t, path => Promise.resolve(path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : pending.promise))
  search(ui, '合成'); await flush()
  ui.find(node => node.props['data-online-result'] === 'movie-1').props.onClick()
  await flush()
  assert.equal(ui.text().includes('正在加载详情'), true)
  ui.find(node => node.type === 'button' && node.children.some(child => child.text === '返回搜索结果')).props.onClick()
  pending.resolve({ item: { title: '过时详情' }, lines: [] })
  await flush()
  assert.equal(ui.text().includes('合成电影'), true)
  assert.equal(ui.text().includes('过时详情'), false)
  assert.equal(ui.find(node => node.props['data-online-detail'] !== undefined), undefined)
})

test('permission cleanup discards the pending detail and previously visible results', async t => {
  const pending = deferred()
  const { ui, cleanup } = await setup(t, path => Promise.resolve(path.endsWith('/sources') ? sources : path.includes('/search?') ? rows : pending.promise))
  search(ui, '合成'); await flush()
  ui.find(node => node.props['data-online-result'] === 'movie-1').props.onClick()
  await flush(); cleanup()
  pending.resolve({ item: { title: '受保护详情' }, lines: [] })
  await flush()
  assert.equal(ui.text().includes('受保护详情'), false)
  assert.equal(ui.text().includes('合成电影'), false)
  assert.equal(ui.text().includes('查找想看的内容'), true)
})
