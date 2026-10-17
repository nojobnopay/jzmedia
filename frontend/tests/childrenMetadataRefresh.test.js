import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'

const KEY = 'jzmedia.childrenRefreshJob'
const libraries = [
  { id: 7, name: '电影库', kind: 'movie', media_name: '家庭媒体' },
  { id: 9, name: '剧集库', kind: 'tv', media_name: '儿童媒体' },
]
const flush = async () => {
  for (let i = 0; i < 10; i++) { await Promise.resolve(); await Vue.nextTick() }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const job = (libraryId, extra = {}) => ({
  job_id: 'refresh-job', library_id: libraryId, state: 'done', total: 1, done: 1,
  worker_finished: true, ...extra,
})

async function mountRefresh(t, { props: initialProps = {}, savedJob = '', respond } = {}) {
  const storageDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  const storage = new Map(savedJob ? [[KEY, savedJob]] : [])
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    value: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: key => storage.delete(key),
    },
  })
  const requests = [], retries = []
  const props = Vue.reactive({ active: true, libs: libraries, librariesReady: true, librariesError: '', ...initialProps })
  const selectModel = {
    beforeMount(element, binding, vnode) {
      element.modelValue = binding.value
      element.updateModel = vnode.props['onUpdate:modelValue']
    },
    beforeUpdate(element, binding, vnode) {
      element.modelValue = binding.value
      element.updateModel = vnode.props['onUpdate:modelValue']
    },
  }
  const button = {
    props: ['loading', 'disabled'],
    emits: ['click'],
    setup(buttonProps, { slots, emit }) {
      return () => Vue.h('button', {
        disabled: buttonProps.disabled || buttonProps.loading,
        onClick: () => { if (!buttonProps.disabled && !buttonProps.loading) emit('click') },
      }, slots.default?.())
    },
  }
  const component = await loadSfc(new URL('../src/components/ChildrenMetadataRefresh.vue', import.meta.url), {
    vue: { ...Vue, vModelSelect: selectModel },
    '../api.js': { api: (path, options = {}) => {
      requests.push({ path, ...options })
      assert.ok(respond, 'Unexpected request: ' + path)
      return respond(path, options)
    } },
    './JzButton.vue': { default: button },
  })
  const element = (tag, text = '') => ({ tag, text, children: [], props: {}, parent: null })
  const remove = node => {
    if (!node.parent) return
    const siblings = node.parent.children
    const index = siblings.indexOf(node)
    if (index >= 0) siblings.splice(index, 1)
    node.parent = null
  }
  const renderer = Vue.createRenderer({
    createElement: tag => element(tag),
    createText: text => element('#text', text),
    createComment: text => element('#comment', text),
    setText: (node, text) => { node.text = text },
    setElementText: (node, text) => { node.text = text; node.children = [] },
    patchProp: (node, name, _previous, value) => { node.props[name] = value },
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
  const app = renderer.createApp({
    setup: () => () => Vue.h(component, { ...props, onRetryLibraries: () => retries.push(true) }),
  })
  app.mount(root)
  let mounted = true
  const unmount = () => {
    if (!mounted) return
    app.unmount()
    mounted = false
  }
  t.after(() => {
    unmount()
    if (storageDescriptor) Object.defineProperty(globalThis, 'sessionStorage', storageDescriptor)
    else delete globalThis.sessionStorage
  })
  const descendants = node => [node, ...node.children.flatMap(descendants)]
  const textOf = node => node.tag === '#comment' ? '' : node.text + node.children.map(textOf).join('')
  const findButton = label => descendants(root).find(node => node.tag === 'button' && textOf(node) === label)
  await flush()
  return {
    props, requests, retries, storage, unmount,
    get select() { return descendants(root).find(node => node.tag === 'select') },
    get text() { return textOf(root) },
    button: findButton,
    async click(label) {
      const target = findButton(label)
      assert.ok(target, 'Missing button: ' + label)
      assert.equal(Boolean(target.props.disabled), false, 'Button must be enabled: ' + label)
      target.props.onClick()
      await flush()
    },
    async choose(value) {
      const target = descendants(root).find(node => node.tag === 'select')
      assert.equal(Boolean(target.props.disabled), false, 'Scope selector must be enabled')
      target.updateModel(value)
      await flush()
    },
  }
}

test('scope starts at all libraries and submits independent all or single-library requests', async t => {
  let selectedScope = null
  const ui = await mountRefresh(t, { respond: (path, options) => {
    if (options.method === 'POST') selectedScope = JSON.parse(options.body).library_id
    return Promise.resolve(job(selectedScope))
  } })
  assert.equal(ui.select.modelValue, '')
  assert.match(ui.text, /家庭媒体 \/ 电影库 · 电影/)
  assert.match(ui.text, /儿童媒体 \/ 剧集库 · 剧集/)
  await ui.click('补全儿童筛选资料')
  assert.deepEqual(JSON.parse(ui.requests[0].body), { library_id: null })
  await ui.choose('9')
  await ui.click('补全儿童筛选资料')
  const starts = ui.requests.filter(request => request.method === 'POST')
  assert.deepEqual(starts.map(request => JSON.parse(request.body)), [{ library_id: null }, { library_id: 9 }])
  assert.equal(ui.storage.get(KEY), 'refresh-job')
  assert.deepEqual(ui.props.libs.map(library => library.id), [7, 9])
})

test('loading, failed and empty library lists block submission; failure offers library retry', async t => {
  const ui = await mountRefresh(t, { props: { libs: [], librariesReady: false } })
  assert.equal(ui.select.props.disabled, true)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, true)
  assert.match(ui.text, /正在加载视频库/)
  assert.doesNotMatch(ui.text, /还没有视频库/)
  ui.props.librariesError = '连接暂不可用'
  await flush()
  assert.match(ui.text, /视频库加载失败：连接暂不可用/)
  await ui.click('重新加载视频库')
  assert.equal(ui.retries.length, 1)
  ui.props.librariesError = ''
  ui.props.librariesReady = true
  await flush()
  assert.match(ui.text, /还没有视频库/)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, true)
  ui.props.libs = libraries
  await flush()
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, false)
  assert.deepEqual(ui.requests, [])
})

