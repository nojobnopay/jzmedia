import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, nodeText, flush, deferred } from './helpers/renderHarness.js'

const hasClass = (node, name) => String(node.props.class || '').split(/\s+/).includes(name)
const button = (ui, name) => ui.find(node => node.type === 'button' && (node.props['aria-label'] || nodeText(node).trim()) === name)
const checkbox = (ui, name) => ui.find(node => node.type === 'input' && node.props['aria-label'] === name)
const rows = (ui, name) => ui.all().filter(node => node.type === 'li' && hasClass(node, name))
const click = async (ui, name) => { assert.ok(button(ui, name), `Missing button: ${name}`); button(ui, name).props.onClick(); await flush() }
const completedItem = index => ({ id: index, library_id: 1, kind: index === 2 ? 'episode' : 'movie',
  title: `已生成样本 ${index}`, file_name: `ready-${index}.mkv`, frames: 150, interval: 10,
  quality_revision: index === 2 ? 1 : 2, needs_update: index === 2 })
const failedItem = index => ({ id: index + 100, library_id: 1, kind: index === 2 ? 'episode' : 'movie',
  title: `生成失败样本 ${index}`, file_name: `failed-${index}.mkv`, failed_at: 1789500000 + index,
  error_code: 'timeout', error: '抽取缩略图超时，请稍后重新生成。' })

