import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import * as cache from '../src/onlineCache.js'
import { onlineArtworkPath } from '../src/onlineArtwork.js'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, deferred, nodeText } from './helpers/renderHarness.js'

const SOURCE = 'src_' + '1'.repeat(32), ITEM = 'itm_' + '2'.repeat(32), LINE = 'ln_' + '3'.repeat(32)
const identity = number => ({ source_id: SOURCE, item_id: ITEM, line_id: LINE, kind: 'episode', episode_id: 'ep_' + number.toString(16).padStart(32, '0') })
const entry = (number, state = 'ready') => ({ id: 'cache_' + number, task_id: 'cache_' + number, identity: identity(number), label: `第${number}集`, line_label: '高清', state,
  downloaded_bytes: 1024, total_bytes: 2048, completed_resources: 1, total_resources: 2, speed_bytes_per_second: 512, error_code: '' })
const show = count => ({ source: { id: SOURCE, name: '合成来源' }, item: { id: ITEM, title: '合成长剧', kind: 'tv' },
  lines: [{ id: LINE, label: '高清', entries: Array.from({ length: count }, (_, index) => ({ label: `第${index + 1}集`, supported: true, identity: identity(index + 1) })) }] })
const list = entries => ({ enabled: true, storage: { used_bytes: 1024, available_bytes: 1024 ** 3, min_free_bytes: 1024 ** 2, ok: true },
  items: [{ item_id: ITEM, source_id: SOURCE, title: '合成长剧', source_name: '合成来源', ready_count: entries.filter(value => value.state === 'ready').length, total_count: entries.length, entries }] })

test('cache status, progress and artwork keep opaque local identities and unknown totals', () => {
  assert.equal(cache.onlineCacheState('paused'), '已暂停')
  assert.equal(cache.onlineCacheBytes(1024 ** 3), '1.0 GiB')
  assert.equal(cache.onlineCacheBytes(null), '未知')
  assert.equal(cache.onlineCacheProgress(entry(1)), 1)
  assert.equal(cache.onlineCacheProgress(entry(1, 'downloading')), .5)
  assert.equal(cache.onlineCacheProgress({ state: 'downloading', downloaded_bytes: 50, total_bytes: null, total_resources: 0 }), null)
  assert.equal(cache.onlineCachedEntry([entry(1)], { ...identity(1), source_id: 'src_' + '4'.repeat(32) }), null)
  assert.equal(cache.onlineCachedEntry([entry(1)], identity(1)).id, 'cache_1')
  assert.equal(onlineArtworkPath('/api/online/cache/artwork/' + ITEM), '/api/online/cache/artwork/' + ITEM)
  assert.equal(onlineArtworkPath('/api/online/cache/artwork/' + ITEM + '/../../file'), '')
  assert.match(cache.onlineCacheError('online_cache_corrupt'), /损坏/)
})

test('cache client replaces reads, treats only lookup 404 as a miss and cancels late authorization', async () => {
  const pending = deferred(), permission = deferred(), calls = []
  const client = cache.createOnlineCacheClient({ authorize: () => permission.promise, request: async (path, options) => {
    calls.push({ path, options })
    if (path.includes('/lookup?')) throw { status: 404 }
    if (path.endsWith('/items')) return pending.promise
    return { tasks: [] }
  } })
  const old = client.list()
  assert.equal(await client.lookup(SOURCE, 'show'), null)
  pending.resolve(list([entry(1)])); assert.equal(await old, null)
  const mutation = client.enqueue({ source_id: SOURCE, item_id: ITEM, line_id: LINE, episode_ids: [identity(2).episode_id] })
  client.close(); permission.resolve(true)
  assert.equal(await mutation, null)
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 0)
  const denied = cache.createOnlineCacheClient({ request: async () => { throw { status: 403, code: 'online_access_revoked' } } })
  await assert.rejects(denied.lookup(SOURCE, 'show'), error => error.status === 403)
  denied.close()
})

test('cache responses cannot substitute another source or item under the requested identity', async () => {
  const wrong = 'itm_' + '7'.repeat(32)
  const client = cache.createOnlineCacheClient({ request: async () => ({ detail: show(2), entries: [entry(1)] }) })
  await assert.rejects(client.detail(wrong), /invalid cache response/)
  await assert.rejects(client.lookup('src_' + '8'.repeat(32), 'show'), /invalid cache response/)
  client.close()
})

async function component(t, name, props, request, events = {}) {
  const viewing = Vue.reactive({ enabled: false, management_unlocked: false, prompt: null })
  const vue = { ...Vue, vModelCheckbox: {}, vModelSelect: {} }
  const dialog = { default: { props: ['title', 'busy', 'focusActive'], emits: ['close'], setup: (_props, { slots }) => () => Vue.h('section', [...(slots.default?.() || []), ...(slots.footer?.() || [])]) } }
  const target = await loadSfc(new URL(`../src/components/${name}.vue`, import.meta.url), {
    vue, '../viewing.js': { viewing, refreshViewing: async () => viewing, ensureManagement: async () => true },
    '../onlineCache.js': { ...cache, createOnlineCacheClient: options => cache.createOnlineCacheClient({ ...options, request }) },
    './JzDialog.vue': dialog,
    './OnlineArtwork.vue': { default: { setup: () => () => Vue.h('div') } },
  })
  const ui = renderHarness(target, { ...props, ...events })
  t.after(() => ui.app.unmount())
  await flush()
  return ui
}
const button = (ui, text) => ui.find(node => node.type === 'button' && nodeText(node) === text)

