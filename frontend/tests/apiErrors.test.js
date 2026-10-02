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
