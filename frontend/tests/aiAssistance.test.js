import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { loadSfc } from './helpers/loadSfc.js'
import { useAiRequest } from '../src/useAiRequest.js'
import { aiFilterDraft, aiFilterError, aiFiltersToWall } from '../src/aiSearch.js'
import { canBindAiCandidate, isAiExternalCandidate } from '../src/aiMatch.js'
import { aiProviderPreset } from '../src/aiProviders.js'

const deferred = () => {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Vue.nextTick() }
function scoped(t, request) {
  const scope = Vue.effectScope()
  const state = scope.run(() => useAiRequest(request))
  t.after(() => scope.stop())
  return { scope, state }
}
test('AI request requires explicit invocation, suppresses duplicates, and sends only the supplied scope', async t => {
  const pending = deferred(), requests = []
  const { state } = scoped(t, (path, opts) => { requests.push({ path, opts }); return pending.promise })
  assert.equal(requests.length, 0)
  const first = state.run('/api/ai/search', { q: '香港喜剧', kind: 'movie', media_library_id: 7 })
  await state.run('/api/ai/search', { q: '重复' })
  assert.equal(requests.length, 1)
  assert.deepEqual(JSON.parse(requests[0].opts.body), { q: '香港喜剧', kind: 'movie', media_library_id: 7 })
  pending.resolve({ ok: true, filters: {} })
  await first
  assert.equal(state.result.value.ok, true)
})
test('changing context aborts old request; its late result cannot replace the current result', async t => {
  const old = deferred(), latest = deferred(), signals = []
  const { state } = scoped(t, (_path, opts) => { signals.push(opts.signal); return signals.length === 1 ? old.promise : latest.promise })
  const first = state.run('/old', {})
  state.reset()
  assert.equal(signals[0].aborted, true)
  const second = state.run('/new', {})
  latest.resolve({ ok: true, summary: '最新' })
  await second
  old.resolve({ ok: true, summary: '过期' })
  await first
  assert.equal(state.result.value.summary, '最新')
  assert.equal(state.busy.value, false)
})
test('unmount discards pending results and forbids subsequent requests', async t => {
  const pending = deferred()
  let count = 0, signal
  const { scope, state } = scoped(t, (_path, opts) => { count++; signal = opts.signal; return pending.promise })
  const task = state.run('/match', {})
  scope.stop()
  assert.equal(signal.aborted, true)
  pending.resolve({ ok: true })
  await task
  await state.run('/match', {})
  assert.equal(state.result.value, null)
  assert.equal(count, 1)
})
test('failed or disabled AI never leaves a previous suggestion actionable', async t => {
  let fail = false
  const { state } = scoped(t, async () => fail ? { ok: false, code: 'disabled', message: '智能辅助未启用' } : { ok: true })
  await state.run('/match', {})
  fail = true
  await state.run('/match', {})
  assert.equal(state.result.value, null)
  assert.equal(state.error.value, '智能辅助未启用')
  assert.equal(state.busy.value, false)
})
test('editable AI filters map to existing movie and TV filters without changing library scope', () => {
  const draft = aiFilterDraft({ q: '', genre: ['喜剧'], country: ['HK'], decade: [1990], watched: 0, min_rating: 7,
    rating_source: 'tmdb', sort: 'rating', order: 'desc', library_id: 999, media_library_id: 999 })
  draft.year = '1994，1995'
  const out = aiFiltersToWall(draft)
  assert.deepEqual(out.sel.years, ['1994', '1995'])
  assert.deepEqual(out.sel.countries, ['HK'])
  assert.equal(out.sel.watched, 0)
  assert.equal(out.sel.rating, 7)
  assert.equal(out.media_library_id, undefined)
  assert.equal(out.library_id, undefined)
  assert.deepEqual(out.sort, { key: 'rating', order: 'desc' })
  const tv = aiFiltersToWall({ ...draft, status: ['ended', 'invented'], rating_source: 'douban' }, 'tv')
  assert.deepEqual(tv.sel.status, ['ended'])
  assert.equal(tv.sel.ratingSource, 'tmdb')
})
test('malformed edited years and decades are rejected before backend could silently broaden the search', () => {
  assert.match(aiFilterError({ year: '199x' }), /年份/)
  assert.match(aiFilterError({ decade: '1994' }), /整十/)
  assert.match(aiFilterError({ min_rating: 11 }), /评分/)
  assert.match(aiFilterError({ country: '中国' }), /两位代码/)
  assert.equal(aiFilterError({ year: '1994,1995', decade: '1990', country: 'hk' }), '')
})
test('country precedence matches the existing movie and TV walls without retaining inactive regions', () => {
  for (const kind of ['movie', 'tv']) {
    assert.deepEqual(aiFiltersToWall({ country: 'hk', region: '欧美' }, kind).sel.regions, [])
    assert.deepEqual(aiFiltersToWall({ country: '', region: '华语' }, kind).sel.regions, ['华语'])
  }
})
test('server verified cached candidates may bind through external path regardless of source', () => {
  const cached = { source: 'imdb', source_id: 'tt12345', bindable: true }
  assert.equal(canBindAiCandidate(cached), true)
  assert.equal(isAiExternalCandidate(cached), true)
  assert.equal(isAiExternalCandidate({ ...cached, bindable: false }), false)
  assert.equal(isAiExternalCandidate({ ...cached, bindable: undefined }), false)
  assert.equal(isAiExternalCandidate({ ...cached, source_id: '' }), false)
  assert.equal(isAiExternalCandidate({ source: 'tvmaze', source_id: 7 }), true)
})