async function mount(t) {
  const requests = [], timers = new Map()
  let timerId = 0, revision = 1, completedGate = null, completedFailure = false, queueFailure = false
  let failedGate = null, failedFailure = false, retryGate = null, retryResult = 'queued'
  let startGate = null, startFailure = false, cancelGate = null, singleCancelFailure = false, batchSequence = 1
  const listeners = new Set(), previousDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  const document = { activeElement: null, body: {},
    addEventListener(type, handler) { if (type === 'keydown') listeners.add(handler) },
    removeEventListener(type, handler) { if (type === 'keydown') listeners.delete(handler) } }
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document })
  let ui
  const seenNodes = new Set()
  function wireFocus() {
    for (const node of seenNodes) node.isConnected = false
    for (const node of ui?.all() || []) {
      seenNodes.add(node)
      node.isConnected = true
      node.focus = () => { document.activeElement = node }
    }
  }
  // Keep the real dialog and its Escape/busy events; the browser fixture checks
  // the actual teleport and Tab trap. Give native harness nodes focus methods.
  const vue = { ...Vue, Teleport: 'div', async nextTick(callback) {
    await Vue.nextTick(); wireFocus(); return callback?.()
  } }
  const state = {
    pending: Array.from({ length: 52 }, (_, index) => ({ kind: 'movie', id: index + 1,
      library_id: 1, title: `待处理样本 ${index + 1}`, file_name: `pending-${index + 1}.mkv`, source: 'batch', batch_id: 'batch-1' })),
    running: { kind: 'movie', id: 91, library_id: 1, title: '当前处理样本', file_name: 'current.mkv',
      job_id: 'manual-job', source: 'batch', batch_id: 'batch-1', auto: false, waiting: false, frames: 4, frame_total: 10 },
    job: { state: 'running', job_id: 'manual-job', done: 0, total: 53 },
    completed: Array.from({ length: 53 }, (_, index) => completedItem(index + 1)),
    failed: Array.from({ length: 53 }, (_, index) => failedItem(index + 1)),
  }
  t.mock.method(globalThis, 'setTimeout', (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id })
  t.mock.method(globalThis, 'clearTimeout', id => timers.delete(id))
  const api = async (path, options = {}) => {
    const url = new URL(path, 'http://fixture.invalid'), body = options.body && JSON.parse(options.body)
    const request = { path: url.pathname, query: Object.fromEntries(url.searchParams), options, body }
    requests.push(request)
    const library = Number(url.searchParams.get('library_id')), offset = Number(url.searchParams.get('offset') || 0)
    if (url.pathname.endsWith('/auto')) return { mode: 'local', start_hour: 3, end_hour: 6, queued: state.pending.length }
    if (url.pathname.endsWith('/jobs') && options.method === 'POST') {
      if (startGate) { const gate = startGate; startGate = null; return gate.promise }
      if (startFailure) { startFailure = false; throw new Error('批量加入暂时不可用') }
      const batch_id = `batch-${++batchSequence}`
      state.pending.push(...[201, 202].map(id => ({ kind: 'movie', id, library_id: body.library_id,
        title: `新批次样本 ${id}`, file_name: `batch-${id}.mkv`, source: 'batch', batch_id })))
      revision++
      return { state: 'queued', batch_id, total: 55, added: 2, promoted: 0, ready: 0, already_queued: 53, unavailable: 0 }
    }
    if (url.pathname.endsWith('/completed')) {
      const items = library === 1 ? state.completed : []
      const response = { items: items.slice(offset, offset + 50), total: items.length, offset, limit: 50 }
      if (completedGate) { const gate = completedGate; completedGate = null; return gate.promise }
      if (completedFailure) { completedFailure = false; throw new Error('已生成清单暂时不可用') }
      return response
    }
    if (url.pathname.endsWith('/failed')) {
      const items = library === 1 ? state.failed : []
      const response = { items: items.slice(offset, offset + 50), total: items.length, offset, limit: 50 }
      if (failedGate) { const gate = failedGate; failedGate = null; return gate.promise }
      if (failedFailure) { failedFailure = false; throw new Error('失败清单暂时不可用') }
      return response
    }
    if (url.pathname.endsWith('/retry')) {
      assert.equal(options.method, 'POST')
      assert.deepEqual(body, { library_id: 1 })
      if (retryGate) { const gate = retryGate; retryGate = null; return gate.promise }
      if (retryResult === 'queued') {
        const id = Number(url.pathname.split('/').at(-2))
        state.pending.push({ ...state.failed.find(item => item.id === id), auto: false, source: 'retry' })
        revision++
      }
      return { state: retryResult }
    }
    if (url.pathname.endsWith('/queue') && !options.method) {
      if (queueFailure) { queueFailure = false; throw new Error('队列暂时不可用') }
      const items = state.pending.filter(item => item.library_id === library)
      const batches = new Map()
      for (const item of [...items, ...(state.running?.library_id === library ? [state.running] : [])]) {
        if (!item.batch_id) continue
        if (!batches.has(item.batch_id)) batches.set(item.batch_id, { batch_id: item.batch_id, pending: 0, running: 0, remaining: 0 })
        const batch = batches.get(item.batch_id)
        if (item === state.running) batch.running++
        else batch.pending++
        batch.remaining++
      }
      return { items: items.slice(offset, offset + 50), total: items.length, revision: String(revision),
        batches: [...batches.values()], running: state.running?.library_id === library ? { ...state.running } : null }
    }
    if (url.pathname.endsWith('/queue/remove')) {
      assert.equal(body.revision, String(revision))
      const key = item => `${item.kind}:${item.id}`
      const selected = new Set((body.all_pending ? body.exclude : body.items).map(key))
      const before = state.pending.length
      state.pending = state.pending.filter(item => body.all_pending ? selected.has(key(item)) : !selected.has(key(item)))
      revision++
      return { removed: before - state.pending.length }
    }
    if (/\/queue\/movie\/\d+$/.test(url.pathname) && options.method === 'DELETE') {
      if (singleCancelFailure) { singleCancelFailure = false; throw new Error('取消当前影片暂时不可用') }
      const id = Number(url.pathname.split('/').at(-1))
      state.pending = state.pending.filter(item => item.id !== id)
      if (state.running?.id === id) state.running = null
      revision++
      return { removed: true }
    }
    if (/\/batches\/[^/]+\/cancel$/.test(url.pathname)) {
      if (cancelGate) { const gate = cancelGate; cancelGate = null; return gate.promise }
      const id = url.pathname.split('/').at(-2), before = state.pending.length
      state.pending = state.pending.filter(item => item.batch_id !== id || item.library_id !== body.library_id)
      const cancelled = state.running?.batch_id === id && state.running?.library_id === body.library_id
      if (cancelled) state.running = null
      revision++
      return { removed: before - state.pending.length + (cancelled ? 1 : 0), cancelled }
    }
    throw new Error(`Unexpected request: ${path}`)
  }
  const component = await loadSfc(new URL('../src/components/PreviewMaintenance.vue', import.meta.url), {
    '../api.js': { api }, vue, '../useFocusTrap.js': { useFocusTrap: () => ({ isTop: () => true }) },
  })
  const props = Vue.reactive({ libraryId: 1, active: true, librarySource: 'local', libraryEnabled: true })
  ui = renderHarness(component, props)
  t.after(() => { ui.app.unmount(); if (previousDocument) Object.defineProperty(globalThis, 'document', previousDocument); else delete globalThis.document })
  await flush()
  wireFocus()
  return { ui, props, state, requests,
    async poll() { const timer = [...timers.values()].find(item => item.delay === 2500 || item.delay === 30000); assert.ok(timer); timer.fn(); await flush(); wireFocus() },
    holdCompleted() { completedGate = deferred(); return completedGate },
    holdFailed() { failedGate = deferred(); return failedGate },
    holdRetry() { retryGate = deferred(); return retryGate },
    holdStart() { startGate = deferred(); return startGate }, failStart() { startFailure = true },
    holdBatchCancel() { cancelGate = deferred(); return cancelGate },
    failSingleCancel() { singleCancelFailure = true },
    escape() {
      const event = { key: 'Escape', defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true }, stopPropagation() {} }
      for (const listener of listeners) listener(event)
    },
    document, wireFocus,
    failFailed() { failedFailure = true }, setRetryResult(value) { retryResult = value },
    failCompleted() { completedFailure = true }, failQueue() { queueFailure = true },
    changeRevision() { revision++ },
    completedCalls: () => requests.filter(request => request.path.endsWith('/completed')),
    failedCalls: () => requests.filter(request => request.path.endsWith('/failed')),
    retryCalls: () => requests.filter(request => request.path.endsWith('/retry')),
  }
}

