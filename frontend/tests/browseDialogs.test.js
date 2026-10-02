import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

// Render the teleport's real children inline; focus/position are exercised by
// the isolated browser fixture, while this test drives the real dialog events.
const vue = { ...Vue, vModelText: {}, Teleport: 'div' }
const button = (ui, label) => ui.find(node => node.type === 'button' && nodeText(node).trim() === label)

function listenerEnvironment() {
  const handlers = new Map()
  const descriptors = new Map(['document', 'window'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  const document = {
    activeElement: null,
    addEventListener(type, handler) {
      if (!handlers.has(type)) handlers.set(type, new Set())
      handlers.get(type).add(handler)
    },
    removeEventListener(type, handler) { handlers.get(type)?.delete(handler) },
  }
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { addEventListener() {}, removeEventListener() {} } })
  function restore() {
    for (const [key, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  }
  return {
    document, restore,
    escape() {
      const event = { key: 'Escape', defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true }, stopPropagation() {} }
      for (const handler of handlers.get('keydown') || []) handler(event)
    },
  }
}

test('menu closes and restores its visible trigger in capture before a child action opens a dialog', async t => {
  const { document, restore } = listenerEnvironment()
  const menu = await loadSfc(new URL('../src/components/ActionMenu.vue', import.meta.url), { vue })
  let opener = null, openDuringAction = true
  const ui = renderHarness({ setup: () => () => Vue.h(menu, {}, {
    default: () => Vue.h('button', { onClick() {
      opener = document.activeElement
      openDuringAction = details.open
    } }, '打开弹窗'),
  }) })
  t.after(() => { ui.app.unmount(); restore() })
  const details = ui.find(node => node.type === 'details')
  const summary = ui.find(node => node.type === 'summary')
  details.open = true
  details.querySelector = selector => selector === 'summary' ? summary : null
  summary.focus = () => { document.activeElement = summary }
  const action = button(ui, '打开弹窗')
  document.activeElement = action
  const items = ui.find(node => node.props.class === 'action-menu-items')
  // Native event order is capture → target. The action observes the summary as
  // the dialog's return target, and no bubbling close can steal its new focus.
  items.props.onClickCapture({ target: { closest: () => action } })
  action.props.onClick()
  assert.equal(opener, summary)
  assert.equal(openDuringAction, false)
  assert.equal(items.props.onClick, undefined)
})

test('collection delete dialog supports cancel/Escape, blocks busy closure and duplicate writes, and retains failure for retry', async t => {
  const { escape, restore } = listenerEnvironment()
  const requests = [], navigations = []
  const component = await loadSfc(new URL('../src/views/CollectionDetail.vue', import.meta.url), {
    vue,
    'vue-router': { useRoute: () => ({ params: { id: '301' } }), useRouter: () => ({ push: path => navigations.push(path) }) },
    '../api.js': { posterUrl: path => path, api: (path, options) => {
      if (!options) return Promise.resolve({ id: 301, name: '合成合集', member_count: 0, members: [] })
      const request = { path, options, ...deferred() }
      requests.push(request)
      return request.promise
    } },
    '../useFocusTrap.js': { useFocusTrap: () => ({ isTop: () => true }) },
    '../components/MediaOverview.vue': { default: { setup: () => () => null } },
  })
  const ui = renderHarness(component)
  t.after(() => { ui.app.unmount(); restore() })
  await flush()
  const dialog = () => ui.find(node => node.props.role === 'dialog')
  const open = async () => {
    assert.ok(button(ui, '删除合集'), ui.text())
    button(ui, '删除合集').props.onClick()
    await flush()
  }
  assert.equal(dialog(), undefined)
  await open()
  assert.match(nodeText(dialog()), /合成合集.*影片仍会保留.*不可恢复/)
  escape()
  await flush()
  assert.equal(dialog(), undefined)
  assert.equal(requests.length, 0)
  await open()
  button(ui, '取消').props.onClick()
  await flush()
  assert.equal(dialog(), undefined)

  await open()
  const confirm = button(ui, '确认删除')
  confirm.props.onClick()
  confirm.props.onClick()
  await flush()
  assert.equal(requests.length, 1)
  assert.equal(requests[0].path, '/api/collections/301')
  assert.equal(requests[0].options.method, 'DELETE')
  assert.equal(button(ui, '取消').props.disabled, true)
  assert.equal(button(ui, '删除中…').props.disabled, true)
  escape()
  await flush()
  assert.ok(dialog(), 'busy deletion must retain its dialog')
  requests[0].reject(new Error('服务暂不可用'))
  await flush()
  assert.match(nodeText(ui.find(node => node.props.role === 'alert')), /删除失败.*服务暂不可用/)
  assert.equal(navigations.length, 0)
  button(ui, '确认删除').props.onClick()
  requests[1].resolve({ ok: true })
  await flush()
  assert.equal(dialog(), undefined)
  assert.deepEqual(navigations, ['/collections'])
})
