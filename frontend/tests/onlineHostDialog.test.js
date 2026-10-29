import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'
import { createOnlineHostReminders, onlineHostError, validOnlineHostReport } from '../src/onlineHosts.js'

const SOURCE = 'src_' + '1'.repeat(32)
const report = () => ({ source_id: SOURCE, external_id: 'show-1', configuration_fingerprint: 'f'.repeat(64),
  missing_hosts: ['cdn.example', 'images.example'], allowed_hosts: ['api.example'], rejected_count: 0 })
const button = (ui, text) => ui.find(node => node.type === 'button' && nodeText(node) === text)
async function click(ui, text) { const target = button(ui, text); assert.ok(target, text); await target.props.onClick(); await flush() }
async function mount(t, { request, refresh = async () => {}, session = {}, authorize = async () => true,
  refreshSession = async () => {}, current = () => true } = {}) {
  const calls = [], closes = [], auth = [], viewing = Vue.reactive({ enabled: false, management_unlocked: false, prompt: null, ...session })
  const component = await loadSfc(new URL('../src/components/OnlineHostDialog.vue', import.meta.url), {
    vue: { ...Vue, vModelCheckbox: {} },
    '../api.js': { api: async (path, options) => { calls.push({ path, ...options, body: JSON.parse(options.body) }); return request ? request(path, options) : report() } },
    '../viewing.js': { viewing, refreshViewing: async () => { auth.push('refresh'); await refreshSession(viewing) },
      ensureManagement: async () => { auth.push('authorize'); return authorize(viewing) } },
    './JzDialog.vue': { default: { props: ['title', 'busy', 'focusActive'], emits: ['close'],
      setup: (props, { slots, attrs }) => () => Vue.h('section', { ...attrs, 'data-test-busy': props.busy, 'data-test-focus': props.focusActive },
        [...(slots.default?.() || []), ...(slots.footer?.() || [])]) } },
  })
  const ui = renderHarness(component, { sourceId: SOURCE, externalId: 'show-1', title: '合成节目', sourceName: '合成来源', current, refresh, onClose: () => closes.push(true) })
  let mounted = true
  const unmount = () => { if (mounted) { mounted = false; ui.app.unmount() } }
  t.after(unmount)
  await flush()
  return { ui, calls, closes, auth, viewing, unmount }
}

test('automatic inspection only reads metadata; cancelling never grants any host', async t => {
  const { ui, calls, closes } = await mount(t)
  assert.equal(calls.length, 1)
  assert.ok(calls[0].path.endsWith('/host-check'))
  assert.equal(calls[0].silentViewing, true)
  assert.deepEqual(calls[0].body, { external_id: 'show-1' })
  assert.equal(ui.find(node => node.props['data-online-host-candidate'] !== undefined).props.value, 'cdn.example')
  await click(ui, '暂不添加')
  assert.equal(closes.length, 1)
  assert.equal(calls.length, 1)
})

test('explicit confirmation sends only selected hosts and exact fingerprint then awaits refresh', async t => {
  const pending = deferred(), refreshed = []
  const { ui, calls, closes } = await mount(t, { refresh: async () => { refreshed.push(true); await pending.promise } })
  const input = ui.find(node => node.props.value === 'images.example')
  input.props['onUpdate:modelValue'](['cdn.example']); await flush()
  const applying = button(ui, '确认添加').props.onClick(); await flush()
  assert.deepEqual(calls[1].body, { hosts: ['cdn.example'], expected_fingerprint: 'f'.repeat(64) })
  assert.ok(calls[1].path.endsWith('/allow-hosts'))
  assert.equal(refreshed.length, 1)
  assert.equal(closes.length, 0)
  assert.equal(button(ui, '关闭').props.disabled, true)
  pending.resolve(); await applying; await flush()
  assert.equal(closes.length, 1)
})

test('a saved configuration whose refresh fails offers read retry without another write', async t => {
  let attempts = 0
  const { ui, calls } = await mount(t, { refresh: async () => { if (++attempts === 1) throw new Error('private-url') } })
  await click(ui, '确认添加')
  assert.ok(ui.find(node => node.props['data-online-host-saved'] !== undefined))
  assert.match(ui.text(), /不必重复添加/)
  assert.ok(button(ui, '关闭'))
  assert.equal(button(ui, '暂不添加'), undefined, 'closing after a saved change cannot suggest declining it')
  assert.equal(ui.text().includes('private-url'), false)
  await click(ui, '重新加载详情')
  assert.equal(calls.filter(call => call.path.endsWith('/allow-hosts')).length, 1)
  assert.equal(attempts, 2)
})

