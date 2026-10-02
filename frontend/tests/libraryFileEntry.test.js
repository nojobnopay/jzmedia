import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'

const flush = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await Vue.nextTick() } }
async function mountTools(t, name, kind) {
  const emitted = [], scrolls = []
  let finishLoading
  const loading = new Promise(resolve => { finishLoading = resolve })
  const previousWindow = globalThis.window
  globalThis.window = { scrollY: 420, scrollTo: value => scrolls.push(value.top) }
  const child = { props: ['preselectIds'], setup(props, { expose }) {
    expose({ ensure: async () => {}, count: () => 0, selectedIds: () => props.preselectIds || [] })
    return () => Vue.h('div')
  } }
  const overrides = {
    vue: { ...Vue, vShow: {}, vModelCheckbox: {}, vModelText: {} },
    '../api.js': { api: () => loading },
    '../useLibraryScan.js': { useLibraryScan: () => ({ running: Vue.ref(false), blocked: Vue.ref(false),
      message: Vue.ref(''), stateText: Vue.ref('随时可用'), start: async () => {}, cancel: async () => {} }) },
  }
  for (const component of ['OrganizePanel', 'LibraryMaintenancePanel', 'RestorePanel', 'TvOrganizePanel', 'TvBindingsDialog', 'TvMaintenancePanel']) overrides['./' + component + '.vue'] = { default: child }
  const component = await loadSfc(new URL('../src/components/' + name + '.vue', import.meta.url), overrides)
  const props = Vue.reactive({ tab: { id: 7, media_id: 2, kind, enabled: true }, active: false, request: null,
    onOpenFiles: value => emitted.push(value) })
  const element = () => ({ children: [], parent: null })
  const remove = node => { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = null }
  const renderer = Vue.createRenderer({
    createElement: element, createText: element, createComment: element, setText() {}, setElementText() {}, patchProp() {},
    insert(node, parent, anchor) { if (node.parent) remove(node); node.parent = parent; const index = parent.children.indexOf(anchor); if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node) },
    remove, parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  })
  const app = renderer.createApp({ setup: () => () => Vue.h(component, props) })
  app.mount(element())
  t.after(() => { app.unmount(); globalThis.window = previousWindow })
  const text = node => typeof node?.children === 'string' ? node.children : Array.isArray(node?.children) ? node.children.map(text).join('') : ''
  const nodes = () => {
    const out = []
    const walk = node => { if (!node || typeof node !== 'object') return; out.push(node); if (node.component) walk(node.component.subTree); if (Array.isArray(node.children)) node.children.forEach(walk) }
    walk(app._instance.subTree)
    return out
  }
  return { props, emitted, scrolls, finishLoading, button: label => nodes().find(node => node.type === 'button' && text(node) === label) }
}

for (const [name, kind, view, step, ids] of [
  ['MovieLibraryTools', 'movie', 'restore', 'pending', [11, 12]],
  ['TvLibraryTools', 'tv', 'maintenance', 'match', []],
]) {
  test(name + ' exposes an explicit file shortcut and restores its original tool state after loading', async t => {
    const ui = await mountTools(t, name, kind)
    assert.equal(ui.button('文件管理'), undefined, 'File navigation is not a tab')
    assert.ok(ui.button('管理文件'))
    assert.equal(ui.button('管理文件').props['aria-pressed'], undefined)
    ui.button('资料维护').props.onClick()
    await flush()
    ui.button('管理文件').props.onClick()
    assert.equal(ui.emitted[0].view, 'maintenance')
    assert.equal(ui.emitted[0].scrollTop, 420)
    ui.props.active = true
    ui.props.request = { sec: 'sec-libtools', restore: { view, step, ids, scrollTop: 745 } }
    await flush()
    assert.deepEqual(ui.scrolls, [], 'Do not restore scroll against a still-empty cold view')
    ui.finishLoading({ items: [] })
    await flush()
    assert.deepEqual(ui.scrolls, [745])
    ui.button('管理文件').props.onClick()
    const restored = ui.emitted.at(-1)
    assert.equal(restored.view, view)
    assert.equal(restored.step, step)
    assert.deepEqual(restored.ids, ids)
  })
}