test('one processing list highlights current work and excludes it from pending bulk removal', async t => {
  const { ui, requests } = await mount(t)
  assert.match(ui.text(), /共 53 项 · 处理中 1 · 等待 52/)
  const current = rows(ui, 'is-running')
  assert.equal(current.length, 1)
  assert.equal(rows(ui, 'is-pending').length, 50)
  assert.equal(current[0].children.some(node => node.type === 'input'), false)
  const progress = ui.find(node => node.type === 'progress')
  assert.equal(progress.props.value, 4)
  assert.equal(progress.props.max, 10)
  assert.match(progress.props['aria-label'], /当前处理样本.*进度/)
  checkbox(ui, '全选本库待处理项').props.onChange()
  await flush()
  assert.match(ui.text(), /已选 52 项/)
  await click(ui, '批量移出选中项')
  const removal = requests.find(request => request.path.endsWith('/queue/remove')).body
  assert.equal(removal.all_pending, true)
  assert.deepEqual(removal.items, [])
  assert.equal(rows(ui, 'is-running').length, 1)
  assert.equal(rows(ui, 'is-pending').length, 0)
  assert.doesNotMatch(ui.text(), /当前视频库没有待处理影片/)
  await click(ui, '取消当前影片 当前处理样本')
  assert.ok(requests.some(request => request.path === '/api/stream/previews/queue/movie/91' && request.options.method === 'DELETE'))
  assert.equal(requests.some(request => request.path.includes('/jobs/')), false)
  assert.match(ui.text(), /当前视频库没有待处理影片/)
})

