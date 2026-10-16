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

async function mount(t) {
  const requests = [], timers = new Map()
  let timerId = 0, revision = 1, completedGate = null, completedFailure = false, queueFailure = false
  const state = {
    pending: Array.from({ length: 52 }, (_, index) => ({ kind: 'movie', id: index + 1,
      library_id: 1, title: `待处理样本 ${index + 1}`, file_name: `pending-${index + 1}.mkv` })),
    running: { kind: 'movie', id: 91, library_id: 1, title: '当前处理样本', file_name: 'current.mkv',
      job_id: 'manual-job', auto: false, waiting: false, frames: 4, frame_total: 10 },
    job: { state: 'running', job_id: 'manual-job', done: 0, total: 53 },
    completed: Array.from({ length: 53 }, (_, index) => completedItem(index + 1)),
  }
  t.mock.method(globalThis, 'setTimeout', (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id })
  t.mock.method(globalThis, 'clearTimeout', id => timers.delete(id))
  const api = async (path, options = {}) => {
    const url = new URL(path, 'http://fixture.invalid'), body = options.body && JSON.parse(options.body)
    const request = { path: url.pathname, query: Object.fromEntries(url.searchParams), options, body }
    requests.push(request)
    const library = Number(url.searchParams.get('library_id')), offset = Number(url.searchParams.get('offset') || 0)
    if (url.pathname.endsWith('/jobs/latest')) return library === 1 ? { ...state.job } : { state: 'idle' }
    if (url.pathname.endsWith('/auto')) return { mode: 'local', start_hour: 3, end_hour: 6, queued: state.pending.length }
    if (url.pathname.endsWith('/completed')) {
      const items = library === 1 ? state.completed : []
      const response = { items: items.slice(offset, offset + 50), total: items.length, offset, limit: 50 }
      if (completedGate) { const gate = completedGate; completedGate = null; return gate.promise }
      if (completedFailure) { completedFailure = false; throw new Error('已生成清单暂时不可用') }
      return response
    }
    if (url.pathname.endsWith('/queue') && !options.method) {
      if (queueFailure) { queueFailure = false; throw new Error('队列暂时不可用') }
      const items = state.pending.filter(item => item.library_id === library)
      return { items: items.slice(offset, offset + 50), total: items.length, revision: String(revision),
        running: state.running?.library_id === library ? { ...state.running } : null }
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
      state.pending = state.pending.filter(item => item.id !== Number(url.pathname.split('/').at(-1)))
      revision++
      return { removed: true }
    }
    if (url.pathname.endsWith('/cancel')) { state.running = null; state.job = { state: 'cancelled' }; return {} }
    throw new Error(`Unexpected request: ${path}`)
  }
  const component = await loadSfc(new URL('../src/components/PreviewMaintenance.vue', import.meta.url), { '../api.js': { api } })
  const props = Vue.reactive({ libraryId: 1, active: true, librarySource: 'local', libraryEnabled: true })
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  await flush()
  return { ui, props, state, requests,
    async poll() { const timer = [...timers.values()].find(item => item.delay === 2500 || item.delay === 30000); assert.ok(timer); timer.fn(); await flush() },
    holdCompleted() { completedGate = deferred(); return completedGate },
    failCompleted() { completedFailure = true }, failQueue() { queueFailure = true },
    changeRevision() { revision++ },
    completedCalls: () => requests.filter(request => request.path.endsWith('/completed')),
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
  await click(ui, '取消立即生成任务 当前处理样本')
  assert.ok(requests.some(request => request.path === '/api/stream/previews/jobs/manual-job/cancel'))
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