test('configuration conflict clears the old fingerprint and requires a fresh check', async t => {
  let checks = 0
  const { ui, calls } = await mount(t, { request: async path => {
    if (path.endsWith('/allow-hosts')) throw { code: 'online_source_changed', status: 409, message: 'private-url' }
    return { ...report(), configuration_fingerprint: (++checks === 1 ? 'a' : 'b').repeat(64) }
  } })
  await click(ui, '确认添加')
  assert.equal(ui.find(node => node.props['data-online-host-candidate'] !== undefined), undefined)
  assert.match(ui.text(), /重新检查后再确认/)
  await click(ui, '重新检查')
  await click(ui, '确认添加')
  assert.deepEqual(calls.filter(call => call.path.endsWith('/allow-hosts')).map(call => call.body.expected_fingerprint), ['a'.repeat(64), 'b'.repeat(64)])
})

test('a protected page waits for an explicit authorization action and refreshes stale management state', async t => {
  const { ui, calls, auth, viewing } = await mount(t, { session: { enabled: true },
    authorize: async state => { state.management_unlocked = true; return true } })
  assert.equal(calls.length, 0)
  assert.equal(auth.length, 0)
  await click(ui, '验证并检查')
  assert.deepEqual(auth, ['refresh', 'authorize'])
  assert.equal(viewing.management_unlocked, true)
  assert.equal(calls.length, 1)
})

test('cancelling authorization leaves the original confirmation visible and does not inspect', async t => {
  const { ui, calls, closes } = await mount(t, { session: { enabled: true }, authorize: async () => false })
  await click(ui, '验证并检查')
  assert.equal(calls.length, 0)
  assert.ok(button(ui, '验证并检查'))
  assert.equal(closes.length, 0)
})

test('unmount cancels metadata reads and late success never becomes a confirmation', async t => {
  const pending = deferred()
  const { ui, calls, unmount } = await mount(t, { request: () => pending.promise })
  unmount()
  assert.equal(calls[0].signal.aborted, true)
  pending.resolve(report()); await flush()
  assert.equal(ui.find(node => node.props['data-online-host-candidate'] !== undefined), undefined)
})

test('a late save for an obsolete detail never refreshes or closes a new detail', async t => {
  const pending = deferred(); let active = true, refreshes = 0
  const { ui, closes } = await mount(t, { current: () => active,
    request: path => path.endsWith('/allow-hosts') ? pending.promise : report(), refresh: async () => { refreshes++ } })
  const applying = button(ui, '确认添加').props.onClick(); await flush()
  active = false; pending.resolve({}); await applying; await flush()
  assert.equal(refreshes, 0)
  assert.equal(closes.length, 0)
})

test('candidate validation and error copy never reflect upstream URLs', () => {
  assert.equal(validOnlineHostReport(report(), SOURCE, 'show-1'), true)
  for (const invalid of [{ source_id: 'other' }, { external_id: 'other' }, { missing_hosts: ['https://private/?sig=secret'] },
    { missing_hosts: ['cdn.example', 'cdn.example'] }, { configuration_fingerprint: 'bad' }]) {
    assert.equal(validOnlineHostReport({ ...report(), ...invalid }, SOURCE, 'show-1'), false)
  }
  assert.equal(onlineHostError({ message: 'https://private/?sig=secret' }).includes('secret'), false)
})

test('reminders stay bounded and suppress a dismissed item until its configuration or scope changes', () => {
  const reminders = createOnlineHostReminders()
  assert.equal(reminders.claim('scope/item/config-a'), true)
  assert.equal(reminders.claim('scope/item/config-a'), false)
  assert.equal(reminders.claim('scope/item/config-b'), true)
  reminders.remember('scope/item/config-b')
  assert.equal(reminders.claim('scope/item/config-b'), false)
  for (let index = 0; index < 16; index++) reminders.claim('other/' + index)
  assert.equal(reminders.claim('scope/item/config-a'), true)
  reminders.clear()
  assert.equal(reminders.claim('scope/item/config-a'), true)
})