test('pending selections survive paging and completed browsing; select-all exclusions remain library scoped', async t => {
  const { ui, requests } = await mount(t)
  checkbox(ui, '选择 待处理样本 1').props.onChange()
  await click(ui, '下一页')
  checkbox(ui, '选择 待处理样本 52').props.onChange()
  await click(ui, '已生成缩略图')
  await click(ui, '已生成下一页')
  await click(ui, '已生成缩略图')
  assert.match(ui.text(), /已选 2 项/)
  await click(ui, '批量移出选中项')
  let removal = requests.filter(request => request.path.endsWith('/queue/remove')).at(-1).body
  assert.deepEqual(removal.items, [{ kind: 'movie', id: 1 }, { kind: 'movie', id: 52 }])
  checkbox(ui, '全选本库待处理项').props.onChange()
  checkbox(ui, '选择 待处理样本 2').props.onChange()
  await flush()
  assert.match(ui.text(), /已选 49 项/)
  await click(ui, '批量移出选中项')
  removal = requests.filter(request => request.path.endsWith('/queue/remove')).at(-1).body
  assert.equal(removal.all_pending, true)
  assert.deepEqual(removal.exclude, [{ kind: 'movie', id: 2 }])
  assert.equal(rows(ui, 'is-pending').length, 1)
  assert.match(nodeText(rows(ui, 'is-pending')[0]), /待处理样本 2/)
})

test('completed pages load on demand, remain independent of polls and refresh only on task transition', async t => {
  const setup = await mount(t), { ui, state } = setup
  await setup.poll()
  assert.equal(setup.completedCalls().length, 0)
  await click(ui, '已生成缩略图')
  assert.equal(button(ui, '已生成缩略图').props['aria-expanded'], true)
  assert.equal(rows(ui, 'completed-row').length, 50)
  assert.match(ui.text(), /旧版可用/)
  assert.match(ui.text(), /共 150 张.*10 秒\/张/)
  await setup.poll()
  assert.equal(setup.completedCalls().length, 1)
  state.running.frames = 10
  await setup.poll()
  assert.match(ui.text(), /抽帧完成，正在整理缩略图/)
  assert.equal(setup.completedCalls().length, 1, '100% frames is not a complete preview set')
  await click(ui, '已生成下一页')
  assert.equal(setup.completedCalls().at(-1).query.offset, '50')
  assert.equal(rows(ui, 'completed-row').length, 3)
  await click(ui, '刷新已生成缩略图')
  assert.equal(setup.completedCalls().length, 3)
  state.running = null
  state.job = { state: 'done', done: 53, total: 53 }
  await setup.poll()
  assert.equal(setup.completedCalls().length, 4)
  await click(ui, '已生成缩略图')
  await setup.poll()
  assert.equal(setup.completedCalls().length, 4)
})

test('completed loading, failure and empty states do not block the processing queue', async t => {
  const setup = await mount(t), { ui, state } = setup
  const pending = setup.holdCompleted()
  await click(ui, '已生成缩略图')
  assert.match(ui.text(), /正在加载已生成缩略图/)
  assert.doesNotMatch(ui.text(), /暂无可用的完整缩略图/)
  state.running.waiting = true
  state.running.frames = 7
  await setup.poll()
  assert.equal(ui.find(node => node.type === 'progress').props.value, 7)
  assert.match(ui.text(), /等待播放转码释放资源/)
  pending.reject(new Error('已生成清单暂时不可用'))
  await flush()
  assert.match(ui.text(), /已生成清单暂时不可用/)
  assert.equal(rows(ui, 'is-running').length, 1)
  assert.doesNotMatch(ui.text(), /暂无可用的完整缩略图/)
  state.completed = []
  await click(ui, '重试加载已生成缩略图')
  assert.match(ui.text(), /当前视频库暂无可用的完整缩略图/)
  setup.failQueue()
  await setup.poll()
  assert.match(ui.text(), /队列暂时不可用/)
  state.completed = [completedItem(1)]
  await click(ui, '刷新已生成缩略图')
  assert.equal(rows(ui, 'completed-row').length, 1)
  assert.match(ui.text(), /队列暂时不可用/, 'Completed reload must not clear a queue failure')
})

