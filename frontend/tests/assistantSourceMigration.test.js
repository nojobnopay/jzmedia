import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, nodeText, flush, deferred } from './helpers/renderHarness.js'

const oldUrl = 'https://old.example/api/vod/', newUrl = 'https://new.example/api/vod/'
const baseline = 'a'.repeat(64), fingerprint = 'b'.repeat(64)
const source = { id: 'src_' + '1'.repeat(32), name: '我的片源', provider: 'maccms', api_base_url: oldUrl,
  allowed_hosts: ['old.example', 'old-media.example', 'custom.example'] }
const template = { id: 'liangzi', name: '量子', provider: 'maccms', api_base_url: newUrl,
  known_api_urls: ['https://old.example:443/api/vod'] }
const changes = [{ kind: 'api', old_host: 'old.example', new_host: 'new.example', notice_url: '' },
  { kind: 'media', old_host: 'old-media.example', new_host: 'new-media.example', notice_url: 'https://example.com/notice' }]
const initial = { kind: 'online_source', template_id: 'liangzi', api_base_url: null }

async function mount(t, { items = [source], suggestion = initial, save, replace, read, preview } = {}) {
  const requests = [], saved = [], busy = []
  const access = Vue.reactive({ blocked: '', setBusy: value => busy.push(value), saved: row => saved.push(row) })
  const api = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body)
    requests.push({ path, body, signal: options.signal, method: options.method || 'GET' })
    if (path.endsWith('/source-templates')) return { items: [template] }
    if (path.endsWith('/sources')) return { items }
    if (path.endsWith('/host-migration-preview')) return preview ? preview(body) : { expected_fingerprint: fingerprint, changes }
    if (path.endsWith('/replace-catalogue')) return replace ? replace(body) : { previous: { ...source, enabled: false }, source: { ...source,
      id: 'src_' + 'd'.repeat(32), api_base_url: body.api_base_url } }
    const row = items.find(item => path.endsWith('/' + item.id))
    assert.ok(row)
    if (options.method === 'PATCH') return save ? save(row, body) : { ...row, api_base_url: body.api_base_url }
    return read ? read(row) : { ...row, configuration_fingerprint: baseline }
  }
  const component = await loadSfc(new URL('../src/components/AssistantSourceMigration.vue', import.meta.url), {
    vue: { ...Vue, vModelSelect: {}, vModelText: {}, vModelCheckbox: {} }, '../api.js': { api },
  })
  const ui = renderHarness(component, { suggestion, access })
  t.after(() => ui.app.unmount())
  await flush()
  const field = id => ui.find(el => el.props.id === id)
  const button = name => ui.find(el => el.type === 'button' && nodeText(el).includes(name))
  return { ...ui, requests, saved, access, busy, field, button }
}

test('assistant recognizes renamed sources by reviewed URL and applies only the confirmed host choices', async t => {
  const response = deferred()
  const ui = await mount(t, { save: () => response.promise })
  assert.match(ui.text(), /我的片源/)
  assert.ok(ui.text().includes(oldUrl) && ui.text().includes(newUrl))
  assert.deepEqual(ui.requests.at(-1).body, { expected_fingerprint: baseline, allowed_hosts: source.allowed_hosts, api_base_url: newUrl })
  const check = ui.find(el => el.type === 'input' && el.props.value === 'old-media.example')
  check.props['onUpdate:modelValue'](['old.example'])
  await flush()
  const confirm = ui.button('确认应用')
  ui.access.blocked = '请先处理草稿。'
  await flush()
  await confirm.props.onClick()
  assert.equal(ui.requests.some(row => row.method === 'PATCH'), false)
  ui.access.blocked = ''
  await flush()
  const saving = confirm.props.onClick()
  await confirm.props.onClick()
  assert.deepEqual(ui.requests.filter(row => row.method === 'PATCH').map(row => row.body), [
    { api_base_url: newUrl, host_migration: { expected_fingerprint: fingerprint, selected_old_hosts: ['old.example'] } },
  ])
  response.resolve({ ...source, api_base_url: newUrl })
  await saving; await flush()
  assert.equal(ui.saved[0].api_base_url, newUrl)
  assert.deepEqual(ui.busy, [true, false])
  assert.match(ui.text(), /已更新“我的片源”/)
  assert.equal(ui.button('确认应用'), undefined)
})

