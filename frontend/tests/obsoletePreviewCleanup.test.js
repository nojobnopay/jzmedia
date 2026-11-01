import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'

test('obsolete preview cleanup checks before confirmation and reports the actual result', async t => {
  const requests = [], events = []
  const previousWindow = globalThis.window
  globalThis.window = { dispatchEvent: event => events.push(event.type) }
  t.after(() => { globalThis.window = previousWindow })
  const button = { props: ['loading', 'disabled'], emits: ['click'], setup(props, { slots, emit, expose }) {
    expose({ focus() {} })
    return () => Vue.h('button', { disabled: props.disabled || props.loading, onClick: () => emit('click') }, slots.default?.())
  } }
  const component = await loadSfc(new URL('../src/components/ObsoletePreviewCleanup.vue', import.meta.url), {
    '../api.js': { api: async (path, options) => {
      const body = JSON.parse(options.body)
      requests.push({ path, ...body })
      return body.dry_run
        ? { candidates: 2, candidate_bytes: 1024, reasons: { replaced: 2 }, skipped: { offline: 3 } }
        : { removed: 1, freed: 512, failed: 1 }
    } },
    './JzButton.vue': { default: button },
  })
  const node = (tag, text = '') => ({ tag, text, children: [], props: {}, parent: null })
  const remove = child => {
    if (!child.parent) return
    const siblings = child.parent.children, index = siblings.indexOf(child)
    if (index >= 0) siblings.splice(index, 1)
    child.parent = null
  }
  const renderer = Vue.createRenderer({
    createElement: tag => node(tag), createText: text => node('#text', text), createComment: text => node('#comment', text),
    setText: (target, text) => { target.text = text }, setElementText: (target, text) => { target.text = text; target.children = [] },
    patchProp: (target, key, _old, value) => { target.props[key] = value },
    insert(child, parent, anchor) { remove(child); child.parent = parent; const index = parent.children.indexOf(anchor); if (index < 0) parent.children.push(child); else parent.children.splice(index, 0, child) },
    remove, parentNode: target => target.parent, nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] || null,
  })
  const root = node('root'), app = renderer.createApp(component)
  app.mount(root)
  t.after(() => app.unmount())
  const descendants = target => [target, ...target.children.flatMap(descendants)]
  const textOf = target => target.tag === '#comment' ? '' : target.text + target.children.map(textOf).join('')
  const click = async label => {
    const target = descendants(root).find(item => item.tag === 'button' && textOf(item) === label)
    assert.ok(target, label)
    target.props.onClick()
    for (let i = 0; i < 5; i++) { await Promise.resolve(); await Vue.nextTick() }
  }
  await click('清理废弃缩略图')
  assert.deepEqual(requests.map(request => request.dry_run), [true])
  assert.match(textOf(root), /旧片源 2/)
  assert.match(textOf(root), /离线或扫描未完成 3/)
  await click('确认清理废弃缩略图')
  assert.deepEqual(requests.map(request => request.dry_run), [true, false])
  assert.ok(requests.every(request => request.path === '/api/stream/previews/cache/clean-obsolete'))
  assert.match(textOf(root), /已清理 1 个目录.*失败 1 个/)
  assert.deepEqual(events, ['jzmedia:data-usage-changed'])
})