test('removing the selected library preserves its selection and blocks accidental all-library submission', async t => {
  const ui = await mountRefresh(t)
  await ui.choose('9')
  ui.props.libs = [libraries[0]]
  await flush()
  assert.equal(ui.select.modelValue, '9')
  assert.match(ui.text, /所选视频库已不存在，请重新选择补全范围/)
  assert.equal(ui.select.props['aria-invalid'], true)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, true)
  assert.deepEqual(ui.requests, [])
  await ui.choose('7')
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, false)
})

test('restored server task supplies its own library scope and locks it while running', async t => {
  const pending = deferred()
  const ui = await mountRefresh(t, { savedJob: 'old-task', respond: () => pending.promise })
  assert.equal(ui.select.props.disabled, true)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, true)
  assert.equal(ui.requests[0].path, '/api/viewing/refresh/old-task')
  pending.resolve(job(9, { job_id: 'old-task', state: 'running', done: 0, worker_finished: false }))
  await flush()
  assert.equal(ui.select.modelValue, '9')
  assert.equal(ui.select.props.disabled, true)
  assert.match(ui.text, /任务范围：儿童媒体 \/ 剧集库 · 剧集/)
  assert.ok(ui.button('取消补全'))
})

test('failed task restoration blocks new work until a successful status retry', async t => {
  let calls = 0
  const ui = await mountRefresh(t, { savedJob: 'old-task', respond: () => {
    calls++
    return calls === 1 ? Promise.reject(new Error('网络暂不可用')) : Promise.resolve(job(7))
  } })
  assert.match(ui.text, /读取任务失败：网络暂不可用/)
  assert.equal(ui.select.props.disabled, true)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, true)
  assert.equal(ui.storage.get(KEY), 'old-task')
  await ui.click('刷新状态')
  assert.equal(ui.select.modelValue, '7')
  assert.equal(ui.select.props.disabled, false)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, false)
  assert.equal(ui.requests.length, 2)
  assert.ok(ui.requests.every(request => !request.method))
})

