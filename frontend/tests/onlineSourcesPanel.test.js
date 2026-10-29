import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, nodeText, flush, deferred } from './helpers/renderHarness.js'

const SOURCE = 'src_' + '1'.repeat(32), PROFILE = 'np_' + '2'.repeat(32)
const inherit = () => ({ mode: 'inherit', profile_id: null })
const policy = () => Object.fromEntries(['default', 'api', 'media', 'image', 'subtitle', 'subscription'].map(key => [key, inherit()]))
const element = (ui, id) => ui.find(node => node.props.id === id)
const button = (ui, name) => ui.find(node => node.type === 'button' && (node.props['aria-label'] || nodeText(node).trim()) === name)
async function click(ui, name) { assert.ok(button(ui, name), 'Missing button: ' + name); await button(ui, name).props.onClick(); await flush() }
async function field(ui, id, value) { element(ui, id).props['onUpdate:modelValue'](value); await flush() }
async function submit(ui, attr) { await ui.find(node => node.type === 'form' && attr in node.props).props.onSubmit({ preventDefault() {} }); await flush() }

async function mount(t, { sourcePolicy = policy(), profiles = [], templatesFail = false, policyFailures = 0, catalogGate = null } = {}) {
  const requests = [], source = { id: SOURCE, name: '演示来源', provider: 'maccms', api_base_url: 'https://api.example/',
    allowed_hosts: ['api.example'], enabled: true, priority: 100, provider_options: {} }
  let draft
  const api = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body)
    requests.push({ path, method: options.method || 'GET', body })
    if (path === '/api/online/settings') return { enabled: true }
    if (path === '/api/online/sources') return options.method === 'POST' ? { ...body, id: SOURCE } : { items: [source] }
    if (path === '/api/online/network/profiles') {
      if (options.method === 'POST') return { id: PROFILE, name: body.name, mode: body.mode, proxy_url: body.proxy_url, has_credentials: !!(body.username || body.password) }
      return { items: profiles }
    }
    if (path === '/api/online/network/default') return { rule: { mode: 'direct', profile_id: null } }
    if (path === '/api/online/sources/' + SOURCE + '/network') {
      if (options.method === 'PATCH' && policyFailures-- > 0) throw new Error('private upstream detail must not enter UI')
      return { policy: sourcePolicy }
    }
    if (path === '/api/online/source-templates') {
      if (templatesFail) throw new Error('directory unavailable')
      return { items: [{ id: 'demo', name: '目录来源', provider: 'maccms', api_base_url: 'https://catalog.example/',
        allowed_hosts: ['catalog.example'], provider_options: { search_action: 'detail' }, description: '演示公开来源', network_note: '按服务器网络验证', homepage: 'https://catalog.example/' }] }
    }
    if (path.startsWith('/api/online/catalog/browse?')) return catalogGate ? catalogGate.promise : { items: [{ title: '演示影片' }] }
    throw new Error('Unexpected request: ' + path)
  }
  const component = await loadSfc(new URL('../src/components/OnlineSourcesPanel.vue', import.meta.url), {
    vue: { ...Vue, vModelText: {}, vModelSelect: {}, vModelCheckbox: {} },
    '../api.js': { api }, '../settingsDrafts.js': { useSettingsDraft(entry) { draft = entry } },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  await flush()
  return { ui, requests, get draft() { return draft } }
}

test('source directory pre-fills an enabled source; directory failure leaves custom creation available', async t => {
  for (const templatesFail of [false, true]) {
    const { ui, requests } = await mount(t, { templatesFail })
    await click(ui, '添加来源')
    if (!templatesFail) {
      await field(ui, 'online-source-template', 'demo')
      element(ui, 'online-source-template').props.onChange()
      await flush()
    } else {
      assert.ok(button(ui, '重试目录'))
      await field(ui, 'online-source-name', '自定义来源')
      await field(ui, 'online-source-api', 'https://custom.example/')
    }
    await submit(ui, 'data-online-source-form')
    const saved = requests.find(call => call.path === '/api/online/sources' && call.method === 'POST')
    assert.equal(saved.body.enabled, true)
    assert.equal(saved.body.name, templatesFail ? '自定义来源' : '目录来源')
    assert.equal(requests.some(call => call.path.startsWith('/api/online/catalog/')), false, 'saving does not silently probe a source')
  }
})

test('opening a source network retains inherited defaults and explicit per-purpose choices', async t => {
  const configured = policy(); configured.api = { mode: 'direct', profile_id: null }
  configured.media = { mode: 'proxy', profile_id: PROFILE }
  const { ui, requests, draft } = await mount(t, { sourcePolicy: configured,
    profiles: [{ id: PROFILE, mode: 'proxy', name: '已有代理', proxy_url: 'http://proxy.example:7890' }] })
  await click(ui, '网络 演示来源')
  assert.equal(draft.dirty(), false)
  await submit(ui, 'data-online-network-form')
  const saved = requests.find(call => call.method === 'PATCH')
  assert.deepEqual(saved.body, configured)
  assert.equal(requests.some(call => call.path === '/api/online/network/profiles' && call.method === 'POST'), false)
})

test('new proxy retry reuses the created profile and preserves explicit API direct routing', async t => {
  const configured = policy(); configured.api = { mode: 'direct', profile_id: null }
  const { ui, requests } = await mount(t, { sourcePolicy: configured, policyFailures: 1 })
  await click(ui, '网络 演示来源')
  element(ui, 'online-network-mode').props.onChange({ target: { value: 'proxy' } }); await flush()
  await field(ui, 'online-proxy-host', '127.0.0.1')
  await field(ui, 'online-proxy-port', '7890')
  await field(ui, 'online-proxy-username', 'demo')
  await field(ui, 'online-proxy-password', 'isolated-secret')
  await submit(ui, 'data-online-network-form')
  assert.equal(element(ui, 'online-proxy-password'), undefined, 'write-only fields leave the form after profile creation')
  assert.equal(ui.text().includes('isolated-secret'), false)
  assert.equal(ui.text().includes('private upstream detail'), false)
  await submit(ui, 'data-online-network-form')
  assert.equal(requests.filter(call => call.path === '/api/online/network/profiles' && call.method === 'POST').length, 1)
  const writes = requests.filter(call => call.method === 'PATCH')
  assert.equal(writes.length, 2)
  for (const write of writes) {
    assert.deepEqual(write.body.api, { mode: 'direct', profile_id: null })
    assert.deepEqual(write.body.default, { mode: 'proxy', profile_id: PROFILE })
    assert.deepEqual(write.body.media, inherit())
  }
})

test('source test is an explicit directory request and does not claim playback acceptance', async t => {
  const { ui, requests } = await mount(t)
  assert.equal(requests.some(call => call.path.startsWith('/api/online/catalog/')), false)
  await click(ui, '测试来源 演示来源')
  const reads = requests.filter(call => call.path.startsWith('/api/online/catalog/browse?'))
  assert.equal(reads.length, 1)
  assert.equal(new URL(reads[0].path, 'https://jzmedia.invalid').searchParams.get('source_id'), SOURCE)
  assert.match(ui.text(), /目录读取成功；可前往在线页选择影片试播/)
})

test('a directory test cannot publish an old route result after saving new network rules', async t => {
  const gate = deferred()
  t.after(() => gate.resolve({ items: [] }))
  const { ui } = await mount(t, { catalogGate: gate })
  const checking = button(ui, '测试来源 演示来源').props.onClick()
  await flush()
  await click(ui, '网络 演示来源')
  element(ui, 'online-network-mode').props.onChange({ target: { value: 'direct' } }); await flush()
  await submit(ui, 'data-online-network-form')
  gate.resolve({ items: [{ title: '旧出口返回的影片' }] })
  await checking; await flush()
  assert.equal(ui.text().includes('目录读取成功'), false)
})
