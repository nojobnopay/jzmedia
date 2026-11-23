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
  readSource, previewMigration, patchSource, discoveryApi, onlineEnabled = true } = {}) {
  const requests = [], source = { id: SOURCE, name: '演示来源', provider: 'maccms', api_base_url: 'https://api.example/',
    allowed_hosts: ['api.example'], enabled: true, priority: 100, provider_options: {} }
  const sourceList = [source, ...extraSources.map(item => ({ ...source, ...item }))]
  const route = Vue.reactive({ query: { sec: 'sec-online', ...routeQuery } })
  let draft
  const api = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body)
    requests.push({ path, method: options.method || 'GET', body, signal: options.signal, timeout: options.timeout, headers: options.headers })
    if (path === '/api/online/settings') return { enabled: onlineEnabled }
    if (discoveryApi && path.startsWith('/api/online/discovery')) return discoveryApi(path, body)
    if (path === '/api/online/sources') return options.method === 'POST' ? { ...body, id: SOURCE } : { items: sourceList }
    if (path.endsWith('/host-migration-preview')) return previewMigration ? previewMigration(body) : { expected_fingerprint: PREVIEW, changes: [] }
    if (path.endsWith('/replace-catalogue')) return { previous: { ...source, name: source.name + '（旧片库）', enabled: false },
      source: { ...source, ...body, id: 'src_' + 'f'.repeat(32) } }
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
  await field(ui, 'online-catalogue-change', 'mirror')
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

