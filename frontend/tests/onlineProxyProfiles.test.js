import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, nodeText, flush, deferred } from './helpers/renderHarness.js'

const PROFILE = 'np_' + '2'.repeat(32)
const proxy = () => ({ id: PROFILE, name: 'NAS 既有代理', mode: 'proxy', proxy_url: 'http://127.0.0.1:7890', has_credentials: true,
  connect_timeout_seconds: 5, read_timeout_seconds: 15, max_connections: 4, ca_file: '', ca_dir: '' })
const button = (ui, name) => ui.find(node => node.type === 'button' && (node.props['aria-label'] || nodeText(node).trim()) === name)
async function click(ui, name) { assert.ok(button(ui, name), 'Missing button: ' + name); await button(ui, name).props.onClick(); await flush() }
async function field(ui, id, value) { ui.find(node => node.props.id === id).props['onUpdate:modelValue'](value); await flush() }
async function submit(ui, attr) { await ui.find(node => node.type === 'form' && attr in node.props).props.onSubmit({ preventDefault() {} }); await flush() }
async function mount(t, { conflict = false, gate = null } = {}) {
  const requests = [], state = Vue.reactive({ profiles: [proxy()], applicationDefault: { rule: { mode: 'direct', profile_id: null } }, busy: false, dirty: false })
  const api = async (path, options) => {
    const body = options.body && JSON.parse(options.body)
    requests.push({ path, method: options.method, body })
    if (gate) await gate.promise
    if (options.method === 'DELETE') {
      if (conflict) throw Object.assign(new Error('private upstream details'), { status: 409 })
      return null
    }
    return path.endsWith('/default') ? body : { ...proxy(), ...body, has_credentials: !body.clear_credentials }
  }
  const component = await loadSfc(new URL('../src/components/OnlineProxyProfiles.vue', import.meta.url), {
    vue: { ...Vue, vModelText: {}, vModelSelect: {}, vModelCheckbox: {} }, '../api.js': { api },
  })
  const ui = renderHarness({ setup: () => () => Vue.h(component, { profiles: state.profiles, applicationDefault: state.applicationDefault,
    onUpdated: profile => { state.profiles = [profile] }, onDeleted: () => { state.profiles = [] },
    onDefaultUpdated: value => { state.applicationDefault = value }, onBusy: value => { state.busy = value }, onDirty: value => { state.dirty = value } }) })
  t.after(() => ui.app.unmount()); await flush()
  return { ui, requests, state }
}

test('saved proxy details are visible and editing keeps the profile id without replaying credentials', async t => {
  const { ui, requests, state } = await mount(t)
  assert.match(ui.text(), /127\.0\.0\.1:7890/)
  assert.match(ui.text(), /已保存认证信息/)
  await click(ui, '编辑代理 NAS 既有代理')
  assert.equal(state.dirty, false)
  await field(ui, 'online-profile-name', '家庭代理')
  await field(ui, 'online-profile-host', '192.168.1.10')
  assert.equal(state.dirty, true)
  await submit(ui, 'data-online-proxy-form')
  assert.deepEqual(requests, [{ path: '/api/online/network/profiles/' + PROFILE, method: 'PATCH', body: { name: '家庭代理', proxy_url: 'http://192.168.1.10:7890' } }])
  assert.equal(state.dirty, false)
  assert.equal(state.busy, false)
})

test('explicit credential clearing is submitted separately from replacement', async t => {
  const { ui, requests } = await mount(t)
  await click(ui, '编辑代理 NAS 既有代理')
  await field(ui, 'online-profile-clear', true)
  await submit(ui, 'data-online-proxy-form')
  assert.equal(requests[0].body.clear_credentials, true)
  assert.equal('password' in requests[0].body, false)
  assert.equal('username' in requests[0].body, false)
})

test('deleting a referenced proxy shows a recovery action and retains the profile', async t => {
  const { ui, state, requests } = await mount(t, { conflict: true })
  await click(ui, '删除代理 NAS 既有代理')
  assert.equal(requests.length, 0)
  await click(ui, '确认删除')
  assert.equal(state.profiles.length, 1)
  assert.match(ui.text(), /请先将相关连接改为直连或其它代理/)
  assert.equal(ui.text().includes('private upstream'), false)
})

test('unreferenced proxies can be deleted and service default can release a profile reference', async t => {
  const { ui, state, requests } = await mount(t)
  await field(ui, 'online-service-default', 'profile:' + PROFILE)
  assert.equal(state.dirty, true)
  await submit(ui, 'data-online-default-form')
  assert.deepEqual(requests[0].body, { rule: { mode: 'proxy', profile_id: PROFILE } })
  assert.equal(state.dirty, false)
  await field(ui, 'online-service-default', 'direct')
  await submit(ui, 'data-online-default-form')
  assert.deepEqual(requests[1].body, { rule: { mode: 'direct', profile_id: null } })
  await click(ui, '编辑代理 NAS 既有代理')
  await click(ui, '删除代理 NAS 既有代理')
  await click(ui, '确认删除')
  assert.equal(state.profiles.length, 0)
  assert.equal(ui.find(node => node.type === 'form' && 'data-online-proxy-form' in node.props), undefined)
})

test('pending profile writes expose busy state and retain dirty edits on failure', async t => {
  const gate = deferred(); t.after(() => gate.resolve())
  const { ui, state } = await mount(t, { gate })
  await click(ui, '编辑代理 NAS 既有代理')
  await field(ui, 'online-profile-host', 'proxy.example')
  const saving = submit(ui, 'data-online-proxy-form'); await flush()
  assert.equal(state.busy, true)
  assert.equal(button(ui, '取消编辑').props.disabled, true)
  gate.reject(new Error('isolated failure')); await saving
  assert.equal(state.busy, false)
  assert.equal(state.dirty, true)
  assert.match(ui.text(), /保存失败/)
})