test('completed requests abort on library change, collapse and unmount; late replies cannot repopulate another scope', async t => {
  const setup = await mount(t), { ui, props } = setup
  const stale = setup.holdCompleted()
  await click(ui, '已生成缩略图')
  const oldRequest = setup.completedCalls().at(-1)
  props.libraryId = 2
  await flush()
  assert.equal(oldRequest.options.signal.aborted, true)
  assert.equal(setup.completedCalls().at(-1).query.library_id, '2')
  stale.resolve({ items: [completedItem(99)], total: 1 })
  await flush()
  assert.equal(rows(ui, 'completed-row').length, 0)
  assert.doesNotMatch(ui.text(), /已生成样本 99/)
  const late = setup.holdCompleted()
  await click(ui, '刷新已生成缩略图')
  const collapsed = setup.completedCalls().at(-1)
  await click(ui, '已生成缩略图')
  assert.equal(collapsed.options.signal.aborted, true)
  late.resolve({ items: [completedItem(100)], total: 1 })
  await flush()
  assert.equal(rows(ui, 'completed-row').length, 0)
  await click(ui, '已生成缩略图')
  const unmounted = setup.holdCompleted()
  await click(ui, '刷新已生成缩略图')
  const last = setup.completedCalls().at(-1)
  ui.app.unmount()
  assert.equal(last.options.signal.aborted, true)
  const count = setup.requests.length
  unmounted.resolve({ items: [completedItem(101)], total: 1 })
  await flush()
  assert.equal(setup.requests.length, count)
})

test('external queue revisions reset pending selections without disturbing completed content', async t => {
  const setup = await mount(t), { ui } = setup
  await click(ui, '已生成缩略图')
  checkbox(ui, '选择 待处理样本 1').props.onChange()
  setup.changeRevision()
  await setup.poll()
  assert.match(ui.text(), /待处理队列已变化，请重新选择/)
  assert.match(ui.text(), /已选 0 项/)
  assert.equal(rows(ui, 'completed-row').length, 50)
  assert.equal(setup.completedCalls().length, 1)
})

test('failure list is collapsed by default, pages independently and refreshes during polling only while expanded', async t => {
  const setup = await mount(t), { ui, state } = setup
  await setup.poll()
  assert.equal(setup.failedCalls().length, 0)
  await click(ui, '生成失败')
  assert.equal(rows(ui, 'failed-row').length, 50)
  assert.match(ui.text(), /抽取缩略图超时.*最近失败|最近失败.*抽取缩略图超时/s)
  await click(ui, '失败下一页')
  assert.equal(rows(ui, 'failed-row').length, 3)
  assert.equal(setup.failedCalls().at(-1).query.offset, '50')
  assert.equal(rows(ui, 'is-pending').length, 50, 'Failure paging does not page the processing queue')
  state.failed = [failedItem(1)]
  await setup.poll()
  assert.equal(setup.failedCalls().at(-1).query.offset, '0', 'A shrinking failure list returns to its last available page')
  assert.equal(rows(ui, 'failed-row').length, 1)
  await click(ui, '生成失败')
  const count = setup.failedCalls().length
  await setup.poll()
  assert.equal(setup.failedCalls().length, count)
})

test('failure loading, errors and empty results remain distinct and do not block processing', async t => {
  const setup = await mount(t), { ui, state } = setup
  const pending = setup.holdFailed()
  await click(ui, '生成失败')
  assert.match(ui.text(), /正在加载失败清单/)
  assert.doesNotMatch(ui.text(), /暂无缩略图生成失败记录/)
  state.running.frames = 9
  await setup.poll()
  assert.equal(ui.find(node => node.type === 'progress').props.value, 9)
  pending.reject(new Error('失败清单暂时不可用'))
  await flush()
  assert.match(ui.text(), /失败清单暂时不可用/)
  assert.doesNotMatch(ui.text(), /暂无缩略图生成失败记录/)
  state.failed = []
  await click(ui, '重试加载失败清单')
  assert.match(ui.text(), /暂无缩略图生成失败记录/)
  setup.failFailed()
  await click(ui, '刷新失败清单')
  assert.match(ui.text(), /失败清单暂时不可用/)
  state.failed = [failedItem(1)]
  await click(ui, '重试加载失败清单')
  assert.equal(rows(ui, 'failed-row').length, 1)
})

