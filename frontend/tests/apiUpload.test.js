import test from 'node:test'
import assert from 'node:assert/strict'
import { setImmediate as tick } from 'node:timers/promises'
import { apiUpload } from '../src/api.js'

const file = { name: '东北警察故事2 (2023).mp4', size: 10 * 1024 ** 3 }
const allowed = () => ({ ok: true, status: 200, json: async () => ({ ok: true }) })

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function uploadBrowser(t, fetchResponse = async () => allowed()) {
  const requests = [], forms = [], events = [], checks = []
  const install = (name, value) => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, name)
    Object.defineProperty(globalThis, name, { configurable: true, value })
    t.after(() => descriptor ? Object.defineProperty(globalThis, name, descriptor) : delete globalThis[name])
  }
  install('window', { dispatchEvent: event => events.push([event.type, event.detail]) })
  install('localStorage', { getItem: () => 'test-token' })
  install('XMLHttpRequest', class {
    constructor() {
      this.upload = {}
      this.headers = {}
      this.responseHeaders = {}
      requests.push(this)
    }
    open(method, url) { this.method = method; this.url = url }
    setRequestHeader(name, value) { this.headers[name] = value }
    getResponseHeader(name) { return this.responseHeaders[name] ?? null }
    send(body) { this.body = body }
    abort() { this.aborted = true; this.onabort?.() }
    respond(status, body, headers = {}) {
      this.status = status
      this.responseText = body
      this.responseHeaders = headers
      this.onload()
    }
  })
  install('FormData', class {
    constructor() { this.fields = []; forms.push(this) }
    append(...values) { this.fields.push(values) }
  })
  t.mock.method(globalThis, 'fetch', (path, opts) => {
    checks.push({ path, opts })
    return fetchResponse(path, opts)
  })
  return { requests, forms, events, checks }
}

test('upload checks management access before constructing or sending the movie', async t => {
  const body = { detail: '请验证保护密码后管理全部内容（包括隐私内容）', code: 'management_required' }
  const browser = uploadBrowser(t, async () => ({ ok: false, status: 403, text: async () => JSON.stringify(body) }))
  const upload = apiUpload('/api/uploads', file)

  await assert.rejects(upload.promise, { message: `403 ${body.detail}`, status: 403, code: body.code })
  assert.equal(browser.requests.length, 0, 'A rejected check must not start a file request')
  assert.equal(browser.forms.length, 0, 'A rejected check must not package the movie')
  assert.deepEqual(browser.events, [['jzmedia:viewing-error', { code: body.code }]])
  assert.equal(browser.checks.length, 1)
  const { path, opts } = browser.checks[0]
  assert.equal(path, '/api/uploads/check-access')
  assert.equal(opts.method, 'POST')
  assert.equal(opts.body, undefined)
  assert.equal(opts.headers['Content-Type'], undefined)
  assert.equal(opts.headers['X-Api-Token'], 'test-token')
  assert.equal(opts.signal.aborted, false)
})

test('upload preflight requests the token dialog for an ordinary 401', async t => {
  const browser = uploadBrowser(t, async () => ({ ok: false, status: 401, text: async () => '{"detail":"需要访问令牌"}' }))
  await assert.rejects(apiUpload('/api/uploads', file).promise, {
    message: '401 需要访问令牌', status: 401, code: '',
  })
  assert.deepEqual(browser.events.map(([type]) => type), ['jzmedia:unauthorized'])
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
})

test('cancelling an upload aborts its pending access check without sending a file', async t => {
  const pending = deferred()
  const browser = uploadBrowser(t, (_path, opts) => {
    opts.signal.addEventListener('abort', () => pending.reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true })
    return pending.promise
  })
  const upload = apiUpload('/api/uploads', file)
  const rejected = assert.rejects(upload.promise, { message: '已取消' })
  upload.abort()
  await rejected

  assert.equal(browser.checks[0].opts.signal.aborted, true)
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
  assert.deepEqual(browser.events, [])
})

test('a late successful check cannot start a movie upload after cancellation', async t => {
  const pending = deferred()
  const browser = uploadBrowser(t, () => pending.promise)
  const upload = apiUpload('/api/uploads', file)
  const rejected = assert.rejects(upload.promise, { message: '已取消' })
  upload.abort()
  upload.abort()
  pending.resolve(allowed())
  await rejected
  await tick()

  assert.equal(browser.checks[0].opts.signal.aborted, true)
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
})

