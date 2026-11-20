import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, nodeText, flush, deferred } from './helpers/renderHarness.js'

const SOURCE = 'src_' + '1'.repeat(32), PROFILE = 'np_' + '2'.repeat(32)
const BASELINE = 'a'.repeat(64), PREVIEW = 'b'.repeat(64)
const inherit = () => ({ mode: 'inherit', profile_id: null })
const policy = () => Object.fromEntries(['default', 'api', 'media', 'image', 'subtitle', 'subscription'].map(key => [key, inherit()]))
const element = (ui, id) => ui.find(node => node.props.id === id)
const button = (ui, name) => ui.find(node => node.type === 'button' && (node.props['aria-label'] || nodeText(node).trim()) === name)
async function click(ui, name) { assert.ok(button(ui, name), 'Missing button: ' + name); await button(ui, name).props.onClick(); await flush() }
async function field(ui, id, value) { element(ui, id).props['onUpdate:modelValue'](value); await flush() }
async function submit(ui, attr) { await ui.find(node => node.type === 'form' && attr in node.props).props.onSubmit({ preventDefault() {} }); await flush() }

async function mount(t, { sourcePolicy = policy(), profiles = [], templatesFail = false, policyFailures = 0, catalogGate = null,
  routeQuery = {}, hostCheckGate = null, hostApplyGate = null, hostApplyFailures = 0, hostReport = null, extraSources = [], profileGate = null,
  readSource, previewMigration, patchSource } = {}) {
  const requests = [], source = { id: SOURCE, name: '演示来源', provider: 'maccms', api_base_url: 'https://api.example/',
    allowed_hosts: ['api.example'], enabled: true, priority: 100, provider_options: {} }
  const sourceList = [source, ...extraSources.map(item => ({ ...source, ...item }))]
  const route = Vue.reactive({ query: { sec: 'sec-online', ...routeQuery } })
  let draft
  const api = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body)
    requests.push({ path, method: options.method || 'GET', body, signal: options.signal })
    if (path === '/api/online/settings') return { enabled: true }
    if (path === '/api/online/sources') return options.method === 'POST' ? { ...body, id: SOURCE } : { items: sourceList }
    if (path.endsWith('/host-migration-preview')) return previewMigration ? previewMigration(body) : { expected_fingerprint: PREVIEW, changes: [] }
    if (path === '/api/online/sources/' + SOURCE) {
      if (options.method === 'PATCH') return patchSource ? patchSource(body) : { ...source, ...body }
      return readSource ? readSource(source) : { ...source, configuration_fingerprint: BASELINE }
    }
    if (path.endsWith('/host-check')) return hostCheckGate ? hostCheckGate.promise : hostReport || {
      source_id: path.split('/')[4], external_id: body.external_id,
      missing_hosts: ['cdn.extra.example', 'images.extra.example'], allowed_hosts: ['api.example'],
      rejected_count: 1, configuration_fingerprint: 'f'.repeat(64),
    }
    if (path.endsWith('/allow-hosts')) {
      if (hostApplyGate) await hostApplyGate.promise
      if (hostApplyFailures-- > 0) throw Object.assign(new Error('private source details'), { status: 409, code: 'online_source_changed' })
      source.allowed_hosts = [...new Set([...source.allowed_hosts, ...body.hosts])]
      return { ...source }
    }
    if (path === '/api/online/network/profiles') {
      if (options.method === 'POST') return { id: PROFILE, name: body.name, mode: body.mode, proxy_url: body.proxy_url, has_credentials: !!(body.username || body.password) }
      return { items: profiles }
    }
    if (path === '/api/online/network/profiles/' + PROFILE) {
      if (profileGate) await profileGate.promise
      return { ...profiles.find(item => item.id === PROFILE), ...body }
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
    vue: { ...Vue, vModelText: {}, vModelSelect: {}, vModelCheckbox: {}, vShow: {} },
    'vue-router': { useRoute: () => route },
    '../api.js': { api }, '../settingsDrafts.js': { useSettingsDraft(entry) { draft = entry } },
  })
  const ui = renderHarness(component)
  t.after(() => ui.app.unmount())
  await flush()
  return { ui, requests, route, get draft() { return draft } }
}

const renamedHosts = [
  { kind: 'api', old_host: 'api.example', new_host: 'new.example', notice_url: '' },
  { kind: 'media', old_host: 'old-cdn.example', new_host: 'new-cdn.example', notice_url: 'https://notice.example/move' },
]
const choices = ui => ui.all().filter(node => node.type === 'input' && renamedHosts.some(change => change.old_host === node.props.value))
const renamedDraft = source => ({ ...source, allowed_hosts: ['api.example', 'old-cdn.example', 'keep.example'], configuration_fingerprint: BASELINE })