test('cache dialog selects across 50-entry groups and submits the exact original episode IDs', async t => {
  const detail = show(123), calls = [], created = []
  const ui = await component(t, 'OnlineCacheDialog', { detail, line: detail.lines[0], entries: [entry(1)] }, async (path, options) => {
    calls.push({ path, body: JSON.parse(options.body) }); return { tasks: [entry(2, 'queued')] }
  }, { onCreated: value => created.push(value) })
  assert.equal(ui.all().filter(node => node.props['data-online-cache-select'] !== undefined).length, 50)
  assert.equal(ui.find(node => node.props['data-online-cache-select'] === 0).props.disabled, true)
  ui.find(node => node.props['data-online-cache-select'] === 1).props['onUpdate:modelValue']([identity(2).episode_id])
  ui.find(node => node.props['data-online-cache-group'] !== undefined).props['onUpdate:modelValue'](2); await flush()
  assert.equal(ui.all().filter(node => node.props['data-online-cache-select'] !== undefined).length, 23)
  ui.find(node => node.props['data-online-cache-select'] === 122).props['onUpdate:modelValue']([identity(2).episode_id, identity(123).episode_id]); await flush()
  await ui.find(node => node.props['data-online-cache-submit'] !== undefined).props.onClick(); await flush()
  assert.deepEqual(calls[0].body, { source_id: SOURCE, item_id: ITEM, line_id: LINE, episode_ids: [identity(2).episode_id, identity(123).episode_id] })
  assert.equal(created.length, 1)
  button(ui, '选择整剧').props.onClick(); await flush()
  assert.match(ui.text(), /已选 122 集/)
})

test('cache list pauses and resumes a task, preserves partial delete failures and waits for confirmation', async t => {
  let rows = [entry(1), entry(2, 'paused'), entry(3, 'failed')]
  rows[2].error_code = 'online_cache_corrupt'
  const writes = []
  const ui = await component(t, 'OnlineCacheView', {}, async (path, options) => {
    if (!options.method) return list(rows)
    const body = JSON.parse(options.body); writes.push({ path, body })
    if (path.endsWith('/actions')) { rows = rows.map(row => row.id === 'cache_2' ? { ...row, state: body.action === 'resume' ? 'paused' : 'queued' } : row); return { ok: true } }
    if (path.endsWith('/delete')) { rows = rows.filter(row => row.id !== 'cache_2'); return { results: body.ids.map(id => ({ id, deleted: id === 'cache_2', error_code: id === 'cache_2' ? '' : 'online_cache_in_use' })) } }
    throw new Error('unexpected request')
  })
  assert.match(ui.text(), /1.0 KiB/)
  assert.match(ui.text(), /损坏/)
  await button(ui, '继续缓存').props.onClick(); await flush()
  assert.equal(writes[0].body.action, 'resume')
  button(ui, '删除整剧').props.onClick(); await flush()
  assert.equal(writes.length, 1)
  assert.match(ui.text(), /其他设备也会失去/)
  assert.match(ui.text(), /共 3 集，占用 3.0 KiB/)
  assert.match(ui.text(), /停止相关设备的播放/)
  await ui.find(node => node.props['data-online-cache-delete-confirm'] !== undefined).props.onClick(); await flush()
  assert.equal(writes.length, 2)
  assert.deepEqual(writes[1].body.ids, ['cache_1', 'cache_2', 'cache_3'])
  assert.match(ui.text(), /正在播放/)
  assert.equal(ui.find(node => node.props['data-online-cache-entry'] === 'cache_2'), undefined)
  assert.ok(ui.find(node => node.props['data-online-cache-delete-confirm'] !== undefined))
  assert.match(ui.text(), /共 2 集，占用 2.0 KiB/)
})

test('disabling online playback keeps cache inspection, pause and confirmed deletion available', async t => {
  const ui = await component(t, 'OnlineCacheView', {}, async () => ({ ...list([entry(1), entry(2, 'paused'), entry(3, 'failed'), entry(4, 'downloading')]), enabled: false }))
  assert.match(ui.text(), /在线点播已关闭，可删除缓存/)
  assert.equal(button(ui, '播放').props.disabled, true)
  assert.equal(button(ui, '继续缓存').props.disabled, true)
  assert.equal(button(ui, '重试').props.disabled, true)
  assert.equal(button(ui, '暂停').props.disabled, false)
  assert.equal(button(ui, '删除整剧').props.disabled, false)
  button(ui, '删除整剧').props.onClick(); await flush()
  assert.ok(ui.find(node => node.props['data-online-cache-delete-confirm'] !== undefined))
})