test('a late successful check cannot start a movie upload after its caller becomes inactive', async t => {
  const pending = deferred()
  const browser = uploadBrowser(t, () => pending.promise)
  let current = true
  const upload = apiUpload('/api/uploads', file, { canStart: () => current })
  const rejected = assert.rejects(upload.promise, { message: '已取消' })
  assert.equal(browser.checks.length, 1)
  assert.equal(browser.requests.length, 0)
  current = false
  pending.resolve(allowed())
  await rejected
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
  assert.deepEqual(browser.events, [])
})

test('unlocking management access rechecks permission and continues the same upload', async t => {
  const authorization = deferred()
  let checks = 0
  let prompts = 0
  const browser = uploadBrowser(t, async () => ++checks === 1
    ? { ok: false, status: 403, text: async () => '{"detail":"请验证保护密码","code":"management_required"}' }
    : allowed())
  const upload = apiUpload('/api/uploads', file, {
    authorize: () => { prompts += 1; return authorization.promise },
  })
  await tick()
  assert.equal(prompts, 1)
  assert.equal(browser.checks.length, 1)
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
  assert.deepEqual(browser.events, [], 'The inline authorization callback owns the password prompt')

  authorization.resolve(true)
  await tick()
  assert.equal(browser.checks.length, 2, 'Permission must be checked again after the password prompt')
  assert.equal(browser.requests.length, 1)
  assert.deepEqual(browser.forms[0].fields, [['file', file, file.name]])
  browser.requests[0].respond(200, '{"movie_id":42}')
  assert.deepEqual(await upload.promise, { movie_id: 42 })
  assert.equal(prompts, 1)
})

test('cancelling the management password prompt leaves the movie unsent', async t => {
  const browser = uploadBrowser(t, async () => ({ ok: false, status: 403, text: async () => '{"code":"management_required"}' }))
  await assert.rejects(apiUpload('/api/uploads', file, { authorize: async () => false }).promise, { message: '已取消' })
  assert.equal(browser.checks.length, 1)
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
  assert.deepEqual(browser.events, [])
})

test('an authorization callback does not suppress a locked viewing session error', async t => {
  const browser = uploadBrowser(t, async () => ({
    ok: false, status: 403, text: async () => '{"detail":"请先解锁观看","code":"viewing_locked"}',
  }))
  let prompts = 0
  const upload = apiUpload('/api/uploads', file, { authorize: async () => { prompts += 1; return true } })
  await assert.rejects(upload.promise, { status: 403, code: 'viewing_locked', message: '403 请先解锁观看' })
  assert.equal(prompts, 0, 'Only management_required should invoke the authorization callback')
  assert.equal(browser.checks.length, 1)
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
  assert.deepEqual(browser.events, [['jzmedia:viewing-error', { code: 'viewing_locked' }]])
})

test('permission still denied after authorization reports the error without prompting again', async t => {
  const browser = uploadBrowser(t, async () => ({
    ok: false, status: 403, text: async () => '{"detail":"请验证保护密码","code":"management_required"}',
  }))
  let prompts = 0
  const upload = apiUpload('/api/uploads', file, { authorize: async () => { prompts += 1; return true } })
  await assert.rejects(upload.promise, { status: 403, code: 'management_required', message: '403 请验证保护密码' })
  assert.equal(prompts, 1)
  assert.equal(browser.checks.length, 2)
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
  assert.deepEqual(browser.events, [['jzmedia:viewing-error', { code: 'management_required' }]])
})

test('cancelling during the password prompt settles immediately and ignores a later unlock', async t => {
  const authorization = deferred()
  const browser = uploadBrowser(t, async () => ({ ok: false, status: 403, text: async () => '{"code":"management_required"}' }))
  const upload = apiUpload('/api/uploads', file, { authorize: () => authorization.promise })
  let rejection
  const observed = upload.promise.catch(error => { rejection = error })
  await tick()
  upload.abort()
  await tick()
  assert.equal(rejection?.message, '已取消', 'Closing the upload must not wait for the password prompt')
  authorization.resolve(true)
  await observed
  await tick()
  assert.equal(browser.checks.length, 1)
  assert.equal(browser.requests.length, 0)
  assert.equal(browser.forms.length, 0)
})