test('expired stored tasks clear their reference and allow a fresh all-library task', async t => {
  const ui = await mountRefresh(t, { savedJob: 'expired', respond: () => Promise.reject(Object.assign(new Error('任务不存在'), { status: 404 })) })
  assert.equal(ui.storage.has(KEY), false)
  assert.equal(ui.select.modelValue, '')
  assert.equal(ui.select.props.disabled, false)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, false)
  assert.equal(ui.button('刷新状态'), undefined)
})

test('cancelled tasks remain locked until their worker finishes', async t => {
  let current = job(9, { state: 'running', done: 0, worker_finished: false })
  const ui = await mountRefresh(t, { savedJob: 'refresh-job', respond: (path, options) => {
    if (options.method === 'POST' && path.endsWith('/cancel')) current = job(9, { state: 'cancelled', done: 0, worker_finished: false })
    return Promise.resolve(current)
  } })
  await ui.click('取消补全')
  assert.equal(ui.select.props.disabled, true)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, true)
  assert.match(ui.text, /正在取消/)
  current = { ...current, worker_finished: true }
  await ui.click('刷新状态')
  assert.equal(ui.select.props.disabled, false)
  assert.equal(ui.button('补全儿童筛选资料').props.disabled, false)
  assert.match(ui.text, /已取消/)
})

test('terminal task keeps its original scope label when choosing the next task range', async t => {
  const ui = await mountRefresh(t, { savedJob: 'refresh-job', respond: () => Promise.resolve(job(9, { failed_count: 1 })) })
  assert.ok(ui.button('重试补全儿童筛选资料'))
  await ui.choose('7')
  assert.match(ui.text, /任务范围：儿童媒体 \/ 剧集库 · 剧集/)
  assert.ok(ui.button('补全儿童筛选资料'))
  assert.equal(ui.button('重试补全儿童筛选资料'), undefined)
  ui.props.active = false
  await flush()
  ui.props.active = true
  await flush()
  assert.equal(ui.select.modelValue, '7', 'Reopening the section must keep the next task selection')
})

test('responses arriving after the section becomes inactive cannot restore stale task state', async t => {
  const pending = deferred()
  let calls = 0
  const ui = await mountRefresh(t, { savedJob: 'refresh-job', respond: () => {
    calls++
    return calls === 1 ? pending.promise : Promise.resolve(job(7))
  } })
  ui.props.active = false
  await flush()
  assert.equal(ui.requests[0].signal.aborted, true)
  pending.resolve(job(9, { state: 'running', worker_finished: false }))
  await flush()
  assert.equal(ui.select.modelValue, '')
  assert.doesNotMatch(ui.text, /任务范围：/)
  ui.props.active = true
  await flush()
  assert.equal(ui.select.modelValue, '7')
  assert.match(ui.text, /任务范围：家庭媒体 \/ 电影库 · 电影/)
})

test('a late start response after unmount preserves a newer saved task without polling the old task', async t => {
  const pending = deferred()
  const ui = await mountRefresh(t, { respond: () => pending.promise })
  await ui.choose('9')
  await ui.click('补全儿童筛选资料')
  assert.equal(ui.requests.length, 1)
  assert.equal(ui.requests[0].method, 'POST')
  assert.equal(ui.select.props.disabled, true)
  ui.unmount()
  ui.storage.set(KEY, 'newer-task')
  pending.resolve(job(9, { job_id: 'late-old-task', state: 'running', done: 0, worker_finished: false }))
  await flush()
  assert.equal(ui.storage.get(KEY), 'newer-task')
  assert.equal(ui.requests.length, 1, 'An unmounted page must not fetch the late task status')
})

test('a late start response after unmount saves its task for recovery when no newer task exists', async t => {
  const pending = deferred()
  const ui = await mountRefresh(t, { respond: () => pending.promise })
  await ui.click('补全儿童筛选资料')
  assert.equal(ui.storage.has(KEY), false)
  ui.unmount()
  pending.resolve(job(null, { job_id: 'recover-late-task', state: 'running', done: 0, worker_finished: false }))
  await flush()
  assert.equal(ui.storage.get(KEY), 'recover-late-task')
  assert.equal(ui.requests.length, 1, 'Saving the recovery reference must not fetch status after unmount')
  assert.equal(ui.requests[0].method, 'POST')
})