test('ambiguous or unrecognized named sources need explicit selection, and custom addresses take precedence', async t => {
  const other = { ...source, id: 'src_' + '2'.repeat(32), api_base_url: 'https://other.example/vod' }
  const ui = await mount(t, { items: [source, other], suggestion: { ...initial, api_base_url: 'https://my.example/new' } })
  assert.equal(ui.requests.filter(row => row.path.includes('/sources/')).length, 0)
  assert.ok(ui.field('assistant-source-url'))
  ui.field('assistant-source-target').props['onUpdate:modelValue'](other.id)
  await flush()
  assert.equal(ui.requests.at(-1).body.api_base_url, 'https://my.example/new')
  assert.ok(ui.requests.at(-1).path.includes(other.id))
  const unmatched = await mount(t, { items: [other] })
  assert.equal(unmatched.requests.length, 2)
})

test('a custom destination needs a catalogue decision before applying an assistant proposal', async t => {
  const ui = await mount(t, { suggestion: { ...initial, template_id: null, api_base_url: 'https://other.example/catalog' } })
  assert.ok(ui.button('确认应用').props.disabled)
  await ui.button('确认应用').props.onClick()
  assert.equal(ui.requests.some(request => request.method === 'PATCH' || request.path.endsWith('/replace-catalogue')), false)
  ui.field('assistant-catalogue-change').props['onUpdate:modelValue']('replace'); await flush()
  await ui.button('确认应用').props.onClick(); await flush()
  assert.ok(ui.requests.at(-1).path.endsWith('/replace-catalogue'))
  assert.equal(ui.requests.at(-1).method, 'POST')
  assert.equal(ui.saved.length, 2)
  assert.equal(ui.saved[0].id, source.id)
  assert.notEqual(ui.saved[1].id, source.id)
})

test('a conflict blocks stale saves until a new snapshot; re-reading a completed change avoids another patch', async t => {
  let current = source
  const ui = await mount(t, { save: () => {
    current = { ...source, api_base_url: newUrl }
    throw Object.assign(new Error('conflict'), { status: 409 })
  }, read: () => ({ ...current, configuration_fingerprint: baseline }) })
  await ui.button('确认应用').props.onClick(); await flush()
  assert.match(ui.text(), /来源配置已变化/)
  assert.equal(ui.button('确认应用').props.disabled, true)
  await ui.button('重新核对').props.onClick(); await flush()
  assert.match(ui.text(), /此来源已使用该接口/)
  assert.equal(ui.button('确认应用').props.disabled, true)
})

test('a lost replacement response can be confirmed with the exact same draft and preview', async t => {
  const submitted = []
  const ui = await mount(t, { suggestion: { ...initial, template_id: null, api_base_url: 'https://other.example/catalog' },
    replace: body => {
      submitted.push(body)
      if (submitted.length === 1) throw new TypeError('connection closed')
      return { previous: { ...source, enabled: false }, source: { ...source, id: 'src_' + 'd'.repeat(32), api_base_url: body.api_base_url } }
    } })
  ui.field('assistant-catalogue-change').props['onUpdate:modelValue']('replace'); await flush()
  await ui.button('确认应用').props.onClick(); await flush()
  assert.match(ui.text(), /尚未收到换库结果/)
  assert.equal(ui.button('确认应用').props.disabled, false)
  await ui.button('确认应用').props.onClick(); await flush()
  assert.equal(submitted.length, 2)
  assert.deepEqual(submitted[1], submitted[0])
  assert.equal(ui.saved.length, 2)
})

test('switching targets discards a late snapshot, and cancelling a pending preview never writes', async t => {
  const lateRead = deferred(), latePreview = deferred()
  const other = { ...source, id: 'src_' + '2'.repeat(32), name: '第二个来源' }
  const ui = await mount(t, { items: [source, other], read: row => row.id === source.id ? lateRead.promise
    : { ...row, configuration_fingerprint: baseline }, preview: () => latePreview.promise })
  const target = ui.field('assistant-source-target')
  target.props['onUpdate:modelValue'](source.id); await flush()
  const oldRead = ui.requests.at(-1)
  target.props['onUpdate:modelValue'](other.id); await flush()
  assert.equal(oldRead.signal.aborted, true)
  const oldPreview = ui.requests.at(-1)
  assert.ok(oldPreview.path.endsWith('/host-migration-preview'))
  await ui.button('取消').props.onClick(); await flush()
  assert.equal(oldPreview.signal.aborted, true)
  lateRead.resolve({ ...source, configuration_fingerprint: baseline })
  latePreview.resolve({ expected_fingerprint: fingerprint, changes })
  await flush()
  assert.match(ui.text(), /已取消换址/)
  assert.ok(!ui.requests.some(row => row.method === 'PATCH'))
})
