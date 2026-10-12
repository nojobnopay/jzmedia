import test from 'node:test'
import assert from 'node:assert/strict'
import { loadMp4Blob } from '../src/mp4Chunks.js'

test('普通 MP4 字节生成本地 Blob；源 URL 不重编码，内容保持完整', async () => {
  let requested, bytes
  const result = await loadMp4Blob('/video?name=%E4%B8%AD%20%25', null, {
    fetcher: async url => { requested = url; return new Response(new Uint8Array([1, 2, 3])) },
    makeUrl: blob => { bytes = blob.size; return 'blob:fixture' },
  })
  assert.equal(requested, '/video?name=%E4%B8%AD%20%25')
  assert.equal(result.url, 'blob:fixture')
  assert.equal(bytes, 3)
})
test('声明长度或实际流超过限额时拒绝，不创建超限 Blob', async () => {
  const options = { maxBytes: 2, makeUrl: () => { assert.fail('must not allocate URL') } }
  await assert.rejects(loadMp4Blob('/media', null, { ...options,
    fetcher: async () => new Response('abc', { headers: { 'Content-Length': '3' } }) }), /内存限额/)
  await assert.rejects(loadMp4Blob('/media', null, { ...options,
    fetcher: async () => new Response('abc') }), /内存限额/)
})
test('用户取消后，忽略晚响应并禁止创建 Blob URL', async () => {
  let resolve
  const pending = new Promise(r => { resolve = r }), ctrl = new AbortController()
  const result = loadMp4Blob('/media', ctrl.signal, {
    fetcher: () => pending, makeUrl: () => { assert.fail('cancelled URL allocation') },
  })
  ctrl.abort()
  resolve(new Response('movie'))
  await assert.rejects(result, /已取消/)
})
test('网络超时与 HTTP/空数据错误有明确提示', async () => {
  await assert.rejects(loadMp4Blob('/media', null, { timeout: 10,
    fetcher: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('abort')))
    }) }), /下载超时/)
  await assert.rejects(loadMp4Blob('/media', null, { fetcher: async () => new Response('', { status: 404 }) }), /HTTP 404/)
  await assert.rejects(loadMp4Blob('/media', null, { fetcher: async () => new Response('') }), /片段为空/)
})