test('source directory help is available while listing, adding and editing', async t => {
  const { ui } = await mount(t)
  function helpVisible() {
    const link = ui.find(node => node.type === 'a' && node.props.href === '/help/user-guide/watch-online.html#online-source-directory')
    assert.ok(link)
    assert.match(nodeText(link), /去哪里找视频源？/)
  }
  helpVisible()
  await click(ui, '添加来源'); helpVisible()
  await click(ui, '取消')
  await click(ui, '编辑 演示来源'); helpVisible()
})

test('editing previews a debounced host migration and saves the draft and selected choices in one patch', async t => {
  const { ui, requests } = await mount(t, { readSource: renamedDraft, previewMigration: body => ({
    expected_fingerprint: PREVIEW, changes: body.api_base_url.startsWith('https://new.example/') ? renamedHosts : [],
  }) })
  t.mock.timers.enable({ apis: ['setTimeout'] })
  await click(ui, '编辑 演示来源')
  await field(ui, 'online-source-api', 'https://n.example/')
  await field(ui, 'online-source-api', 'https://new.example/')
  await submit(ui, 'data-online-source-form')
  assert.equal(button(ui, '保存来源').props.disabled, true)
  assert.equal(requests.some(call => call.method === 'PATCH'), false)
  t.mock.timers.tick(250); await flush()
  const previews = requests.filter(call => call.path.endsWith('/host-migration-preview'))
  assert.equal(previews.length, 2)
  assert.deepEqual(previews.at(-1).body, { api_base_url: 'https://new.example/', expected_fingerprint: BASELINE,
    allowed_hosts: ['api.example', 'old-cdn.example', 'keep.example'] })
  assert.equal(choices(ui).length, 2)
  assert.match(ui.text(), /新的接口地址始终生效/)
  assert.ok(ui.find(node => node.type === 'a' && node.props.href === 'https://notice.example/move'))
  choices(ui)[0].props['onUpdate:modelValue'](['old-cdn.example'])
  await field(ui, 'online-source-hosts', 'api.example\nold-cdn.example\nkeep.example\ncustom.example')
  t.mock.timers.tick(250); await flush()
  await submit(ui, 'data-online-source-form')
  const patch = requests.find(call => call.method === 'PATCH')
  assert.deepEqual(patch.body.host_migration, { expected_fingerprint: PREVIEW, selected_old_hosts: ['old-cdn.example'] })
  assert.deepEqual(patch.body.allowed_hosts, ['api.example', 'old-cdn.example', 'keep.example', 'custom.example'])
  assert.equal(patch.body.api_base_url, 'https://new.example/')
})

test('closing drafts aborts both snapshot and preview requests and ignores late replies', async t => {
  const snapshot = deferred(), preview = deferred()
  let reads = 0
  const { ui, requests, draft } = await mount(t, { readSource: source => ++reads === 1 ? snapshot.promise : renamedDraft(source),
    previewMigration: () => preview.promise })
  const opening = button(ui, '编辑 演示来源').props.onClick(); await flush()
  await click(ui, '返回视频源列表')
  assert.equal(requests.find(call => call.path.endsWith(SOURCE)).signal.aborted, true)
  snapshot.resolve({}); await opening
  await click(ui, '编辑 演示来源')
  const read = requests.find(call => call.path.endsWith('/host-migration-preview'))
  await field(ui, 'online-source-api', 'https://new.example/')
  assert.equal(read.signal.aborted, true)
  draft.discard(); await flush()
  preview.resolve({ expected_fingerprint: PREVIEW, changes: renamedHosts }); await flush()
  assert.equal(choices(ui).length, 0)
  assert.equal(requests.some(call => call.method === 'PATCH'), false)
})

test('a failed preview keeps the draft editable and retries without saving', async t => {
  let attempts = 0
  const { ui, requests } = await mount(t, { previewMigration() {
    if (++attempts === 1) throw Object.assign(new Error('private response'), { status: 503 })
    return { expected_fingerprint: PREVIEW, changes: [] }
  } })
  await click(ui, '编辑 演示来源')
  assert.match(ui.text(), /无法预览主机变更/)
  assert.equal(ui.text().includes('private response'), false)
  await field(ui, 'online-source-name', '暂存名字')
  await submit(ui, 'data-online-source-form')
  assert.equal(requests.some(call => call.method === 'PATCH'), false)
  await click(ui, '重新预览')
  assert.equal(button(ui, '保存来源').props.disabled, false)
  await submit(ui, 'data-online-source-form')
  assert.equal(requests.find(call => call.method === 'PATCH').body.name, '暂存名字')
})