const componentUrl = name => new URL('../src/components/' + name + '.vue', import.meta.url)
const overrides = request => ({
  vue: { ...Vue, vModelText: {}, vModelSelect: {}, vModelCheckbox: {} },
  '../useAiRequest.js': { useAiRequest: () => useAiRequest(request) },
})
const link = { props: ['to'], setup: (_props, { slots }) => () => Vue.h('a', slots.default?.()) }
for (const [name, props, expected] of [
  ['AiSettingsPanel', {}, /智能辅助（可选）/],
  ['AiSearchPanel', { kind: 'movie', mediaLibraryId: 7 }, /解析条件/],
  ['AiMatchSuggestions', { kind: 'tv', itemId: 12 }, /AI 匹配建议/],
]) {
  test(name + ' initializes and renders without a cloud request', async () => {
    const component = await loadSfc(componentUrl(name), overrides(() => { throw new Error('No setup side effects') }))
    const app = Vue.createSSRApp(component, props)
    app.component('RouterLink', link)
    const html = await renderToString(app)
    assert.match(html, expected)
    assert.doesNotMatch(html, /确认绑定此候选|确认应用条件/)
  })
}

async function mount(t, name, initialProps, request) {
  const component = await loadSfc(componentUrl(name), overrides(request))
  const props = Vue.reactive(initialProps)
  const element = () => ({ children: [], parent: null })
  const remove = node => {
    if (!node?.parent) return
    const siblings = node.parent.children
    const index = siblings.indexOf(node)
    if (index >= 0) siblings.splice(index, 1)
    node.parent = null
  }
  const renderer = Vue.createRenderer({
    createElement: element, createText: element, createComment: element,
    setText: () => {}, setElementText: () => {}, patchProp: () => {},
    insert: (node, parent, anchor) => {
      remove(node)
      node.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(node)
      else parent.children.splice(index, 0, node)
    },
    remove, parentNode: node => node.parent,
    nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  })
  const app = renderer.createApp({ setup: () => () => Vue.h(component, props) })
  app.component('RouterLink', link)
  app.mount(element())
  t.after(() => app.unmount())
  const nodes = () => {
    const out = []
    function walk(node) {
      if (!node || typeof node !== 'object') return
      out.push(node)
      if (node.component) walk(node.component.subTree)
      if (Array.isArray(node.children)) node.children.forEach(walk)
    }
    walk(app._instance.subTree)
    return out
  }
  const textOf = node => typeof node?.children === 'string' ? node.children : Array.isArray(node?.children) ? node.children.map(textOf).join('') : ''
  const button = text => nodes().find(n => n.type === 'button' && textOf(n) === text)
  return { props, nodes, button, app, text: () => nodes().map(textOf).join(' ') }
}
test('search preview must be reviewed, permits edits, and is invalidated by media switch or new text', async t => {
  const requests = [], applied = []
  const response = { ok: true, summary: '香港喜剧', warnings: [], filters: { genre: ['喜剧'], country: ['HK'], watched: 0 } }
  const ui = await mount(t, 'AiSearchPanel', { kind: 'movie', query: '未看的香港喜剧', mediaLibraryId: 7, onApply: v => applied.push(v) }, async (path, opts) => { requests.push({ path, body: JSON.parse(opts.body) }); return response })
  assert.equal(requests.length, 0)
  await ui.button('解析条件').props.onClick()
  await flush()
  assert.equal(applied.length, 0)
  assert.deepEqual(requests[0].body, { q: '未看的香港喜剧', kind: 'movie', media_library_id: 7 })
  const genre = ui.nodes().find(n => n.type === 'input' && n.props['onUpdate:modelValue'] && n.dirs?.[0]?.value === '喜剧')
  genre.props['onUpdate:modelValue']('喜剧，动作')
  ui.button('确认应用条件').props.onClick()
  assert.deepEqual(applied[0].sel.genres, ['喜剧', '动作'])
  ui.props.mediaLibraryId = 8
  await flush()
  assert.equal(ui.button('确认应用条件'), undefined)
  await ui.button('解析条件').props.onClick()
  await flush()
  ui.nodes().find(n => n.type === 'textarea').props['onUpdate:modelValue']('新输入')
  await flush()
  assert.equal(ui.button('确认应用条件'), undefined)
})
test('AI candidate choice needs confirmation and reuses only returned candidates', async t => {
  const requests = [], selected = []
  const candidate = { title: '测试电影', year: 1994, tmdb_id: 44, reason: '标题与年份一致' }
  const ui = await mount(t, 'AiMatchSuggestions', { kind: 'movie', itemId: 9, alreadyMatched: true, onSelect: c => selected.push(c) }, async (path, opts) => { requests.push({ path, body: JSON.parse(opts.body) }); return { ok: true, candidates: [candidate], summary: '需要核对' } })
  await ui.button('AI 匹配建议').props.onClick()
  await flush()
  assert.deepEqual(requests, [{ path: '/api/ai/match', body: { kind: 'movie', id: 9 } }])
  assert.equal(selected.length, 0)
  ui.button('选择此候选').props.onClick()
  await flush()
  assert.equal(selected.length, 0)
  assert.match(ui.text(), /这会替换当前资料匹配/)
  ui.button('确认绑定此候选').props.onClick()
  assert.equal(selected[0].tmdb_id, 44)
  ui.props.itemId = 10
  await flush()
  assert.equal(ui.button('选择此候选'), undefined)
})
test('index-only candidate without retrievable details cannot be confirmed', async t => {
  const ui = await mount(t, 'AiMatchSuggestions', { kind: 'movie', itemId: 9 }, async () => ({ ok: true,
    candidates: [{ title: '只知道片名', source: 'nfo', source_id: 'index-only', bindable: false }] }))
  await ui.button('AI 匹配建议').props.onClick()
  await flush()
  assert.equal(ui.button('选择此候选'), undefined)
  assert.match(ui.text(), /仅索引线索，请手动搜索核对/)
})
test('protected TV directory binding shows server guidance instead of a generic index-only message', async t => {
  const ui = await mount(t, 'AiMatchSuggestions', { kind: 'tv', itemId: 9 }, async () => ({ ok: true,
    candidates: [{ title: '不同剧集', tmdb_id: 123, bindable: false, bind_reason: '已有目录归属，换绑请使用“归属与季号”。' }] }))
  await ui.button('AI 匹配建议').props.onClick()
  await flush()
  assert.equal(ui.button('选择此候选'), undefined)
  assert.match(ui.text(), /换绑请使用“归属与季号”/)
  assert.doesNotMatch(ui.text(), /仅索引线索/)
})
test('settings persist key only on save, independently test saved config, and explicitly clear with env fallback', async t => {
  const requests = []
  let saved = { enabled: false, provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash',
    timeout_seconds: 12, daily_limit: 100, api_key_set: true, api_key_source: 'db', api_key_masked: '***1234',
    usage: { date: '2026-10-02', requests: 0, input_tokens: 0, output_tokens: 0 } }
  const ui = await mount(t, 'AiSettingsPanel', {}, async (path, opts) => {
    const body = opts.body && JSON.parse(opts.body)
    requests.push({ path, method: opts.method, body })
    if (path.endsWith('/check')) return { ok: true, message: '连接成功', usage: { ...saved.usage, requests: 1 } }
    if (body?.clear_api_key) saved = { ...saved, api_key_source: 'env' }
    else if (body) { const config = Object.fromEntries(Object.entries(body).filter(([key]) => key !== 'api_key')); saved = { ...saved, ...config } }
    return { ...saved }
  })
  await flush()
  assert.equal(ui.nodes().some(n => n.type === 'input' && n.props.type === 'password'), false, 'Disabled AI keeps optional service fields collapsed')
  await ui.button('配置与测试服务').props.onClick()
  await flush()
  const password = ui.nodes().find(n => n.type === 'input' && n.props.type === 'password')
  assert.equal(password.dirs[0].value, '')
  assert.equal(requests.length, 1)
  password.props['onUpdate:modelValue']('user-secret-key')
  await flush()
  assert.equal(ui.button('测试已保存连接').props.disabled, true)
  assert.equal(requests.length, 1)
  await ui.button('保存智能辅助配置').props.onClick()
  await flush()
  assert.equal(requests[1].body.api_key, 'user-secret-key')
  assert.equal(ui.nodes().find(n => n.type === 'input' && n.props.type === 'password').dirs[0].value, '')
  await ui.button('测试已保存连接').props.onClick()
  await flush()
  assert.equal(requests[2].path, '/api/ai/check')
  assert.equal(requests[2].body, undefined)
  await ui.button('移除已保存密钥').props.onClick()
  await flush()
  assert.equal(requests.length, 3)
  await ui.button('确认移除已保存密钥').props.onClick()
  await flush()
  assert.deepEqual(requests[3].body, { clear_api_key: true })
  assert.match(ui.text(), /现使用服务器环境密钥/)
})