test('allowed uploads retain multipart fields, progress, completion and unlimited transfer time', async t => {
  const browser = uploadBrowser(t)
  const progress = []
  let uploaded = 0
  const upload = apiUpload('/api/uploads', file, {
    subdir: '电影', fields: { relpath: file.name, target_dir: '本地 分析', offset: 0, empty: '', absent: null },
    onProgress: (...values) => progress.push(values), onUploaded: () => { uploaded += 1 },
  })
  await tick()
  assert.equal(browser.checks.length, 1)
  assert.equal(browser.requests.length, 1)
  const xhr = browser.requests[0]
  assert.equal(xhr.method, 'POST')
  const url = new URL(xhr.url, 'http://localhost')
  assert.equal(url.pathname, '/api/uploads')
  assert.deepEqual(Object.fromEntries(url.searchParams), { subdir: '电影', relpath: file.name, target_dir: '本地 分析', offset: '0' })
  assert.equal(xhr.headers['X-Api-Token'], 'test-token')
  assert.equal(xhr.timeout, 0)
  assert.equal(browser.forms.length, 1)
  assert.equal(xhr.body, browser.forms[0])
  assert.deepEqual(xhr.body.fields, [['file', file, file.name]])
  xhr.upload.onprogress({ lengthComputable: false, loaded: 1, total: 3 })
  xhr.upload.onprogress({ lengthComputable: true, loaded: 1, total: 3 })
  assert.equal(uploaded, 0)
  xhr.upload.onprogress({ lengthComputable: true, loaded: 3, total: 3 })
  xhr.upload.onload()
  assert.deepEqual(progress, [[33, Math.round(file.size / 3)], [100, file.size]])
  assert.equal(uploaded, 1, 'Finishing the bytes fires onUploaded only once')
  xhr.respond(201, '{"movie_id":42}', { 'X-Viewing-Generation': '7' })
  assert.deepEqual(await upload.promise, { movie_id: 42 })
  assert.deepEqual(browser.events, [['jzmedia:viewing-generation', { generation: 7 }]])
})

test('actual upload responses preserve server codes and dispatch the appropriate authorization event', async t => {
  const cases = [
    { status: 403, body: { detail: '请验证保护密码', code: 'management_required' }, event: ['jzmedia:viewing-error', { code: 'management_required' }] },
    { status: 401, body: { detail: '观看会话已过期', code: 'viewing_expired' }, event: ['jzmedia:viewing-error', { code: 'viewing_expired' }] },
    { status: 401, body: { detail: '需要访问令牌' }, event: ['jzmedia:unauthorized'] },
    { status: 503, body: { detail: '视频库暂时不可用' } },
  ]
  for (const scenario of cases) {
    await t.test(`${scenario.status} ${scenario.body.code || scenario.body.detail}`, async t => {
      const browser = uploadBrowser(t)
      const upload = apiUpload('/api/uploads', file)
      await tick()
      const rejected = assert.rejects(upload.promise, {
        message: `${scenario.status} ${scenario.body.detail}`, status: scenario.status, code: scenario.body.code || '',
      })
      browser.requests[0].respond(scenario.status, JSON.stringify(scenario.body))
      await rejected
      if (scenario.event) {
        assert.equal(browser.events.length, 1)
        assert.equal(browser.events[0][0], scenario.event[0])
        if (scenario.event.length > 1) assert.deepEqual(browser.events[0][1], scenario.event[1])
      } else assert.deepEqual(browser.events, [])
    })
  }
})

test('cancelling a transfer after access is granted aborts its XMLHttpRequest', async t => {
  const browser = uploadBrowser(t)
  const upload = apiUpload('/api/uploads', file)
  await tick()
  const rejected = assert.rejects(upload.promise, { message: '已取消' })
  upload.abort()
  await rejected
  assert.equal(browser.requests[0].aborted, true)
})

test('permission lost during a file transfer reports the failure without resending the movie', async t => {
  const browser = uploadBrowser(t)
  let prompts = 0
  const upload = apiUpload('/api/uploads', file, { authorize: async () => { prompts += 1; return true } })
  await tick()
  const rejected = assert.rejects(upload.promise, { status: 403, code: 'management_required' })
  browser.requests[0].respond(403, '{"detail":"请验证保护密码","code":"management_required"}')
  await rejected
  await tick()
  assert.equal(prompts, 0)
  assert.equal(browser.checks.length, 1)
  assert.equal(browser.requests.length, 1)
  assert.equal(browser.forms.length, 1)
  assert.deepEqual(browser.events, [['jzmedia:viewing-error', { code: 'management_required' }]])
})
