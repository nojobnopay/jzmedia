import * as Vue from 'vue'

export function nodeText(node) {
  if (node.type === '#comment') return ''
  return node.text + node.children.map(nodeText).join('')
}

// A small host renderer for component behavior tests without a browser or DOM.
// Keep actual Vue templates, child components, events and reactivity in the test.
export function renderHarness(component, props = {}) {
  const element = (type = '', text = '') => ({ type, text, props: {}, children: [], parent: null })
  const remove = node => {
    if (!node.parent) return
    const siblings = node.parent.children
    const index = siblings.indexOf(node)
    if (index >= 0) siblings.splice(index, 1)
    node.parent = null
  }
  const renderer = Vue.createRenderer({
    // Vue passes an SVG namespace as the second createElement argument, not text.
    createElement: type => element(type), createText: text => element('#text', text), createComment: text => element('#comment', text),
    setText(node, text) { node.text = text },
    setElementText(node, text) { node.text = text; node.children = [] },
    patchProp(node, key, _oldValue, value) { node.props[key] = value },
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
  const root = element('root')
  const app = renderer.createApp({ setup: () => () => Vue.h(component, props) })
  app.component('RouterLink', { props: ['to'], setup: (_props, { slots }) => () => Vue.h('a', slots.default?.()) })
  app.mount(root)
  const all = () => {
    const nodes = []
    function visit(node) { nodes.push(node); node.children.forEach(visit) }
    visit(root)
    return nodes
  }
  return {
    app, root, all,
    find: predicate => all().find(predicate),
    text: () => all().filter(node => node.type !== '#comment').map(node => node.text).join(''),
  }
}
export async function flush() {
  for (let i = 0; i < 12; i++) { await Promise.resolve(); await Vue.nextTick() }
}
export function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
