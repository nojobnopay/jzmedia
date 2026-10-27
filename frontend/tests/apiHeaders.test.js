import test from 'node:test'
import assert from 'node:assert/strict'
import { api } from '../src/api.js'

function tokenStorage(t) {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', { configurable: true,
    value: { getItem: () => 'isolated-write-token' } })
  t.after(() => {
    if (original) Object.defineProperty(globalThis, 'localStorage', original)
    else delete globalThis.localStorage
  })
}

test('custom viewing-scope headers preserve the write token and JSON body type', async t => {
  tokenStorage(t)
  let options
  t.mock.method(globalThis, 'fetch', async (_path, value) => {
    options = value
    return { ok: true, json: async () => ({ saved: true }) }
  })
  const result = await api('/api/online/state/progress', { method: 'POST', body: '{"seq":1}',
    headers: { 'X-Online-State-Scope': 'a'.repeat(64) }, keepalive: true })
  assert.equal(result.saved, true)
  assert.equal(options.headers['X-Api-Token'], 'isolated-write-token')
  assert.equal(options.headers['Content-Type'], 'application/json')
  assert.equal(options.headers['X-Online-State-Scope'], 'a'.repeat(64))
  assert.equal(options.keepalive, true)
})

test('explicit content-type headers remain supported while reads add no JSON type', async t => {
  tokenStorage(t)
  const calls = []
  t.mock.method(globalThis, 'fetch', async (_path, value) => {
    calls.push(value)
    return { ok: true, json: async () => ({}) }
  })
  await api('/api/example', { method: 'POST', body: 'example', headers: { 'Content-Type': 'text/plain' } })
  await api('/api/example', { headers: { 'X-Request-Test': 'isolated' } })
  assert.equal(calls[0].headers['Content-Type'], 'text/plain')
  assert.equal(calls[0].headers['X-Api-Token'], 'isolated-write-token')
  assert.equal(calls[1].headers['Content-Type'], undefined)
  assert.equal(calls[1].headers['X-Request-Test'], 'isolated')
  assert.equal(calls[1].headers['X-Api-Token'], 'isolated-write-token')
})
