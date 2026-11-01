import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'

const flush = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await Vue.nextTick() } }
function gate() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

async function mountCleanup(t, { props = {}, respond, activeTraps = Vue.ref(0) } = {}) {
  const requests = [], signals = [], events = [], emitted = []
  let focuses = 0, mounted = true
  const previousWindow = globalThis.window
  globalThis.window = { dispatchEvent: event => events.push(event.type) }
  const button = { props: ['loading', 'disabled'], emits: ['click'], setup(props, { slots, emit, expose }) {
    expose({ focus() { focuses++ } })
    return () => Vue.h('button', { disabled: props.disabled || props.loading, onClick: () => emit('click') }, slots.default?.())
  } }
  // Deliberately emit close while busy too: the owner must guard stale close events.
  const dialog = { props: ['busy', 'title'], emits: ['close'], setup(props, { slots, emit }) {
    return () => Vue.h('section', { role: 'dialog', 'aria-label': props.title }, [
      Vue.h('button', { onClick: () => emit('close') }, '关闭废弃缩略图清理'),
      slots.default?.(), Vue.h('footer', slots.footer?.()),
    ])
  } }
  const component = await loadSfc(new URL('../src/components/ObsoletePreviewCleanup.vue', import.meta.url), {
    '../api.js': { api: async (path, options) => {
      const body = JSON.parse(options.body)
      requests.push({ path, ...body }); signals.push(options.signal)
      return respond(body, requests.length)
    } },
    './JzButton.vue': { default: button }, './JzDialog.vue': { default: dialog },
    '../useFocusTrap.js': { activeFocusTraps: activeTraps },
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
  const root = node('root')
  const descendants = target => [target, ...target.children.flatMap(descendants)]
  const textOf = target => target.tag === '#comment' ? '' : target.text + target.children.map(textOf).join('')
  const dialogNode = () => descendants(root).find(item => item.props.role === 'dialog')
  const app = renderer.createApp(component, {
    ...props,
    onChecked: result => emitted.push({ name: 'checked', result }),
    onCleaned: result => emitted.push({ name: 'cleaned', result }),
    onBusyChange: value => emitted.push({ name: 'busy', value }),
    onGeneratePreviews: () => emitted.push({ name: 'generate', afterClose: !dialogNode() }),
  })
  app.mount(root)
  const unmount = () => { if (mounted) { app.unmount(); mounted = false } }
  t.after(() => { unmount(); globalThis.window = previousWindow })
  const findButton = label => descendants(root).find(item => item.tag === 'button' && textOf(item) === label)
  const click = async (label, { disabled = false } = {}) => {
    const target = findButton(label)
    assert.ok(Boolean(target), label)
    if (!disabled) assert.equal(Boolean(target.props.disabled), false, label + ' is available')
    target.props.onClick()
    await flush()
  }
  return { requests, signals, events, emitted, click, findButton, dialogNode, unmount,
    text: () => textOf(root), focuses: () => focuses }
}

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

test('dialog checks on demand, states the global scope and guards close and duplicates while busy', async t => {
  const check = gate(), clean = gate()
  const h = await mountCleanup(t, { props: { dialog: true, label: '检查旧缩略图', contextName: '演示剧集库' },
    respond: body => body.dry_run ? check.promise : clean.promise })
  assert.equal(Boolean(h.dialogNode()), false)
  assert.equal(h.requests.length, 0)
  await h.click('检查旧缩略图')
  assert.equal(Boolean(h.dialogNode()), true)
  assert.match(h.text(), /全服务的全部媒体库/)
  assert.match(h.text(), /演示剧集库/)
  assert.match(h.text(), /正在检查废弃缩略图/)
  await h.click('检查旧缩略图', { disabled: true })
  await h.click('关闭废弃缩略图清理')
  assert.equal(h.requests.length, 1)
  assert.equal(Boolean(h.dialogNode()), true)
  check.resolve({ candidates: 2, candidate_bytes: 1024, reasons: { replaced: 2 }, skipped: {} })
  await flush()
  await h.click('确认清理废弃缩略图')
  await h.click('确认清理废弃缩略图', { disabled: true })
  await h.click('关闭废弃缩略图清理')
  assert.equal(Boolean(h.dialogNode()), true)
  assert.deepEqual(h.requests.map(request => request.dry_run), [true, false])
  assert.ok(h.requests.every(request => !Object.hasOwn(request, 'library_id')), 'the dialog never suggests a library-scoped deletion')
  clean.resolve({ removed: 1, freed: 512, failed: 0 })
  await flush()
  assert.match(h.text(), /已清理 1 个目录/)
  assert.equal(Boolean(h.findButton('确认清理废弃缩略图')), false, 'an old candidate count is not treated as remaining work')
  assert.deepEqual(h.emitted.filter(event => event.name === 'busy').map(event => event.value), [true, false, true, false])
  assert.deepEqual(h.emitted.filter(event => ['checked', 'cleaned'].includes(event.name)).map(event => event.name), ['checked', 'cleaned'])
  const focusBeforeClose = h.focuses()
  await h.click('关闭')
  assert.equal(Boolean(h.dialogNode()), false)
  assert.ok(h.focuses() > focusBeforeClose, 'closing restores focus to the check trigger')
})

test('failed checks can retry; protected-only results explain next actions without claiming a cleanup', async t => {
  const h = await mountCleanup(t, { props: { dialog: true }, respond: (_body, call) => {
    if (call === 1) throw new Error('模拟检查暂不可用')
    return { candidates: 0, candidate_bytes: 0, reasons: {}, skipped: { in_use: 1, offline: 2, protected: 3, unknown: 6 },
      skipped_details: { replacement_missing: 1, scan_required: 2, recently_used: 3, in_use: 1, source_unavailable: 4, unknown: 1 } }
  } })
  await h.click('清理废弃缩略图')
  assert.match(h.text(), /检查失败：模拟检查暂不可用/)
  await h.click('重新检查')
  assert.match(h.text(), /本次检查没有可安全清理/)
  assert.match(h.text(), /尚无完整替代预览 1 项/)
  assert.match(h.text(), /先完成资料核对和目录整理/)
  assert.match(h.text(), /扫描证据不足 2 项/)
  assert.match(h.text(), /近期访问或写入 3 项/)
  assert.match(h.text(), /片源状态无法确认 4 项/)
  assert.doesNotMatch(h.text(), /已清理/)
  assert.equal(Boolean(h.findButton('确认清理废弃缩略图')), false)
  assert.equal(h.emitted.some(event => event.name === 'cleaned'), false)
  await h.click('生成本库新版缩略图')
  assert.equal(Boolean(h.dialogNode()), false)
  assert.deepEqual(h.emitted.filter(event => event.name === 'generate'), [{ name: 'generate', afterClose: true }])
  assert.deepEqual(h.requests.map(request => request.dry_run), [true, true], 'generation is delegated after closing without any extra write')
  assert.deepEqual(h.events, [])
})

test('failed cleanup stays reviewable and can recheck before retrying the actual deletion', async t => {
  const h = await mountCleanup(t, { props: { dialog: true }, respond: (body, call) => {
    if (call === 2) throw new Error('模拟目录权限不足')
    return body.dry_run ? { candidates: 1, candidate_bytes: 1024, reasons: { deleted: 1 }, skipped: {} }
      : { removed: 1, freed: 1024, failed: 0, skipped: { protected: 2 }, skipped_details: { recently_used: 2 } }
  } })
  await h.click('清理废弃缩略图')
  await h.click('确认清理废弃缩略图')
  assert.equal(Boolean(h.dialogNode()), true)
  assert.match(h.text(), /清理失败：模拟目录权限不足/)
  assert.equal(h.emitted.some(event => event.name === 'cleaned'), false)
  await h.click('重新检查')
  assert.doesNotMatch(h.text(), /清理失败/)
  await h.click('确认清理废弃缩略图')
  assert.deepEqual(h.requests.map(request => request.dry_run), [true, false, true, false])
  assert.match(h.text(), /已清理 1 个目录/)
  assert.match(h.text(), /近期访问或写入 2 项/)
  assert.equal(h.emitted.filter(event => event.name === 'cleaned').length, 1)
})

test('inline cancellation restores focus and unmounted pending checks cannot publish late results', async t => {
  const result = gate()
  const h = await mountCleanup(t, { respond: (_body, call) => call === 1
    ? { candidates: 1, candidate_bytes: 1, skipped: {} } : result.promise })
  await h.click('清理废弃缩略图')
  await h.click('取消')
  assert.equal(Boolean(h.findButton('确认清理废弃缩略图')), false)
  assert.ok(h.focuses() > 0)
  await h.click('清理废弃缩略图')
  const beforeUnmount = h.emitted.filter(event => event.name === 'checked').length
  h.unmount()
  assert.equal(h.signals.at(-1).aborted, true)
  result.resolve({ candidates: 100, candidate_bytes: 9999, skipped: {} })
  await flush()
  assert.equal(h.emitted.filter(event => event.name === 'checked').length, beforeUnmount)
  assert.equal(h.emitted.some(event => event.name === 'cleaned'), false)
  assert.equal(h.emitted.at(-1).value, false, 'unmounting clears a pending busy state')
})

test('a cleanup dialog waits for another focus trap and does not claim deletion when revalidation keeps all candidates', async t => {
  const activeTraps = Vue.ref(1)
  const h = await mountCleanup(t, { props: { dialog: true }, activeTraps,
    respond: body => body.dry_run ? { candidates: 1, candidate_bytes: 10, reasons: {}, skipped: {} }
      : { removed: 0, freed: 0, failed: 0, skipped: { in_use: 1 }, skipped_details: { in_use: 1 } } })
  await h.click('清理废弃缩略图')
  assert.equal(Boolean(h.dialogNode()), false)
  assert.equal(h.requests.length, 0)
  activeTraps.value = 0
  await h.click('清理废弃缩略图')
  await h.click('确认清理废弃缩略图')
  assert.match(h.text(), /本次未清理目录/)
  assert.match(h.text(), /使用或排队 1 项/)
  assert.doesNotMatch(h.text(), /已清理/)
})
