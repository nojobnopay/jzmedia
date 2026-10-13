import test from 'node:test'
import assert from 'node:assert/strict'
import { createRenderer, nextTick } from 'vue'
import { usePlaybackPreviews } from '../src/usePlaybackPreviews.js'

function mount() {
  let preview
  const renderer = createRenderer({
    createComment: () => ({}), insert: () => {}, remove: () => {},
    parentNode: () => null, nextSibling: () => null,
  })
  const app = renderer.createApp({ setup() {
    preview = usePlaybackPreviews(() => ({ id: 1, kind: 'episode' }))
    return () => null
  } })
  app.mount({})
  return { preview, app }
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await nextTick() }

test('生成请求后，晚到的旧清单不能覆盖任务状态；卸载取消轮询', async t => {
  const requests = []
  t.mock.method(globalThis, 'fetch', (url, options) => new Promise((resolve, reject) => {
    const req = { url, options, respond: data => resolve({ ok: true, json: async () => data }) }
    requests.push(req)
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))
  }))
  const { preview, app } = mount()
  try {
    assert.equal(requests.length, 1)
    const started = preview.start()
    assert.equal(requests[1].options.method, 'POST')
    requests[1].respond({ job_id: 'new' })
    await flush()
    requests[2].respond({ state: 'running', job_id: 'new', pages: [] })
    await started
    requests[0].respond({ state: 'missing', pages: [] })
    await flush()
    assert.equal(preview.busy.value, true)
    assert.match(preview.status.value, /生成中/)
  } finally { app.unmount() }
})

test('关窗时中止预览请求，晚到响应不再更新组件', async t => {
  let signal
  let respond
  t.mock.method(globalThis, 'fetch', (_url, options) => {
    signal = options.signal
    return new Promise(resolve => { respond = data => resolve({ ok: true, json: async () => data }) })
  })
  const { preview, app } = mount()
  app.unmount()
  assert.equal(signal.aborted, true)
  respond({ state: 'ready', pages: ['late.jpg'] })
  await flush()
  assert.equal(preview.manifest.value, null)
})

test('显示当前影片的抽帧进度，完成后不能重复发起生成', async t => {
  let manifest = { state: 'running', job_id: 'film-job', frames: 37, frame_total: 100,
    count: 100, columns: 5, rows: 5, pages: ['/page-1.jpg'] }
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, method: options?.method || 'GET' })
    return { ok: true, json: async () => manifest }
  })
  const { preview, app } = mount()
  try {
    await flush()
    assert.equal(preview.busy.value, true)
    assert.match(preview.status.value, /37\/100 张（37%）/)
    manifest = { ...manifest, state: 'ready', frames: undefined }
    await preview.start() // 运行中的任务不应重复发起
    assert.equal(calls.length, 1)
    const reopened = mount()
    try {
      await flush()
      assert.equal(reopened.preview.ready.value, true)
      assert.equal(reopened.preview.status.value, '预览已生成完毕')
      await reopened.preview.start()
      assert.equal(calls.filter(call => call.method === 'POST').length, 0)
    } finally { reopened.app.unmount() }
  } finally { app.unmount() }
})