test('failure requests abort on scope change, deactivation, collapse and unmount; stale replies never cross scopes', async t => {
  const setup = await mount(t), { ui, props } = setup
  const stale = setup.holdFailed()
  await click(ui, '生成失败')
  const first = setup.failedCalls().at(-1)
  props.libraryId = 2
  await flush()
  assert.equal(first.options.signal.aborted, true)
  stale.resolve({ items: [failedItem(99)], total: 1 })
  await flush()
  assert.equal(rows(ui, 'failed-row').length, 0)
  assert.doesNotMatch(ui.text(), /生成失败样本 99/)
  const inactive = setup.holdFailed()
  await click(ui, '刷新失败清单')
  const second = setup.failedCalls().at(-1)
  props.active = false
  await flush()
  assert.equal(second.options.signal.aborted, true)
  inactive.resolve({ items: [failedItem(98)], total: 1 })
  await flush()
  const count = setup.failedCalls().length
  assert.equal(rows(ui, 'failed-row').length, 0)
  props.active = true
  await flush()
  assert.equal(setup.failedCalls().length, count + 1)
  const collapsed = setup.holdFailed()
  await click(ui, '刷新失败清单')
  const third = setup.failedCalls().at(-1)
  await click(ui, '生成失败')
  assert.equal(third.options.signal.aborted, true)
  collapsed.resolve({ items: [failedItem(97)], total: 1 })
  await flush()
  await click(ui, '生成失败')
  const unmounted = setup.holdFailed()
  await click(ui, '刷新失败清单')
  const last = setup.failedCalls().at(-1)
  ui.app.unmount()
  assert.equal(last.options.signal.aborted, true)
  const lastCount = setup.requests.length
  unmounted.resolve({ items: [failedItem(96)], total: 1 })
  await flush()
  assert.equal(setup.requests.length, lastCount)
})

test('retry keeps the failure record, appends one manual task, handles deduplication and cancels only that running item', async t => {
  const setup = await mount(t), { ui, state } = setup
  await click(ui, '生成失败')
  const gate = setup.holdRetry()
  await click(ui, '重新生成 生成失败样本 1')
  assert.equal(button(ui, '重新生成 生成失败样本 1').props['aria-busy'], true)
  assert.equal(button(ui, '重新生成 生成失败样本 2').props.disabled, true)
  await click(ui, '重新生成 生成失败样本 1')
  assert.equal(setup.retryCalls().length, 1)
  gate.reject(new Error('队列暂时不可用'))
  await flush()
  assert.match(ui.text(), /队列暂时不可用.*可再次点击/)
  await click(ui, '重新生成 生成失败样本 1')
  assert.match(ui.text(), /已将“生成失败样本 1”加入处理队列末尾/)
  assert.equal(rows(ui, 'failed-row').length, 50)
  assert.equal(state.failed.length, 53)
  assert.equal(state.pending.at(-1).source, 'retry')
  setup.setRetryResult('already_queued')
  await click(ui, '重新生成 生成失败样本 1')
  assert.match(ui.text(), /“生成失败样本 1”已在处理队列中/)
  setup.setRetryResult('running')
  await click(ui, '重新生成 生成失败样本 1')
  assert.match(ui.text(), /“生成失败样本 1”正在处理中/)
  state.running = { ...failedItem(1), source: 'retry', auto: false, frames: 1, frame_total: 10 }
  await setup.poll()
  assert.match(nodeText(rows(ui, 'is-running')[0]), /手动重试/)
  await click(ui, '取消当前影片 生成失败样本 1')
  assert.ok(setup.requests.some(request => request.path === '/api/stream/previews/queue/movie/101' && request.options.method === 'DELETE'))
  assert.equal(setup.requests.some(request => request.path.endsWith('/cancel')), false)
  assert.equal(state.failed.length, 53, 'Cancelling a retry retains the unresolved failure')
})

test('a pending retry aborts on library change and its response cannot show old feedback', async t => {
  const setup = await mount(t), { ui, props } = setup
  await click(ui, '生成失败')
  const gate = setup.holdRetry()
  await click(ui, '重新生成 生成失败样本 1')
  const request = setup.retryCalls().at(-1)
  props.libraryId = 2
  await flush()
  assert.equal(request.options.signal.aborted, true)
  gate.resolve({ state: 'queued' })
  await flush()
  assert.doesNotMatch(ui.text(), /加入处理队列末尾/)
  assert.equal(setup.failedCalls().at(-1).query.library_id, '2')
})