for (const failureAt of ['preview', 'save']) {
  test('a concurrent edit during ' + failureAt + ' preserves the draft until an explicit reload', async t => {
    let revision = 0
    const stale = () => { throw Object.assign(new Error('private details'), { status: 409 }) }
    const { ui, requests } = await mount(t, {
      readSource: source => ({ ...renamedDraft(source), name: revision ? '其他管理员保存的名字' : source.name,
        configuration_fingerprint: revision ? 'c'.repeat(64) : BASELINE }),
      previewMigration: body => failureAt === 'preview' && body.api_base_url === 'https://new.example/'
        ? stale() : { expected_fingerprint: PREVIEW, changes: body.api_base_url === 'https://new.example/' ? renamedHosts : [] },
      patchSource: stale,
    })
    t.mock.timers.enable({ apis: ['setTimeout'] })
    await click(ui, '编辑 演示来源')
    await field(ui, 'online-source-name', '我的草稿')
    await field(ui, 'online-source-api', 'https://new.example/')
    t.mock.timers.tick(250); await flush()
    if (failureAt === 'save') {
      await submit(ui, 'data-online-source-form')
      assert.deepEqual(requests.find(call => call.method === 'PATCH').body.host_migration.selected_old_hosts, ['api.example', 'old-cdn.example'])
    }
    assert.match(ui.text(), /来源已被其他操作修改/)
    assert.equal(button(ui, '保存来源').props.disabled, true)
    assert.equal(requests.filter(call => call.method === 'PATCH').length, failureAt === 'save' ? 1 : 0)
    revision++
    await click(ui, '放弃草稿并重新加载')
    assert.match(ui.text(), /其他管理员保存的名字/)
    assert.equal(requests.filter(call => call.path.endsWith('/host-migration-preview')).at(-1).body.expected_fingerprint, 'c'.repeat(64))
    assert.equal(choices(ui).length, 0)
    assert.equal(button(ui, '保存来源').props.disabled, false)
  })
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

test('a detail deep link opens an explicit host review and atomically appends only selected hosts', async t => {
  const { ui, requests } = await mount(t, { routeQuery: { source: SOURCE, item: 'movie-7' } })
  assert.ok(element(ui, 'online-host-item'))
  assert.equal(requests.some(call => call.path.endsWith('/host-check')), false, 'a link never silently reads the upstream')
  await submit(ui, 'data-online-host-form')
  assert.deepEqual(requests.find(call => call.path.endsWith('/host-check')).body, { external_id: 'movie-7' })
  assert.equal(ui.text().includes('cdn.extra.example'), true)
  assert.equal(ui.text().includes('images.extra.example'), true)
  assert.equal(requests.some(call => call.path.endsWith('/allow-hosts')), false, 'inspection never silently trusts new hosts')
  const choice = ui.find(node => node.type === 'input' && node.props.value === 'cdn.extra.example')
  choice.props['onUpdate:modelValue'](['cdn.extra.example']); await flush()
  await click(ui, '加入允许列表')
  const saved = requests.find(call => call.path.endsWith('/allow-hosts'))
  assert.deepEqual(saved.body, { hosts: ['cdn.extra.example'], expected_fingerprint: 'f'.repeat(64) })
  assert.equal(requests.some(call => call.method === 'PATCH'), false, 'host repair does not replace the source config')
  assert.equal(ui.text().includes('媒体主机已加入允许列表'), true)
  assert.equal(ui.text().includes('返回影片详情'), true)
})

test('a stale source fingerprint clears the old proposal and requires a fresh inspection', async t => {
  const { ui, requests } = await mount(t, { routeQuery: { source: SOURCE, item: 'movie-7' }, hostApplyFailures: 1 })
  await submit(ui, 'data-online-host-form')
  await click(ui, '加入允许列表')
  assert.equal(ui.text().includes('来源配置已变化，请重新检查后再添加。'), true)
  assert.equal(ui.text().includes('private source details'), false)
  assert.equal(button(ui, '加入允许列表'), undefined)
  await submit(ui, 'data-online-host-form')
  await click(ui, '加入允许列表')
  assert.equal(requests.filter(call => call.path.endsWith('/host-check')).length, 2)
  assert.equal(ui.text().includes('媒体主机已加入允许列表'), true)
})

test('closing a pending host inspection aborts it and discards a late report', async t => {
  const gate = deferred()
  t.after(() => gate.resolve({}))
  const { ui, requests } = await mount(t, { routeQuery: { source: SOURCE, item: 'movie-7' }, hostCheckGate: gate })
  const checking = ui.find(node => node.type === 'form' && 'data-online-host-form' in node.props).props.onSubmit({ preventDefault() {} })
  await flush()
  await click(ui, '关闭检查')
  assert.equal(requests.find(call => call.path.endsWith('/host-check')).signal.aborted, true)
  gate.resolve({ source_id: SOURCE, external_id: 'movie-7', missing_hosts: ['late.example'], allowed_hosts: [],
    rejected_count: 0, configuration_fingerprint: 'f'.repeat(64) })
  await checking; await flush()
  assert.equal(ui.text().includes('late.example'), false)
  assert.equal(ui.find(node => node.props['data-online-host-check'] !== undefined), undefined)
})

test('an invalid host response cannot expose a signed provider URL in the review', async t => {
  const { ui } = await mount(t, { routeQuery: { source: SOURCE, item: 'movie-7' }, hostReport: {
    source_id: SOURCE, external_id: 'movie-7', missing_hosts: ['https://cdn.example/v.mp4?private-token=secret'],
    allowed_hosts: [], rejected_count: 0, configuration_fingerprint: 'f'.repeat(64),
  } })
  await submit(ui, 'data-online-host-form')
  assert.equal(ui.text().includes('private-token'), false)
  assert.equal(ui.text().includes('媒体主机检查未完成'), true)
  assert.equal(button(ui, '加入允许列表'), undefined)
})

test('host approval is a busy write that blocks other source editors until it finishes', async t => {
  const gate = deferred()
  t.after(() => gate.resolve())
  const { ui, draft } = await mount(t, { routeQuery: { source: SOURCE, item: 'movie-7' }, hostApplyGate: gate })
  await submit(ui, 'data-online-host-form')
  const applying = button(ui, '加入允许列表').props.onClick(); await flush()
  assert.equal(draft.busy(), true)
  assert.equal(button(ui, '关闭检查').props.disabled, true)
  assert.equal(button(ui, '编辑 演示来源').props.disabled, true)
  gate.resolve(); await applying; await flush()
  assert.equal(draft.busy(), false)
})

test('a proxy draft protects the untouched source form and participates in settings discard', async t => {
  const { ui, requests, draft } = await mount(t, { profiles: [{ id: PROFILE, mode: 'proxy', name: '已有代理', proxy_url: 'http://proxy.example:7890' }] })
  await click(ui, '编辑 演示来源')
  await click(ui, '编辑代理 已有代理')
  await field(ui, 'online-profile-host', 'new-proxy.example')
  assert.equal(draft.dirty(), true)
  assert.equal(element(ui, 'online-source-name').props.disabled, true)
  assert.equal(button(ui, '保存来源').props.disabled, true)
  await submit(ui, 'data-online-source-form')
  assert.equal(requests.some(call => call.method === 'PATCH'), false)
  draft.discard(); await flush()
  assert.equal(draft.dirty(), false)
  assert.equal(element(ui, 'online-profile-host'), undefined)
})

test('a pending proxy edit blocks host approval and cannot leave host actions permanently busy', async t => {
  const gate = deferred(); t.after(() => gate.resolve())
  const { ui, requests, draft } = await mount(t, { profileGate: gate, routeQuery: { source: SOURCE, item: 'movie-7' },
    profiles: [{ id: PROFILE, mode: 'proxy', name: '已有代理', proxy_url: 'http://proxy.example:7890' }] })
  await submit(ui, 'data-online-host-form')
  await click(ui, '编辑代理 已有代理')
  await field(ui, 'online-profile-host', 'new-proxy.example')
  const saving = ui.find(node => node.type === 'form' && 'data-online-proxy-form' in node.props).props.onSubmit({ preventDefault() {} })
  await flush()
  assert.equal(draft.busy(), true)
  assert.equal(button(ui, '加入允许列表').props.disabled, true)
  await click(ui, '加入允许列表')
  assert.equal(requests.some(call => call.path.endsWith('/allow-hosts')), false)
  gate.resolve(); await saving; await flush()
  assert.equal(draft.busy(), false)
  assert.equal(button(ui, '关闭检查').props.disabled, false)
})
