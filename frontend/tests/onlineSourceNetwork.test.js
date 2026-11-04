import test from 'node:test'
import assert from 'node:assert/strict'
import { emptyOnlineSourceForm, resolveSourceNetworkChoice, networkChoiceSummary, proxyProfilePayload,
  proxyProfileForm, proxyProfilePatchPayload } from '../src/onlineSourceNetwork.js'

const PROFILE = 'np_' + '1'.repeat(32)

test('new sources are enabled while existing independent network choices retain their meaning', () => {
  assert.equal(emptyOnlineSourceForm().enabled, true)
  const choices = { default: 'profile:' + PROFILE, api: 'direct', media: 'inherit' }
  assert.deepEqual(resolveSourceNetworkChoice(choices, 'api', { mode: 'direct' }), { choice: 'direct', from: 'api' })
  assert.deepEqual(resolveSourceNetworkChoice(choices, 'media', { mode: 'direct' }), { choice: 'profile:' + PROFILE, from: 'default' })
  const inherited = { default: 'inherit', api: 'inherit', media: 'direct' }
  assert.deepEqual(resolveSourceNetworkChoice(inherited, 'api', { mode: 'proxy', profile_id: PROFILE }), { choice: 'profile:' + PROFILE, from: 'application' })
  assert.deepEqual(resolveSourceNetworkChoice(inherited, 'media', { mode: 'proxy', profile_id: PROFILE }), { choice: 'direct', from: 'media' })
  assert.equal(networkChoiceSummary('profile:missing', []), '网络配置暂不可用')
})

test('saved proxy editing round-trips endpoints and leaves blank credentials unchanged', () => {
  for (const endpoint of ['http://[::1]:7890', 'http://proxy.example', 'https://proxy.example']) {
    const form = proxyProfileForm({ name: 'NAS 既有代理', proxy_url: endpoint, has_credentials: true })
    const patch = proxyProfilePatchPayload(form)
    assert.equal(new URL(patch.proxy_url).origin, new URL(endpoint).origin)
    assert.equal(patch.name, 'NAS 既有代理')
    assert.equal('username' in patch, false)
    assert.equal('password' in patch, false)
    assert.equal('mode' in patch, false)
    assert.deepEqual(proxyProfilePatchPayload({ ...form, password: 'new-secret' }), { ...patch, password: 'new-secret' })
    assert.deepEqual(proxyProfilePatchPayload({ ...form, clear_credentials: true }), { ...patch, clear_credentials: true })
  }
  assert.equal(networkChoiceSummary('profile:' + PROFILE, [{ id: PROFILE, mode: 'proxy', name: 'NAS 既有代理', proxy_url: 'http://127.0.0.1:7890' }]),
    '代理 · NAS 既有代理 · http://127.0.0.1:7890')
})

test('proxy fields produce a server endpoint and separate write-only credentials', () => {
  const form = { protocol: 'http', host: '192.168.1.10', port: '7890', username: 'demo', password: 'private-test-password' }
  assert.deepEqual(proxyProfilePayload(form, '演示来源'), { name: '演示来源代理', mode: 'proxy',
    proxy_url: 'http://192.168.1.10:7890', username: 'demo', password: 'private-test-password' })
  for (const host of ['::1', '[::1]']) assert.equal(proxyProfilePayload({ ...form, host }, '演示').proxy_url, 'http://[::1]:7890')
  for (const patch of [{ host: 'http://proxy.invalid' }, { host: 'user:password@proxy.invalid' }, { host: 'proxy.invalid/path' },
    { host: 'proxy.invalid?token=secret' }, { host: 'proxy.invalid\n' + 'other' }, { port: '0' }, { port: '65536' },
    { port: '1.5' }, { protocol: 'socks5' }, { username: 'name:secret' }, { password: 'bad\npassword' }]) {
    assert.throws(() => proxyProfilePayload({ ...form, ...patch }, '演示'), /代理/)
  }
})
