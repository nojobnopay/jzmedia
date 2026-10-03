import test from 'node:test'
import assert from 'node:assert/strict'
import { createRenderer, createSSRApp, h, markRaw, nextTick, ref } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { loadSfc } from './helpers/loadSfc.js'

const button = await loadSfc(new URL('../src/components/JzButton.vue', import.meta.url))
const dialog = await loadSfc(new URL('../src/components/JzDialog.vue', import.meta.url))

test('shared button keeps native form semantics and disables pending actions', async () => {
  const html = await renderToString(createSSRApp({ render: () => h(button, { loading: true, variant: 'primary', name: 'save', 'aria-label': '保存设置' }, () => '保存') }))
  assert.match(html, /type="button"/)
  assert.match(html, /disabled/)
  assert.match(html, /aria-busy="true"/)
  assert.match(html, /name="save"/)
  assert.match(html, /aria-label="保存设置"/)
  const submit = await renderToString(createSSRApp({ render: () => h(button, { type: 'submit' }, () => '提交') }))
  assert.match(submit, /type="submit"/)
  assert.doesNotMatch(submit, /disabled/)
})

test('icon buttons keep the action name on the native button and replace the icon while busy', async () => {
  const render = props => renderToString(createSSRApp({ render: () => h(button, props) }))
  const html = await render({ icon: 'copy', iconOnly: true, 'aria-label': '复制文件', 'aria-pressed': 'true' })
  assert.match(html, /jz-button--icon/)
  assert.match(html, /aria-label="复制文件"/)
  assert.match(html, /aria-pressed="true"/)
  assert.match(html, /aria-hidden="true"/)
  assert.equal((html.match(/<svg/g) || []).length, 1)
  const busy = await render({ icon: 'copy', loading: true, 'aria-label': '复制文件' })
  assert.match(busy, /jz-spinner/)
  assert.match(busy, /aria-busy="true"/)
  assert.match(busy, /disabled/)
  assert.equal((busy.match(/<svg/g) || []).length, 1)
})

function harness(t) {
  const previousDocument = globalThis.document
  const listeners = new Set()
  const doc = { activeElement: null, addEventListener: (_event, listener) => listeners.add(listener), removeEventListener: (_event, listener) => listeners.delete(listener) }
  globalThis.document = doc
  const makeNode = type => markRaw({
    type, children: [], parent: null, props: {}, offsetWidth: 40, isConnected: true,
    focus() { doc.activeElement = this },
    contains(node) { return node === this || this.children.some(child => child.contains(node)) },
    setAttribute(name, value) { this.props[name] = value },
    matches() { return this.props.disabled === true || this.props.disabled === '' },
    querySelectorAll() {
      return this.children.flatMap(child => [child, ...child.querySelectorAll()]).filter(node =>
        ['button', 'input', 'select', 'textarea', 'summary'].includes(node.type) && !node.matches())
    },
  })
  const body = makeNode('body')
  body.style = { overflow: 'auto' }
  doc.body = body
  const renderer = createRenderer({
    createElement: makeNode, createText: () => makeNode('text'), createComment: () => makeNode('comment'),
    setText(node, value) { node.text = value }, setElementText(node, value) { node.text = value },
    patchProp(node, name, _previous, value) { node.props[name] = value },
    insert(node, parent, anchor) { node.parent = parent; const index = anchor ? parent.children.indexOf(anchor) : -1; if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node) },
    remove(node) { node.isConnected = false; if (node.parent) node.parent.children = node.parent.children.filter(child => child !== node) },
    parentNode: node => node.parent, nextSibling(node) { const siblings = node.parent?.children || []; return siblings[siblings.indexOf(node) + 1] || null },
    querySelector: () => body,
  })
  const apps = []
  t.after(() => { apps.reverse().forEach(app => app.unmount()); if (previousDocument) globalThis.document = previousDocument; else delete globalThis.document })
  return {
    doc, listeners, body, makeNode,
    async mount(props, slots = {}) {
      const state = ref(props)
      const app = renderer.createApp({ render: () => h(dialog, state.value, slots) })
      app.mount(makeNode('root')); apps.push(app)
      await nextTick()
      return state
    },
    key(key, shiftKey = false) {
      const event = { key, shiftKey, defaultPrevented: false, preventDefault() { this.defaultPrevented = true }, stopPropagation() {} }
      for (const listener of listeners) listener(event)
      return event
    },
  }
}

test('dialog respects business busy guards, child Escape handlers, and section attributes', async t => {
  const ui = harness(t)
  let closed = 0
  const state = await ui.mount({ title: '保存归属', busy: true, 'aria-describedby': 'explanation', onClose: () => closed++ })
  const mask = ui.body.children.find(node => node.props.class?.includes('jz-dialog-mask'))
  const section = mask.children.find(node => node.type === 'section')
  assert.equal(section.props['aria-describedby'], 'explanation')
  assert.ok(section.props['aria-labelledby'])
  ui.key('Escape')
  mask.props.onClick({ target: mask, currentTarget: mask })
  assert.equal(closed, 0, 'busy dialogs cannot dismiss through keyboard or backdrop')
  state.value = { ...state.value, busy: false }
  await nextTick()
  for (const listener of ui.listeners) listener({ key: 'Escape', defaultPrevented: true })
  assert.equal(closed, 0, 'a child control consumes Escape before the dialog')
  ui.doc.activeElement = ui.body
  ui.key('Escape')
  assert.equal(closed, 1)
})

test('button exposes its native element for menu positioning and focus restoration', async t => {
  const ui = harness(t)
  const handle = ref(null)
  await ui.mount({ title: '菜单容器' }, { default: () => h(button, { ref: handle, icon: 'more', 'aria-label': '更多操作' }) })
  assert.equal(handle.value.el.type, 'button')
  assert.equal(handle.value.el.props['aria-label'], '更多操作')
  handle.value.focus()
  assert.equal(ui.doc.activeElement, handle.value.el)
})

test('nested dialogs keep Tab in the top dialog and return focus to its trigger', async t => {
  const ui = harness(t)
  const external = ui.makeNode('button')
  external.focus()
  const parent = await ui.mount({ title: '父弹窗' }, { default: () => h('button', { id: 'open-child' }, '打开子弹窗') })
  assert.equal(ui.body.style.overflow, 'hidden')
  const parentMask = ui.body.children.find(node => node.props.class?.includes('jz-dialog-mask'))
  const trigger = parentMask.querySelectorAll().find(node => node.props.id === 'open-child')
  trigger.focus()
  const child = await ui.mount({ title: '子弹窗' }, { default: () => h('input') })
  const childMask = ui.body.children.filter(node => node.props.class?.includes('jz-dialog-mask')).at(-1)
  const childControls = childMask.querySelectorAll()
  assert.equal(ui.doc.activeElement, childControls[0])
  childControls.at(-1).focus()
  ui.key('Tab')
  assert.equal(ui.doc.activeElement, childControls[0])
  ui.key('Tab', true)
  assert.equal(ui.doc.activeElement, childControls.at(-1))
  child.value = { ...child.value, open: false }
  await nextTick()
  assert.equal(ui.doc.activeElement, trigger)
  assert.equal(ui.body.style.overflow, 'hidden', 'closing a child keeps the page locked behind its parent')
  parent.value = { ...parent.value, open: false }
  await nextTick()
  assert.equal(ui.doc.activeElement, external)
  assert.equal(ui.body.style.overflow, 'auto', 'closing the last dialog restores the previous page overflow')
  assert.equal(ui.listeners.size, 2, 'hidden dialogs remove their traps; mounted document Escape listeners stay inert')
})