test('queue counts and current-frame percentages replace the unrelated one-item job counter', async t => {
  const { ui, requests, state, poll } = await mount(t)
  assert.equal(requests.some(request => request.path.endsWith('/jobs/latest')), false)
  assert.match(ui.text(), /共 53 项 · 处理中 1 · 等待 52/)
  assert.match(nodeText(rows(ui, 'is-running')[0]), /4\/10 张 · 40%/)
  assert.equal(ui.find(node => node.type === 'progress').props['aria-valuetext'], '4/10 张 · 40%')
  assert.equal(button(ui, '立即批量生成').props.disabled, false)
  assert.doesNotMatch(ui.text(), /0\/1 项/)
  state.running.frame_total = 0
  await poll()
  const progress = ui.find(node => node.type === 'progress')
  assert.equal(progress.props.value, undefined)
  assert.equal(progress.props['aria-valuetext'], '准备中')
  state.running.frame_total = 10
  state.running.frames = 11
  await poll()
  assert.match(ui.text(), /10\/10 张 · 100%/)
  assert.match(ui.text(), /抽帧完成，正在整理缩略图/)
})

test('immediate batches can append during existing work, prevent duplicate submissions and retry errors', async t => {
  const setup = await mount(t), { ui, state, requests } = setup
  setup.failStart()
  await click(ui, '立即批量生成')
  assert.match(ui.text(), /批量加入暂时不可用/)
  assert.equal(rows(ui, 'is-running').length, 1)
  const gate = setup.holdStart()
  await click(ui, '立即批量生成')
  assert.equal(button(ui, '正在加入…').props['aria-busy'], true)
  await click(ui, '正在加入…')
  assert.equal(requests.filter(request => request.path.endsWith('/jobs')).length, 2)
  gate.resolve({ state: 'queued', batch_id: null, total: 55, added: 0, promoted: 2, ready: 0, already_queued: 53, unavailable: 0 })
  await flush()
  assert.match(ui.text(), /转为立即处理 2 项/)
  await click(ui, '立即批量生成')
  assert.match(ui.text(), /已加入 2 项立即批量任务/)
  assert.equal(state.running.id, 91, 'Appending a batch never replaces the current movie')
  assert.equal(state.pending.at(-1).batch_id, 'batch-2')
  assert.match(ui.text(), /共 55 项 · 处理中 1 · 等待 54/)
  assert.equal(requests.some(request => request.path.includes('/jobs/')), false)
})

for (const source of ['batch', 'retry', 'auto', 'direct']) {
  test(`current-film cancellation uses the same single-item API for ${source}`, async t => {
    const setup = await mount(t), { ui, state, requests } = setup
    state.running.source = source
    state.running.auto = source === 'auto'
    await setup.poll()
    setup.failSingleCancel()
    await click(ui, '取消当前影片 当前处理样本')
    assert.match(ui.text(), /取消当前影片暂时不可用/)
    assert.equal(state.running.id, 91)
    await click(ui, '取消当前影片 当前处理样本')
    assert.equal(state.running, null)
    assert.equal(state.pending.length, 52, 'Cancelling one film retains all pending batch members')
    assert.equal(requests.filter(request => request.path === '/api/stream/previews/queue/movie/91').length, 2)
    assert.equal(requests.some(request => request.path.endsWith('/cancel')), false)
  })
}

