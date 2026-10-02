import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope } from 'vue'
import { useFileChanges } from '../src/useFileChanges.js'

function fixture() {
  const scope = effectScope(), requests = []
  const changes = scope.run(() => useFileChanges((url, options) => new Promise((resolve, reject) => requests.push({ url, ...options, resolve, reject }))))
  return { scope, requests, ...changes }
}

test('a different library finishing a copy does not cancel a leave check', async () => {
  const f = fixture()
  try {
    const a = f.refresh(1, { fresh: true }), b = f.refresh(2)
    assert.equal(f.requests.length, 2)
    assert.equal(f.requests[0].signal.aborted, false)
    f.requests[1].resolve({ library_id: 2, pending: true, count: 1 })
    await b
    f.requests[0].resolve({ library_id: 1, pending: true, count: 2 })
    assert.equal((await a).count, 2)
    assert.equal(f.changes.value[1].pending, true)
    assert.equal(f.changes.value[2].pending, true)
  } finally { f.scope.stop() }
})

test('leave checks fetch a fresh snapshot after an earlier poll completes', async () => {
  const f = fixture()
  try {
    const poll = f.refresh(1)
    const guard = f.refresh(1, { fresh: true })
    assert.equal(f.requests.length, 1)
    f.requests[0].resolve({ library_id: 1, pending: false, count: 0 })
    await poll
    await Promise.resolve()
    assert.equal(f.requests.length, 2)
    f.requests[1].resolve({ library_id: 1, pending: true, count: 1 })
    assert.equal((await guard).pending, true)
  } finally { f.scope.stop() }
})

test('failed leave checks retain their own error even when another library refreshes', async () => {
  const f = fixture()
  try {
    const a = f.refresh(1), b = f.refresh(2)
    f.requests[0].reject(new Error('offline'))
    assert.equal(await a, null)
    f.requests[1].resolve({ library_id: 2, pending: false })
    await b
    assert.match(f.errors.value[1], /offline/)
    assert.equal(f.errors.value[2], '')
  } finally { f.scope.stop() }
})
