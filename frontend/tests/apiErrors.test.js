import test from 'node:test'
import assert from 'node:assert/strict'
import { api } from '../src/api.js'

test('API errors display the server reason while preserving the HTTP status', async t => {
  const cases = [
    ['{"detail":"视频库暂时不可用"}', '503 视频库暂时不可用'],
    ['{"message":"请稍后重试"}', '503 请稍后重试'],
    ['网关暂时不可用', '503 网关暂时不可用'],
    ['{incomplete', '503 {incomplete'],
    ['{"detail":[{"msg":"缺少视频库"}]}', '503 {"detail":[{"msg":"缺少视频库"}]}'],
    [JSON.stringify({ detail: '文件'.repeat(200) }), '503 ' + '文件'.repeat(150) + '…'],
  ]
  for (const [body, expected] of cases) {
    t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 503, text: async () => body }))
    await assert.rejects(api('/api/test'), { message: expected })
    t.mock.restoreAll()
  }
})

test('readable authentication errors still request the token dialog', async t => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const events = []
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { dispatchEvent: event => events.push(event.type) } })
  t.after(() => descriptor ? Object.defineProperty(globalThis, 'window', descriptor) : delete globalThis.window)
  t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 401, text: async () => '{"detail":"需要访问令牌"}' }))
  await assert.rejects(api('/api/test', { method: 'POST' }), { message: '401 需要访问令牌' })
  assert.deepEqual(events, ['jzmedia:unauthorized'])
})

test('viewing authorization is distinct from the existing access token', async t => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const events = []
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { dispatchEvent: event => events.push([event.type, event.detail]) } })
  t.after(() => descriptor ? Object.defineProperty(globalThis, 'window', descriptor) : delete globalThis.window)
  t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 401, text: async () => JSON.stringify({ detail: '请验证保护密码', code: 'management_required' }) }))
  await assert.rejects(api('/api/settings'), { code: 'management_required', status: 401 })
  assert.deepEqual(events, [['jzmedia:viewing-error', { code: 'management_required' }]])
  events.length = 0
  await assert.rejects(api('/api/viewing/unlock', { method: 'POST', silentViewing: true }), { code: 'management_required' })
  assert.deepEqual(events, [], 'Password form errors stay in that form without requesting another dialog')
})

test('background TV checks keep authorization failures silent while retaining status', async t => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'window'), events = []
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { dispatchEvent: event => events.push(event.type) } })
  t.after(() => descriptor ? Object.defineProperty(globalThis, 'window', descriptor) : delete globalThis.window)
  for (const body of [{ detail: '需要访问令牌' }, { detail: '需要管理权限', code: 'management_required' }]) {
    t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 401, text: async () => JSON.stringify(body) }))
    await assert.rejects(api('/api/jobs/tv-ingest', { method: 'POST', body: '{}', silentViewing: true, silentAuth: true }), { status: 401 })
  }
  assert.deepEqual(events, [])
})

for (const source of ['timeout', 'caller']) {
  test(`API ${source} cancels a pending response body after headers`, async t => {
    const outer = new AbortController()
    let reading
    const bodyStarted = new Promise(resolve => { reading = resolve })
    t.mock.method(globalThis, 'fetch', async (_path, { signal }) => ({
      ok: true,
      json: () => new Promise((resolve, reject) => {
        const fallback = setTimeout(() => resolve({}), 500)
        signal.addEventListener('abort', () => {
          clearTimeout(fallback)
          reject(new globalThis.DOMException('Aborted', 'AbortError'))
        }, { once: true })
        reading()
      })
    }))
    const request = api('/api/test', { signal: outer.signal, timeout: source === 'timeout' ? 30 : 1000 })
    await bodyStarted
    if (source === 'caller') outer.abort()
    await assert.rejects(request, source === 'timeout'
      ? { name: 'TimeoutError', message: '请求超时，请检查连接后重试' }
      : { name: 'Error', message: '已取消' })
  })
}