test('changing to a different catalogue requires a choice for the current URL and retains both sources', async t => {
  const { ui, requests } = await mount(t)
  t.mock.timers.enable({ apis: ['setTimeout'] })
  await click(ui, '编辑 演示来源')
  await field(ui, 'online-source-api', 'https://different.example/')
  await field(ui, 'online-catalogue-change', 'replace')
  await field(ui, 'online-source-api', 'https://confirmed.example/')
  t.mock.timers.tick(250); await flush()
  assert.equal(button(ui, '保存来源').props.disabled, true)
  await submit(ui, 'data-online-source-form')
  assert.equal(requests.some(row => row.path.endsWith('/replace-catalogue')), false)
  await field(ui, 'online-catalogue-change', 'replace')
  await submit(ui, 'data-online-source-form')
  const saved = requests.find(row => row.path.endsWith('/replace-catalogue'))
  assert.equal(saved.method, 'POST')
  assert.equal(saved.body.api_base_url, 'https://confirmed.example/')
  assert.equal(saved.body.host_migration.expected_fingerprint, PREVIEW)
  assert.ok(button(ui, '编辑 演示来源（旧片库）')); assert.ok(button(ui, '编辑 演示来源'))
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
    await field(ui, 'online-catalogue-change', 'mirror')
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
  const check = requests.find(call => call.path.endsWith('/host-check'))
  assert.deepEqual(check.body, { external_id: 'movie-7' })
  assert.deepEqual(check.headers, { 'X-Online-Host-Report': '2' })
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

test('source settings inspect a programme without typing and identify just the poster permission', async t => {
  const report = { source_id: SOURCE, external_id: 'example-42', title: '首页样片',
    missing_hosts: ['cdn.extra.example', 'poster.example'], missing_poster_hosts: ['poster.example'],
    allowed_hosts: ['api.example'], rejected_count: 0, configuration_fingerprint: 'f'.repeat(64) }
  const { ui, requests } = await mount(t, { hostReport: report })
  await click(ui, '检查媒体主机 演示来源')
  assert.deepEqual(requests.filter(call => call.path.endsWith('/host-check')).map(call => call.body), [{}])
  assert.match(ui.text(), /本次检查《首页样片》/)
  assert.match(ui.text(), /poster\.example（海报）/)
  assert.equal(requests.some(call => call.path.endsWith('/allow-hosts')), false)
  await click(ui, '加入允许列表')
  assert.deepEqual(requests.find(call => call.path.endsWith('/allow-hosts')).body.hosts, ['poster.example'])
  assert.match(ui.text(), /媒体主机已加入允许列表/)
  const link = ui.find(node => 'data-online-host-return' in node.props)
  assert.equal(nodeText(link), '查看节目详情')
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

const discoveryItem = id => ({ id, name: id, api_base_url: 'https://' + id + '.example/api',
  origin: '公开目录', source_id: null, enabled: false })
const measured = (id, kbps) => ({ id, status: 'measured', catalog_ms: 50, media_ms: 100,
  sample_kbps: kbps, allowed_hosts: ['media-' + id + '.example'], probe_token: 'receipt-' + id })

test('discovery samples one source at a time and ranks playback headroom before raw throughput', async t => {
  const items = ['alpha', 'beta', 'gamma', 'expensive', 'realtime', 'slowest'].map(discoveryItem)
  const slow = deferred()
  const { ui, requests } = await mount(t, { discoveryApi(path, body) {
    if (path === '/api/online/discovery/refresh') return { items }
    if (path.endsWith('/alpha/probe')) return slow.promise
    if (path.endsWith('/beta/probe')) return measured('beta', 9000)
    if (path.endsWith('/realtime/probe')) return { ...measured('realtime', 5000), realtime_ratio: 1 }
    if (path.endsWith('/expensive/probe')) return { ...measured('expensive', 5500), realtime_ratio: 0.7 }
    if (path.endsWith('/slowest/probe')) return { ...measured('slowest', 6000), realtime_ratio: 0.5 }
    if (path.endsWith('/gamma/probe')) return { id: 'gamma', status: 'catalog_only', reason: 'manifest_limit', catalog_ms: 1, probe_token: 'catalog',
      attempts: [{ stage: 'manifest', reason: 'manifest_limit' }, { stage: 'manifest', reason: 'manifest_limit' }, { stage: 'media', reason: 'provider_timeout' }] }
    if (path.endsWith('/alpha/enable')) {
      assert.deepEqual(body, { probe_token: 'receipt-alpha' })
      return { ...items[0], enabled: true, id: 'src_new', provider: 'maccms', priority: 100 }
    }
    throw new Error('Unexpected request')
  } })
  assert.ok(!requests.some(r => r.path.includes('/discovery')))
  await click(ui, '发现并探测来源')
  assert.match(ui.text(), /正在探测 · 0 \/ 6 · 排队 5/)
  assert.equal(requests.filter(r => r.path.endsWith('/probe')).length, 1)
  assert.equal(button(ui, '启用来源 alpha').props.disabled, true)
  slow.resolve({ ...measured('alpha', 3000), realtime_ratio: 1.24,
    attempts: [{ stage: 'entry', reason: 'target_dns_failed' }, { stage: 'media', reason: 'ok' }] }); await flush()
  assert.deepEqual(ui.all().filter(n => 'data-discovery-candidate' in n.props).map(n => n.props['data-discovery-candidate']),
    ['alpha', 'realtime', 'beta', 'expensive', 'slowest', 'gamma'])
  assert.match(ui.text(), /探测完成 · 6 \/ 6/)
  assert.match(ui.text(), /本轮优先试用/)
  assert.match(ui.text(), /取样速度低于该片段播放所需（0.7 倍）/)
  const result = id => nodeText(ui.find(n => n.props['data-discovery-candidate'] === id))
  assert.match(result('gamma'), /仅目录可读 · 目录读取 1 ms/)
  assert.match(result('gamma'), /已试 3 个样片，播放清单 · 大小或条目数超限（2 次），视频数据 · 读取超时（1 次）/)
  assert.match(result('alpha'), /媒体取样 100 ms.*约为播放所需的 1.24 倍/)
  assert.match(result('alpha'), /已试 2 个样片，播放地址 · 域名解析失败（1 次）/)
  assert.doesNotMatch(result('beta'), /已试|播放所需的 .* 倍/)
  assert.match(ui.text(), /允许访问 media-alpha.example/)
  assert.equal(requests.some(r => r.path.endsWith('/enable')), false)
  await click(ui, '启用来源 alpha')
  assert.equal(button(ui, '启用来源 alpha'), undefined)
  await click(ui, '返回视频源列表')
  assert.ok(ui.find(n => n.props['data-online-source-row'] === 'src_new'))
})

test('changing the catalogue route only affects refresh and ignores late results from abandoned routes', async t => {
  const lateRefresh = deferred(), lateSample = deferred()
  let refreshes = 0
  const { ui, requests } = await mount(t, { profiles: [{ id: PROFILE, mode: 'proxy', name: '现有代理' }], discoveryApi(path, body) {
    if (path.endsWith('/refresh')) {
      refreshes++
      if (refreshes === 1) return { items: [discoveryItem('default')] }
      if (body.directory_route.mode === 'direct') return lateRefresh.promise
      return { items: [discoveryItem('proxy')] }
    }
    if (path.endsWith('/default/probe')) return lateSample.promise
    assert.equal(path, '/api/online/discovery/proxy/probe')
    assert.deepEqual(body, {})
    return measured('proxy', 1200)
  } })
  await click(ui, '发现并探测来源')
  const routeField = element(ui, 'online-discovery-route')
  assert.match(nodeText(routeField), /服务默认（直连）.*直连.*代理 · 现有代理/)
  await field(ui, 'online-discovery-route', 'direct')
  const stale = routeField.props.onChange()
  await flush()
  assert.equal(requests.find(r => r.path.endsWith('/default/probe')).signal.aborted, true)
  await field(ui, 'online-discovery-route', PROFILE)
  await routeField.props.onChange()
  const refreshCalls = requests.filter(r => r.path.endsWith('/refresh'))
  assert.deepEqual(refreshCalls.map(r => r.body), [{}, { directory_route: { mode: 'direct' } }, { directory_route: { mode: 'proxy', profile_id: PROFILE } }])
  assert.equal(refreshCalls[1].signal.aborted, true)
  lateRefresh.resolve({ items: [discoveryItem('obsolete')] })
  lateSample.resolve(measured('default', 99999))
  await stale; await flush()
  assert.deepEqual(ui.all().filter(n => n.props['data-discovery-candidate']).map(n => n.props['data-discovery-candidate']), ['proxy'])
  assert.ok(!requests.some(r => r.path.endsWith('/obsolete/probe') || ['/api/online/network/profiles','/api/online/network/default'].includes(r.path) && r.method !== 'GET'))
})

test('stopping discovery cancels pending probes and prevents queued requests and late results', async t => {
  const pending = deferred()
  const { ui, requests } = await mount(t, { discoveryApi(path) {
    if (path === '/api/online/discovery/refresh') return { items: ['first', 'second', 'third'].map(discoveryItem) }
    return pending.promise
  } })
  await click(ui, '发现并探测来源')
  const probes = requests.filter(r => r.path.endsWith('/probe'))
  assert.equal(probes.length, 1)
  await click(ui, '停止探测')
  assert.ok(probes.every(r => r.signal.aborted))
  assert.match(ui.text(), /探测已停止 · 0 \/ 3/)
  pending.resolve(measured('first', 1800)); await flush()
  assert.equal(requests.filter(r => r.path.endsWith('/probe')).length, 1)
  assert.equal(ui.text().includes('本轮优先试用'), false)
})

test('a disabled global online switch keeps discovery inactive', async t => {
  const { ui, requests } = await mount(t, { onlineEnabled: false })
  assert.equal(button(ui, '发现并探测来源').props.disabled, true)
  assert.ok(!requests.some(r => r.path.includes('/discovery')))
})

test('leaving the settings section cancels outstanding probes and reconciles an already confirmed save', async t => {
  const probe = deferred(), save = deferred()
  const { ui, route, requests, draft } = await mount(t, { discoveryApi(path) {
    if (path.endsWith('/refresh')) return { items: ['alpha', 'beta'].map(discoveryItem) }
    if (path.endsWith('/beta/probe')) return probe.promise
    if (path.endsWith('/enable')) return save.promise
    return measured('alpha', 2000)
  } })
  await click(ui, '发现并探测来源')
  const saving = button(ui, '启用来源 alpha').props.onClick()
  await flush()
  route.query.sec = 'sec-general'
  await flush()
  assert.equal(requests.find(r => r.path.endsWith('/beta/probe')).signal.aborted, true)
  assert.equal(requests.find(r => r.path.endsWith('/enable')).signal.aborted, false)
  assert.equal(draft.busy(), true)
  save.resolve({ id: 'src_new', enabled: true, name: 'alpha', priority: 1 })
  probe.resolve(measured('beta', 1000))
  await saving; await flush()
  assert.equal(draft.busy(), false)
  route.query.sec = 'sec-online'
  await flush()
  assert.ok(ui.find(n => n.props['data-online-source-row'] === 'src_new'))
})

test('discovery shows cached catalogue, recovers from an expired receipt and enables only after another explicit selection', async t => {
  let expired = true
  const { ui, requests } = await mount(t, { discoveryApi(path) {
    if (path.endsWith('/refresh')) return { items: [discoveryItem('alpha')], directory_status: 'cached' }
    if (path.endsWith('/probe')) return measured('alpha', 2000)
    if (expired) { expired = false; throw Object.assign(new Error('private details'), { status: 409, code: 'online_discovery_probe_expired' }) }
    return { id: 'src_new', enabled: true, name: 'alpha', priority: 1 }
  } })
  await click(ui, '发现并探测来源')
  assert.match(ui.text(), /最近保存的候选目录/)
  await click(ui, '启用来源 alpha')
  assert.match(ui.text(), /探测结果已失效/)
  assert.equal(button(ui, '启用来源 alpha').props.disabled, true)
  await click(ui, '重新探测')
  assert.equal(requests.filter(r => r.path.endsWith('/enable')).length, 1)
  await click(ui, '启用来源 alpha')
  assert.match(ui.text(), /已启用「alpha」/)
  assert.ok(ui.find(n => n.type === 'a' && n.props['aria-label'] === '使用此源 alpha'))
})

test('enabling a saved source requires a receipt for its current configuration', async t => {
  const saved = { ...discoveryItem('saved'), id: SOURCE, name: '已存来源', source_id: SOURCE }
  const receipts = [null, 'saved-outdated-receipt', 'saved-current-receipt']
  let probes = 0
  const { ui, requests } = await mount(t, { discoveryApi(path, body) {
    if (path.endsWith('/refresh')) return { items: [{ ...saved }] }
    if (path.endsWith('/probe')) return { ...measured(SOURCE, 2500), allowed_hosts: [], probe_token: receipts[probes++] }
    if (path.endsWith('/enable')) {
      assert.deepEqual(body, { probe_token: receipts[probes - 1] })
      if (probes === 2) throw Object.assign(new Error('private details'), { status: 409, code: 'online_source_changed' })
      return { ...saved, enabled: true, priority: 50 }
    }
    throw new Error('Unexpected request')
  } })
  await click(ui, '发现并探测来源')
  assert.equal(button(ui, '启用来源 已存来源').props.disabled, true)
  await click(ui, '重新探测')
  await click(ui, '启用来源 已存来源')
  assert.match(ui.text(), /来源或网络配置已改变，请重新探测后启用/)
  assert.equal(button(ui, '启用来源 已存来源').props.disabled, true)
  await click(ui, '重新探测')
  assert.equal(requests.filter(call => call.path.endsWith('/enable')).length, 1)
  await click(ui, '启用来源 已存来源')
  assert.match(ui.text(), /已启用「已存来源」/)
  assert.ok(ui.find(node => node.type === 'a' && node.props['aria-label'] === '使用此源 已存来源'))
})

test('two simultaneous scans keep busy entries queued and eventually sample every source', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const gate = deferred(), items = Array.from({ length: 10 }, (_, i) => discoveryItem('source' + i))
  let occupied = 0, busy = 0
  const discoveryApi = async path => {
    if (path.endsWith('/refresh')) return { items }
    if (occupied === 1) { busy++; throw Object.assign(new Error('busy'), { status: 429, code: 'provider_catalog_busy', retryAfterSeconds: 1 }) }
    occupied++
    await gate.promise
    occupied--
    return measured(path.split('/').at(-2), 2500)
  }
  const a = await mount(t, { discoveryApi }), b = await mount(t, { discoveryApi })
  await click(a.ui, '发现并探测来源')
  await click(b.ui, '发现并探测来源')
  assert.equal(occupied, 1)
  assert.equal(busy, 1)
  assert.match(b.ui.text(), /正在探测 · 0 \/ 10 · 排队/)
  gate.resolve()
  await flush(); await flush()
  t.mock.timers.tick(1000)
  await flush(); await flush()
  for (const current of [a, b]) {
    assert.match(current.ui.text(), /探测完成 · 10 \/ 10/)
    const rows = current.ui.all().filter(node => node.props['data-discovery-candidate'])
    assert.equal(rows.filter(node => nodeText(node).includes('Mbps')).length, 10)
  }
})

test('empty sources can be retested but only a nonempty catalogue can be enabled', async t => {
  let empty = true
  const { ui } = await mount(t, { discoveryApi(path) {
    if (path.endsWith('/refresh')) return { items: [discoveryItem('alpha')] }
    if (empty) return { status: 'empty', reason: 'empty_catalog' }
    return { status: 'catalog_only', reason: 'no_media_sample', probe_token: 'fixture-receipt' }
  } })
  await click(ui, '发现并探测来源')
  assert.match(ui.text(), /空目录/)
  assert.equal(button(ui, '启用来源 alpha').props.disabled, true)
  empty = false
  await click(ui, '复测此源')
  assert.match(ui.text(), /仅目录可读/)
  assert.equal(button(ui, '启用来源 alpha').props.disabled, false)
})

test('extended probes require an explicit timeout retry and preserve host review and receipt expiry', async t => {
  const approval = deferred()
  let calls = 0
  const { ui, requests } = await mount(t, { discoveryApi(path) {
    if (path.endsWith('/refresh')) return { items: [discoveryItem('alpha')] }
    calls++
    if (calls === 1) return { status: 'catalog_only', reason: 'target_host_not_allowed', probe_token: 'host-receipt',
      missing_hosts: ['media.example'], attempts: [{ stage: 'entry', reason: 'target_host_not_allowed' }, { stage: 'media', reason: 'provider_timeout' }] }
    if (calls === 2) return approval.promise
    if (calls === 3) throw Object.assign(new Error(), { status: 504, code: 'provider_timeout' })
    return measured('alpha', 2600)
  } })
  await click(ui, '发现并探测来源')
  assert.equal(button(ui, '延长取样并复测'), undefined)
  const confirming = button(ui, '允许本次试读并延长复测').props.onClick()
  await flush()
  assert.equal(button(ui, '启用来源 alpha').props.disabled, true)
  approval.reject(Object.assign(new Error(), { status: 409, code: 'online_discovery_probe_expired' }))
  await confirming; await flush()
  assert.match(ui.text(), /探测结果已失效/)
  assert.equal(button(ui, '允许本次试读并延长复测'), undefined)
  assert.equal(button(ui, '延长取样并复测'), undefined)
  await click(ui, '复测此源')
  await click(ui, '延长取样并复测')
  assert.ok(button(ui, '启用来源 alpha'))
  assert.equal(button(ui, '延长取样并复测'), undefined)
  await click(ui, '重新探测')
  const probes = requests.filter(r => r.path.endsWith('/probe'))
  assert.deepEqual(probes.map(r => r.body), [{}, { extended: true, probe_token: 'host-receipt', hosts: ['media.example'] }, {}, { extended: true }, {}])
  assert.deepEqual(probes.map(r => r.timeout), [20000, 28000, 20000, 28000, 20000])
})

test('an active source can explicitly test missing domains and save a selected poster domain', async t => {
  const item = { ...discoveryItem('saved'), id: SOURCE, source_id: SOURCE, enabled: true }
  const { ui, requests } = await mount(t, { discoveryApi(path, body) {
    if (path.endsWith('/refresh')) return { items: [item] }
    if (path.endsWith('/probe') && !body.probe_token) {
      return { status: 'catalog_only', probe_token: 'first-receipt', missing_hosts: ['cdn.example'], reason: 'target_host_not_allowed' }
    }
    if (path.endsWith('/probe')) {
      assert.deepEqual(body, { probe_token: 'first-receipt', hosts: ['cdn.example'] })
      return { ...measured('saved', 2500), probe_token: 'verified-receipt', allowed_hosts: ['cdn.example'], poster_hosts: ['poster.example'] }
    }
    assert.deepEqual(body, { probe_token: 'verified-receipt', poster_hosts: ['poster.example'] })
    return { ...item, allowed_hosts: ['cdn.example', 'poster.example'], priority: 100 }
  } })
  await click(ui, '发现并探测来源')
  assert.match(ui.text(), /试读还需要访问 cdn.example/)
  await click(ui, '允许本次试读并复测')
  assert.match(ui.text(), /保存后允许访问 cdn.example/)
  const check = ui.find(n => n.type === 'input' && n.props.type === 'checkbox' && typeof n.props.onChange === 'function')
  assert.ok(check)
  check.props.onChange({ target: { checked: true } })
  await flush()
  await click(ui, '保存已选主机')
  assert.ok(requests.find(r => r.path.endsWith('/enable')))
  assert.match(ui.text(), /已保存/)
})

test('alternate route approval stays with the tested source, selected proxy and tested hosts', async t => {
  const item = { ...discoveryItem('saved'), id: SOURCE, source_id: SOURCE, enabled: true }
  const proxy = { mode: 'proxy', profile_id: PROFILE }
  const { ui, requests } = await mount(t, { profiles: [{ id: PROFILE, mode: 'proxy', name: '线路甲' }], discoveryApi(path, body) {
    if (path.endsWith('/refresh')) return { items: [item] }
    if (path.endsWith('/enable')) {
      assert.deepEqual(body, { probe_token: 'verified', apply_route: true })
      return item
    }
    if (!body.probe_route) return { ...measured(SOURCE, 4400), realtime_ratio: .75 }
    if (body.probe_route.mode === 'direct') return { status: 'catalog_only', tested_route: { mode: 'direct', profile_id: null },
      reason: 'http_403', probe_token: 'catalog', catalog_ms: 200 }
    assert.deepEqual(body.probe_route, proxy)
    if (!body.probe_token) return { status: 'catalog_only', tested_route: proxy, probe_token: 'proxy-host',
      reason: 'target_host_not_allowed', missing_hosts: ['media.example'] }
    assert.deepEqual(body, { probe_route: proxy, probe_token: 'proxy-host', hosts: ['media.example'] })
    return { ...measured(SOURCE, 6000), realtime_ratio: 1.4, allowed_hosts: ['media.example'], tested_route: proxy, probe_token: 'verified' }
  } })
  await click(ui, '发现并探测来源')
  await click(ui, '延长取样并复测')
  assert.deepEqual(requests.at(-1).body, { extended: true })
  async function select(value) {
    const id = 'online-probe-route-' + SOURCE
    await field(ui, id, value)
    element(ui, id).props.onChange()
    await flush()
  }
  await select(PROFILE)
  await click(ui, '复测此源')
  assert.equal(button(ui, '复测此源'), undefined)
  assert.ok(button(ui, '允许本次试读并复测'))
  await select('direct')
  assert.equal(button(ui, '允许本次试读并复测'), undefined)
  await click(ui, '复测此源')
  assert.deepEqual(requests.at(-1).body, { probe_route: { mode: 'direct' } })
  assert.equal(button(ui, '保存实测出口'), undefined)
  await select(PROFILE)
  await click(ui, '复测此源')
  await click(ui, '允许本次试读并复测')
  assert.match(ui.text(), /本次探测出口 · 代理 · 线路甲/)
  assert.match(ui.text(), /目录和媒体均使用代理 · 线路甲/)
  assert.equal(requests.some(r => r.path.endsWith('/enable')), false)
  await click(ui, '保存出口和主机')
  assert.equal(requests.filter(r => r.path.endsWith('/enable')).length, 1)
})

test('new source only saves a measured explicit route and subsequently retests its saved ID', async t => {
  const item = discoveryItem('candidate')
  const { ui, requests } = await mount(t, { discoveryApi(path, body) {
    if (path.endsWith('/refresh')) return { items: [item] }
    if (path.endsWith('/enable')) {
      assert.deepEqual(body, { probe_token: 'verified-direct', apply_route: true })
      return { ...item, id: SOURCE, enabled: true }
    }
    if (!body.probe_route) return measured(item.id, 1200)
    return { ...measured(item.id, 2500), tested_route: { mode: 'direct', profile_id: null },
      attempts: [{ stage: 'media', reason: 'provider_timeout' }, { stage: 'media', reason: 'ok' }], probe_token: 'verified-direct' }
  } })
  await click(ui, '发现并探测来源')
  const id = 'online-probe-route-candidate'
  await field(ui, id, 'direct')
  element(ui, id).props.onChange()
  await flush()
  assert.equal(button(ui, '启用来源 candidate').props.disabled, true)
  await click(ui, '复测此源')
  await click(ui, '启用并保存实测出口 candidate')
  await click(ui, '延长取样并复测')
  assert.equal(requests.at(-1).path, '/api/online/discovery/' + SOURCE + '/probe')
  assert.deepEqual(requests.at(-1).body, { probe_route: { mode: 'direct' }, extended: true })
})