test('whole-batch confirmation only removes its captured batch and preserves other work', async t => {
  const setup = await mount(t), { ui, state, requests } = setup
  state.pending = [state.pending[0],
    { kind: 'movie', id: 201, library_id: 1, title: '第二批', source: 'batch', batch_id: 'batch-2' },
    { kind: 'movie', id: 202, library_id: 1, title: '重试样本', source: 'retry' },
    { kind: 'movie', id: 203, library_id: 1, title: '闲时样本', source: 'auto', auto: true }]
  await setup.poll()
  const trigger = button(ui, '取消本批次 2')
  trigger.focus()
  await click(ui, '取消本批次 2')
  assert.match(nodeText(ui.find(node => node.props.role === 'dialog')), /本批次剩余 1 项.*处理中 0 项，等待 1 项/s)
  assert.ok(setup.document.activeElement === button(ui, '继续生成'), 'Opening confirmation focuses the safe action')
  setup.escape()
  await flush()
  assert.equal(ui.find(node => node.props.role === 'dialog'), undefined)
  assert.ok(setup.document.activeElement === trigger, 'Escape restores the original batch button')
  assert.equal(requests.some(request => request.path.includes('/batches/')), false)
  await click(ui, '取消本批次 2')
  await click(ui, '确认取消本批次')
  const cancellation = requests.find(request => request.path.includes('/batches/'))
  assert.equal(cancellation.path, '/api/stream/previews/batches/batch-2/cancel')
  assert.deepEqual(cancellation.body, { library_id: 1 })
  assert.equal(state.running.id, 91)
  assert.deepEqual(state.pending.map(item => item.id), [1, 202, 203])
})

test('batch cancellation blocks Escape and duplicate requests while busy, then retains an error for retry', async t => {
  const setup = await mount(t), { ui, state, requests } = setup
  state.pending.push({ kind: 'movie', id: 202, library_id: 1, title: '其它重试', source: 'retry' })
  await setup.poll()
  button(ui, '取消本批次 1').focus()
  await click(ui, '取消本批次 1')
  const gate = setup.holdBatchCancel()
  await click(ui, '确认取消本批次')
  await click(ui, '确认取消本批次')
  assert.equal(requests.filter(request => request.path.includes('/batches/')).length, 1)
  assert.equal(button(ui, '继续生成').props.disabled, true)
  setup.escape()
  await flush()
  assert.ok(ui.find(node => node.props.role === 'dialog'))
  gate.reject(new Error('取消批次暂时不可用'))
  await flush()
  assert.match(ui.text(), /取消批次暂时不可用.*可重试取消/)
  assert.ok(setup.document.activeElement === button(ui, '确认取消本批次'), 'A failed cancellation focuses its retry action')
  await click(ui, '确认取消本批次')
  assert.equal(ui.find(node => node.props.role === 'dialog'), undefined)
  assert.equal(state.running, null)
  assert.deepEqual(state.pending.map(item => item.id), [202])
  assert.ok(setup.document.activeElement === ui.find(node => node.type === 'h5'), 'Removing the focused batch returns focus to the queue heading')
})

test('a confirmation retains its batch identity when finished batches disappear from the queue', async t => {
  const setup = await mount(t), { ui, state, requests } = setup
  state.pending.push({ kind: 'movie', id: 201, library_id: 1, title: '第二批', source: 'batch', batch_id: 'batch-2' })
  await setup.poll()
  await click(ui, '取消本批次 2')
  state.running = null
  state.pending = state.pending.filter(item => item.batch_id === 'batch-2')
  await setup.poll()
  await click(ui, '确认取消本批次')
  assert.equal(requests.find(request => request.path.includes('/batches/')).path, '/api/stream/previews/batches/batch-2/cancel')
})

test('batch requests abort and dismiss their confirmation on a library change', async t => {
  const setup = await mount(t), { ui, props, requests } = setup
  await click(ui, '取消本批次 1')
  const gate = setup.holdBatchCancel()
  await click(ui, '确认取消本批次')
  const request = requests.find(item => item.path.includes('/batches/'))
  props.libraryId = 2
  await flush()
  assert.equal(request.options.signal.aborted, true)
  assert.equal(ui.find(node => node.props.role === 'dialog'), undefined)
  gate.resolve({ removed: 53, cancelled: true })
  await flush()
  assert.doesNotMatch(ui.text(), /已取消本批次/)
  assert.equal(rows(ui, 'is-running').length, 0)
})

test('another library current film cannot supply this library progress or cancellation action', async t => {
  const setup = await mount(t), { ui, state } = setup
  state.running.library_id = 2
  await setup.poll()
  assert.equal(rows(ui, 'is-running').length, 0)
  assert.equal(ui.find(node => node.type === 'progress'), undefined)
  assert.match(ui.text(), /共 52 项 · 处理中 0 · 等待 52/)
})