const savedAiSettings = overrides => ({ enabled: false, provider: 'deepseek',
  base_url: 'https://api.deepseek.com', model: 'deepseek-flash', timeout_seconds: 12,
  daily_limit: 100, api_key_set: true, api_key_source: 'db', api_key_masked: '****1234',
  usage: { date: '2026-10-02', requests: 0, input_tokens: 0, output_tokens: 0 }, ...overrides })
const providerSelect = ui => ui.nodes().find(n => n.type === 'select')
const fieldByValue = (ui, value) => ui.nodes().find(n => n.type === 'input' && n.dirs?.[0]?.value === value)
async function chooseProvider(ui, provider) {
  const select = providerSelect(ui)
  select.props['onUpdate:modelValue'](provider)
  select.props.onChange()
  await flush()
}
test('provider presets return only endpoint/model defaults and leave compatible settings editable', () => {
  assert.deepEqual(aiProviderPreset('opencode_go'), { base_url: 'https://opencode.ai/zen/go/v1', model: 'glm-5.3-flash' })
  const deepseek = aiProviderPreset('deepseek')
  assert.deepEqual(deepseek, { base_url: 'https://api.deepseek.com', model: 'deepseek-flash' })
  deepseek.model = 'edited'
  assert.equal(aiProviderPreset('deepseek').model, 'deepseek-flash')
  assert.deepEqual(aiProviderPreset('compatible'), {})
})
test('selecting Go fills its official preset without a request, permits edits, and saves the chosen key in the single config', async t => {
  const requests = []
  let saved = savedAiSettings()
  const ui = await mount(t, 'AiSettingsPanel', {}, async (path, opts) => {
    const body = opts.body && JSON.parse(opts.body)
    requests.push({ path, method: opts.method, body })
    if (body) saved = { ...saved, ...Object.fromEntries(Object.entries(body).filter(([key]) => key !== 'api_key')) }
    return { ...saved }
  })
  await flush()
  await ui.button('配置与测试服务').props.onClick()
  await flush()
  await chooseProvider(ui, 'opencode_go')
  assert.equal(requests.length, 1)
  assert.ok(fieldByValue(ui, 'https://opencode.ai/zen/go/v1'))
  const model = fieldByValue(ui, 'glm-5.3-flash')
  assert.ok(model)
  assert.match(ui.text(), /影视用途尚未验证/)
  assert.match(ui.text(), /不按服务商分别保存密钥/)
  assert.match(ui.text(), /留空会沿用当前密钥/)
  assert.match(ui.text(), /不加 opencode-go\/ 前缀/)
  const official = ui.nodes().find(n => n.type === 'a' && n.props?.href === 'https://opencode.ai/docs/go/#where-can-i-use-it')
  assert.equal(official.props.target, '_blank')
  assert.equal(official.props.rel, 'noopener noreferrer')
  model.props['onUpdate:modelValue']('glm-5.3')
  ui.nodes().find(n => n.type === 'input' && n.props.type === 'password').props['onUpdate:modelValue']('go-test-secret')
  await ui.button('保存智能辅助配置').props.onClick()
  await flush()
  assert.deepEqual(requests[1], { path: '/api/ai/settings', method: 'PATCH', body: {
    enabled: false, provider: 'opencode_go', base_url: 'https://opencode.ai/zen/go/v1', model: 'glm-5.3',
    timeout_seconds: 12, daily_limit: 100, api_key: 'go-test-secret',
  } })
  assert.ok(fieldByValue(ui, 'glm-5.3'), 'The save response must not restore the example model')
  assert.equal(ui.nodes().find(n => n.type === 'input' && n.props.type === 'password').dirs[0].value, '')
  assert.equal(requests.some(r => r.path === '/api/ai/check'), false)
})
test('loading a saved custom Go model does not apply presets; compatible preserves edits and explicit DeepSeek restores its preset', async t => {
  const requests = []
  const saved = savedAiSettings({ provider: 'opencode_go', model: 'kimi-k3', base_url: 'https://opencode.ai/zen/go/v1' })
  const ui = await mount(t, 'AiSettingsPanel', {}, async (path, opts) => {
    requests.push({ path, body: opts.body && JSON.parse(opts.body) })
    return { ...saved }
  })
  await flush()
  await ui.button('配置与测试服务').props.onClick()
  await flush()
  assert.equal(providerSelect(ui).dirs[0].value, 'opencode_go')
  assert.ok(fieldByValue(ui, 'kimi-k3'))
  assert.equal(ui.button('测试已保存连接').props.disabled, false)
  await chooseProvider(ui, 'compatible')
  assert.ok(fieldByValue(ui, 'kimi-k3'))
  assert.ok(fieldByValue(ui, 'https://opencode.ai/zen/go/v1'))
  fieldByValue(ui, 'https://opencode.ai/zen/go/v1').props['onUpdate:modelValue']('http://localhost:11434/v1')
  fieldByValue(ui, 'kimi-k3').props['onUpdate:modelValue']('local-custom')
  await flush()
  assert.ok(fieldByValue(ui, 'local-custom'))
  assert.ok(fieldByValue(ui, 'http://localhost:11434/v1'))
  await chooseProvider(ui, 'deepseek')
  assert.ok(fieldByValue(ui, 'deepseek-flash'))
  assert.ok(fieldByValue(ui, 'https://api.deepseek.com'))
  assert.equal(requests.length, 1)
  await ui.button('保存智能辅助配置').props.onClick()
  assert.equal(requests[1].body.provider, 'deepseek')
  assert.equal('api_key' in requests[1].body, false, 'Blank key keeps the one existing server key')
})
