import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, deferred, flush, nodeText } from './helpers/renderHarness.js'

async function mount(t, request, extra = {}) {
  const oldDocument = globalThis.document, oldWindow = globalThis.window
  globalThis.document = { activeElement: {}, body: {} }
  globalThis.window = { matchMedia: () => ({ matches: false }) }
  const component = await loadSfc(new URL('../src/components/AssistantChatDialog.vue', import.meta.url), {
    vue: { ...Vue, vModelText: {}, vModelSelect: {} },
    '../api.js': { api: request },
    '../assistantArtwork.js': { projectorArtwork: { idle: 'idle.webp', talk: 'talk.webp' } },
    './JzDialog.vue': { default: { setup: (_props, { slots, expose }) => {
      expose({ canFocus: () => false })
      return () => Vue.h('section', [slots.header?.({ titleId: 'test-title' }), slots.default?.(), slots.footer?.()])
    } } },
  })
  const props = Vue.reactive({ docked: true, section: 'sec-ai', libraries: [], dirtyLibraries: [],
    refreshLibraries: () => {}, settingsVersion: 0, ...extra })
  const ui = renderHarness(component, props)
  t.after(() => { ui.app.unmount(); globalThis.document = oldDocument; globalThis.window = oldWindow })
  const textbox = () => ui.find(el => el.type === 'input' && ['settings-question', 'assistant-question'].includes(el.props.id))
  const ask = text => {
    textbox().props['onUpdate:modelValue'](text)
    return ui.find(el => el.type === 'form').props.onSubmit({ preventDefault() {} })
  }
  await flush()
  return { ...ui, props, textbox, ask }
}

test('settings check failure offers a retry and manual; refreshing saved configuration never contacts the model', async t => {
  let retry = false, state = { enabled: true, provider: 'deepseek', api_key_set: false }, reads = 0
  const ui = await mount(t, async (path, options) => {
    assert.equal(path, '/api/ai/settings')
    assert.equal(options.method, 'GET')
    reads++
    if (!retry) throw new Error('测试网络故障')
    return state
  })
  assert.match(ui.text(), /暂时无法读取智能辅助配置/)
  assert.ok(ui.textbox())
  retry = true
  await ui.find(el => el.type === 'button' && nodeText(el) === '重试').props.onClick()
  await flush()
  assert.match(ui.text(), /还缺少 API Key/)
  assert.ok(ui.textbox())
  state = { enabled: true, provider: 'compatible', api_key_set: false }
  ui.props.settingsVersion++
  await flush()
  assert.ok(ui.textbox())
  assert.equal(reads, 3)
})

test('section change keeps completed chat, discards a stale reply, and permits a new request immediately', async t => {
  const pending = deferred(), sent = [], motions = []
  const ui = await mount(t, async (path, options) => {
    if (path === '/api/ai/settings') return { enabled: true, provider: 'deepseek', api_key_set: true }
    sent.push(options)
    if (sent.length === 2) return pending.promise
    return { answer: sent.length === 1 ? '已完成的说明' : '新分区的说明', topics: [], mode: 'ai' }
  }, { onMotion: motion => motions.push(motion) })
  await ui.ask('最初的提问')
  const late = ui.ask('还没返回的提问')
  ui.props.section = 'sec-display'
  await flush()
  assert.equal(sent[1].signal.aborted, true)
  assert.equal(motions.at(-1), 'idle')
  await ui.ask('新分区的问题')
  pending.resolve({ answer: '上一分区的过期答复', topics: [], mode: 'ai',
    suggestion: { kind: 'display', field: 'fontSize', value: 18 } })
  await late
  await flush()
  assert.match(ui.text(), /已完成的说明/)
  assert.match(ui.text(), /新分区的说明/)
  assert.doesNotMatch(ui.text(), /上一分区的过期答复|确认应用/)
  assert.deepEqual(JSON.parse(sent[2].body).history, [{ question: '最初的提问', answer: '已完成的说明' }])
  assert.equal(motions.filter(motion => motion === 'reply').length, 2)
})

test('changing the active target or saving a new model invalidates an actionable suggestion', async t => {
  let enabled = true
  const suggestion = { kind: 'display', field: 'fontSize', value: 18 }
  const ui = await mount(t, async (path, options) => {
    if (path === '/api/ai/settings') return { enabled, provider: 'compatible' }
    assert.equal(options.method, 'POST')
    return { answer: '请核对浏览器设置。', topics: [], mode: 'ai', suggestion }
  })
  await ui.ask('把字体调到18')
  assert.match(ui.text(), /确认应用/)
  ui.props.contextKey = 'another-library'
  await flush()
  assert.match(ui.text(), /请核对浏览器设置/)
  assert.doesNotMatch(ui.text(), /确认应用/)
  await ui.ask('改大一点')
  assert.match(ui.text(), /确认应用/)
  enabled = false
  ui.props.settingsVersion++
  await flush()
  assert.match(ui.text(), /尚未启用/)
  assert.doesNotMatch(ui.text(), /确认应用/)
  assert.ok(ui.textbox())
})

test('a local fallback keeps its answer and rechecks whether another client disabled the service', async t => {
  let enabled = true
  const calls = []
  const ui = await mount(t, async (path, options) => {
    calls.push(options.method)
    if (path === '/api/ai/settings') return { enabled, provider: 'compatible' }
    return { answer: '手册里的设置方法', topics: [], mode: 'local' }
  })
  enabled = false
  await ui.ask('如何调整？')
  await flush()
  assert.match(ui.text(), /手册里的设置方法/)
  assert.match(ui.text(), /尚未启用/)
  assert.ok(ui.textbox())
  assert.deepEqual(calls, ['GET', 'POST', 'GET'])
})

test('settings can answer locally before model credentials are configured', async t => {
  const calls = []
  const ui = await mount(t, async (path, options) => {
    calls.push(path)
    if (path === '/api/ai/settings') return { enabled: true, provider: 'deepseek', api_key_set: false }
    assert.equal(JSON.parse(options.body).section, 'sec-ai')
    return { answer: '嗨，我是小映！', mode: 'social', reaction: 'wave', topics: [] }
  })
  assert.match(ui.text(), /还缺少 API Key/)
  await ui.ask('你好')
  assert.match(ui.text(), /嗨，我是小映/)
  assert.deepEqual(calls, ['/api/ai/settings', '/api/ai/settings-assistant'])
})

test('browsing shares only completed question and answer text across channels and renders no settings actions', async t => {
  const calls = [], pending = deferred()
  const start = { id: 8, question: '你好', answer: '嗨，我是小映！', mode: 'social', reaction: 'wave' }
  const ui = await mount(t, async (path, options) => {
    assert.equal(path, '/api/ai/assistant')
    assert.equal(options.headers['X-Viewing-Mode'], 'private')
    calls.push({ body: JSON.parse(options.body), signal: options.signal })
    if (calls.length === 2) return pending.promise
    return { answer: '从继续观看找到那一集。', mode: 'local', reaction: null,
      topics: [{ id: 'display', section: 'sec-display', title: '管理设置' }],
      suggestion: { kind: 'display', field: 'fontSize', value: 18 } }
  }, { channel: 'movie', channelLabel: '电影', viewingMode: 'private', history: [start],
    'onUpdate:history': value => { ui.props.history = value } })
  assert.deepEqual(calls, [])
  await ui.ask('怎么继续播放？')
  assert.deepEqual(calls[0].body, { question: '怎么继续播放？', channel: 'movie', history: [
    { question: start.question, answer: start.answer },
  ] })
  assert.equal(ui.props.history.at(-1).id, 9)
  assert.match(ui.text(), /本地回答/)
  assert.doesNotMatch(ui.text(), /管理设置|确认应用/)
  const old = ui.ask('上一频道的问题')
  ui.props.channel = 'tv'
  await flush()
  assert.equal(calls[1].signal.aborted, true)
  pending.resolve({ mode: 'ai', answer: '迟到的回复。' })
  await old
  assert.doesNotMatch(ui.text(), /迟到的回复/)
  await ui.ask('在剧集也可以吗？')
  assert.equal(calls[2].body.channel, 'tv')
  assert.equal(calls[2].body.history.length, 2)
})

test('example chips prefill an editable draft; a composition Enter does not submit a model request', async t => {
  let sends = 0
  const ui = await mount(t, async path => {
    if (path === '/api/ai/settings') return { enabled: true, provider: 'compatible' }
    sends++
    return { answer: '显示说明', mode: 'ai', topics: [] }
  })
  const sample = '帮我把字体调大一点'
  const input = ui.textbox()
  let focused = false
  input.focus = () => { focused = true }
  ui.find(el => el.type === 'button' && nodeText(el) === sample).props.onClick()
  assert.equal(focused, true)
  const enter = { key: 'Enter', isComposing: true }
  input.props.onKeydown(enter)
  await ui.find(el => el.type === 'form').props.onSubmit({ preventDefault() {} })
  assert.equal(sends, 0)
  await new Promise(resolve => setTimeout(resolve, 0))
  await ui.find(el => el.type === 'form').props.onSubmit({ preventDefault() {} })
  assert.equal(sends, 1)
  assert.match(ui.text(), /显示说明/)
})

test('a question appears while waiting and can be resubmitted after a failed reply', async t => {
  const response = deferred(), requests = [], motions = []
  const ui = await mount(t, async (path, options) => {
    if (path === '/api/ai/settings') return { enabled: true, provider: 'compatible' }
    requests.push(JSON.parse(options.body))
    if (requests.length === 1) return response.promise
    return { answer: '当前的配置方法', topics: [], mode: 'ai' }
  }, { onMotion: motion => motions.push(motion) })
  const text = '如何设置字幕？'
  const waiting = ui.ask(text)
  await flush()
  assert.match(ui.text(), /如何设置字幕？/)
  assert.match(ui.text(), /小映正在回复/)
  response.reject(new Error('服务暂时不可用'))
  await waiting
  await flush()
  assert.match(ui.text(), /提问失败，可重新发送/)
  assert.deepEqual(motions, ['thinking', 'idle'])
  await ui.find(el => el.type === 'form').props.onSubmit({ preventDefault() {} })
  await flush()
  assert.deepEqual(requests.map(row => row.question), [text, text])
  assert.deepEqual(requests[1].history, [])
  assert.match(ui.text(), /当前的配置方法/)
  assert.doesNotMatch(ui.text(), /提问失败|小映正在回复/)
  assert.equal(motions.at(-1), 'reply')
})

test('a previous arrival cannot end the current request animation or animate an old bubble', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const motions = [], next = deferred()
  let count = 0
  const answer = { answer: '配置方法', topics: [], mode: 'ai' }
  const ui = await mount(t, async path => {
    if (path === '/api/ai/settings') return { enabled: true, provider: 'compatible' }
    return ++count === 1 ? answer : next.promise
  }, { onMotion: motion => motions.push(motion) })
  const arrivingReplies = () => ui.all().filter(el => el.props.class?.includes('assistant-reply-arriving'))
  await ui.ask('第一个问题')
  assert.equal(arrivingReplies().length, 1)
  t.mock.timers.tick(800)
  const followup = ui.ask('再问一个问题')
  await flush()
  t.mock.timers.tick(800)
  await flush()
  assert.equal(motions.at(-1), 'thinking')
  assert.equal(arrivingReplies().length, 0)
  next.resolve(answer)
  await followup
  await flush()
  assert.equal(motions.at(-1), 'reply')
  assert.equal(arrivingReplies().length, 1)
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(motions.at(-1), 'idle')
  assert.equal(arrivingReplies().length, 0)
})

test('social replies animate only the latest response and cannot introduce settings links or writes', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const motions = [], requests = []
  let reaction = 'dance'
  const ui = await mount(t, async (path, options) => {
    if (path === '/api/ai/settings') return { enabled: true, provider: 'compatible' }
    requests.push(JSON.parse(options.body))
    return { mode: 'social', answer: '我来跳两步！', reaction,
      topics: [{ id: 'display', section: 'sec-display', title: '不相关的教程' }],
      suggestion: { kind: 'display', field: 'fontSize', value: 18 } }
  }, { onMotion: motion => motions.push(motion) })
  assert.match(ui.text(), /我是小映/)
  const poses = pose => ui.all().filter(el => el.props['data-pose'] === pose)
  await ui.ask('跳个舞')
  assert.equal(poses('dance').length, 1)
  assert.equal(motions.at(-1), 'dance')
  assert.doesNotMatch(ui.text(), /不相关的教程|确认应用|回答来自本版手册/)
  reaction = 'cry'
  await ui.ask('给我哭一个')
  assert.equal(poses('dance').length, 0)
  assert.equal(poses('cry').length, 1)
  assert.deepEqual(requests[1].history, [{ question: '跳个舞', answer: '我来跳两步！' }])
  t.mock.timers.tick(2100)
  await flush()
  assert.equal(poses('cry').length, 0)
  assert.equal(motions.at(-1), 'idle')
  reaction = 'drag'
  await ui.ask('再来一次')
  assert.equal(motions.at(-1), 'reply', 'unknown social reactions use the ordinary reply pose')
  assert.equal(poses('drag').length, 0)
})

test('a source change card works locally with AI disabled; an AI-generated source suggestion never opens it', async t => {
  let mode = 'local'
  const requests = []
  const ui = await mount(t, async (path, options = {}) => {
    requests.push([path, options.method || 'GET'])
    if (path === '/api/ai/settings') return { enabled: false, provider: 'compatible' }
    if (path === '/api/ai/settings-assistant') return { mode, answer: '请选择来源并核对接口。', topics: [],
      suggestion: { kind: 'online_source', template_id: 'liangzi', api_base_url: null } }
    if (path === '/api/online/sources' || path === '/api/online/source-templates') return { items: [] }
    throw new Error('Unexpected request ' + path)
  }, { section: 'sec-online' })
  const card = () => ui.find(el => 'data-assistant-source-migration' in el.props)
  await ui.ask('帮我更新量子资源接口'); await flush()
  assert.ok(card())
  assert.match(ui.text(), /请先在“在线视频源”添加 MacCMS 来源/)
  mode = 'ai'
  await ui.ask('帮我更新量子资源接口'); await flush()
  assert.equal(card(), undefined)
  assert.equal(requests.filter(([path]) => path === '/api/online/sources').length, 1)
  assert.ok(!requests.some(([, method]) => method === 'PATCH'))
})

const diagnosisSource = { id: 'src_a', name: '家庭来源', api_base_url: 'https://old.example/vod' }
const otherSource = { id: 'src_b', name: '另一个来源', api_base_url: 'https://other.example/vod' }
const details = { source_id: diagnosisSource.id, item_id: 'itm_a', external_id: '67', episode_id: 'ep_a', line_id: 'ln_a', error_code: 'http_403', cache_only: false }
const report = { answer: '旧目录两次访问失败，候选接口可以读取原线路。', evidence: [
  { tool: 'current_catalogue', label: '原目录', outcome: 'failed', detail: 'HTTP 403' },
  { tool: 'operator_directory', label: '运营方公告', outcome: 'ok', page_url: 'https://example.com/published' },
], proposal: { kind: 'api', id: 'p1', old_url: diagnosisSource.api_base_url, new_url: 'https://new.example/vod', changes: [
  { kind: 'api', old_host: 'old.example', new_host: 'new.example' }], expires_at: 4102444800 } }
const diagnosisQuestion = '为什么在线资源无法播放了？'

async function diagnoseChat(t, { opts = {}, items = [diagnosisSource], diagnose = () => report,
  confirm = () => ({ status: 'applied', answer: '已经替换原接口并复测', evidence: [] }), chat = {} } = {}) {
  const calls = []
  const request = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body)
    calls.push({ path, body, signal: options.signal })
    if (path === '/api/ai/settings') return { enabled: false, provider: 'compatible' }
    if (['/api/ai/assistant', '/api/ai/settings-assistant'].includes(path)) return { answer: '正在分析播放问题。', mode: 'local',
      topics: [], diagnose_online: body.question !== '新话题', ...chat }
    if (path === '/api/online/sources') return { items }
    if (path === '/api/online/source-templates') return { items: [{ id: 'liangzi', api_base_url: 'https://new.example/vod', known_api_urls: [diagnosisSource.api_base_url] }] }
    if (path.endsWith('/confirm')) return confirm()
    if (path.endsWith('/diagnosis')) return diagnose(body)
    assert.fail(path)
  }
  const ui = await mount(t, request, { channel: 'online', diagnosisContext: details, ...opts })
  const button = label => ui.find(el => el.type === 'button' && nodeText(el).includes(label))
  const posts = ending => calls.filter(row => row.path.endsWith(ending) && row.body)
  return { ...ui, calls, button, posts }
}

test('browsing uses the active episode, one text confirmation and readback; replay waits for a click', async t => {
  const inspection = deferred(), write = deferred()
  let replay = 0
  const ui = await diagnoseChat(t, { items: [diagnosisSource, otherSource], diagnose: () => inspection.promise,
    confirm: () => write.promise, opts: { diagnosisContext: { ...details, replay: () => replay++ }, onReplay: run => run() } })
  await ui.ask(diagnosisQuestion)
  await flush()
  assert.match(ui.text(), /我正在帮你分析/)
  const { source_id, ...bound } = details
  assert.deepEqual(ui.posts('/diagnosis').map(call => [call.path, call.body]), [
    ['/api/online/sources/' + source_id + '/diagnosis', { question: diagnosisQuestion, ...bound }],
  ])
  inspection.resolve(report); await flush()
  assert.match(ui.text(), /HTTP 403/)
  assert.ok(ui.find(el => el.props.href === 'https://example.com/published'))
  assert.match(ui.text(), /old\.example 换为 new\.example/)
  const agree = ui.button('同意修改')
  await ui.ask('同意'); await flush()
  await agree.props.onClick()
  await ui.ask('同意')
  assert.deepEqual(ui.posts('/confirm').map(row => row.body), [{}])
  assert.equal(ui.posts('/api/ai/assistant').length, 1)
  write.resolve({ status: 'applied', answer: '已保存并复测目录', evidence: [] }); await flush()
  assert.match(ui.text(), /已保存并复测/)
  assert.equal(ui.calls.filter(call => call.path === '/api/online/sources').length, 2)
  assert.equal(replay, 0)
  await ui.button('重新播放').props.onClick(); assert.equal(replay, 1)
})

test('named operators and manual source selection do not carry another source’s episode identifiers', async t => {
  const duplicate = { ...diagnosisSource, id: 'src_c' }
  const ui = await diagnoseChat(t, { opts: { channel: undefined, section: 'sec-online', diagnosisContext: { ...details, source_id: otherSource.id } },
    chat: { diagnosis_template_id: 'liangzi' }, items: [diagnosisSource, otherSource, duplicate] })
  await ui.ask('为什么量子资源无法播放？'); await flush()
  assert.equal(ui.posts('/diagnosis').length, 0)
  ui.find(el => el.props.id === 'assistant-diagnosis-source').props['onUpdate:modelValue'](duplicate.id)
  await flush()
  assert.deepEqual(ui.posts('/diagnosis').map(row => row.body), [{ question: '为什么量子资源无法播放？' }])
  assert.ok(ui.posts('/diagnosis')[0].path.includes(duplicate.id))
  await ui.ask('取消'); await ui.ask('同意'); await flush()
  assert.equal(ui.posts('/confirm').length, 0)
  assert.equal(ui.posts('/api/ai/settings-assistant').length, 1)
})

test('explicit direct comparison can produce a media-only proposal, and conflicts require fresh diagnosis', async t => {
  const ui = await diagnoseChat(t, { diagnose: body => body.compare_direct
    ? { ...report, answer: '代理失败、直连读取成功。', proposal: { kind: 'media_direct', id: 'p1', expires_at: 4102444800 } }
    : { answer: '媒体出口返回 HTTP 403。', evidence: [], can_compare_direct: true }, confirm: () => { throw Object.assign(new Error(), { status: 409 }) } })
  await ui.ask(diagnosisQuestion); await flush()
  assert.equal(ui.posts('/diagnosis')[0].body.compare_direct, undefined)
  await ui.button('允许临时直连测试本集').props.onClick(); await flush()
  assert.equal(ui.posts('/diagnosis')[1].body.compare_direct, true)
  assert.match(ui.text(), /当前播放出口代理新的播放出口直连/)
  await ui.button('同意修改').props.onClick(); await flush()
  assert.match(ui.text(), /已变化，请重新诊断/)
  await ui.ask('同意')
  assert.equal(ui.posts('/confirm').length, 1)
  await ui.button('重新诊断').props.onClick(); await flush()
  assert.equal(ui.posts('/diagnosis').length, 3)
})

test('locked access, changed contexts and unrelated questions never apply a prior proposal', async t => {
  const unlocks = []
  const ui = await diagnoseChat(t, { opts: { needsDiagnosisUnlock: true, onUnlockDiagnosis: q => unlocks.push(q) } })
  await ui.ask(diagnosisQuestion); await flush()
  assert.equal(ui.calls.some(row => row.path.startsWith('/api/online/')), false)
  await ui.button('验证并诊断').props.onClick()
  assert.deepEqual(unlocks, [diagnosisQuestion])
  ui.props.needsDiagnosisUnlock = false; await flush()
  await ui.ask(diagnosisQuestion); await flush()
  assert.ok(ui.button('同意修改'))
  ui.props.diagnosisContext = { ...details, episode_id: 'ep_b' }; await flush()
  assert.equal(ui.button('同意修改'), undefined)
  await ui.ask('同意'); await flush()
  assert.equal(ui.posts('/confirm').length, 0)
  await ui.ask(diagnosisQuestion); await flush()
  await ui.ask('新话题'); await flush()
  assert.equal(ui.button('同意修改'), undefined)
})

test('after explicit unlock a single diagnosis can resume the episode even if the player closed during verification', async t => {
  const ui = await diagnoseChat(t, { opts: { initialDiagnosisQuestion: diagnosisQuestion, initialDiagnosisContext: details,
    diagnosisContext: { source_id: otherSource.id } }, items: [diagnosisSource, otherSource] })
  assert.equal(ui.posts('/diagnosis').length, 1)
  assert.ok(ui.posts('/diagnosis')[0].path.includes(diagnosisSource.id))
  assert.equal(ui.posts('/diagnosis')[0].body.episode_id, details.episode_id)
  await ui.ask(diagnosisQuestion); await flush()
  assert.ok(ui.posts('/diagnosis')[1].path.includes(otherSource.id))
  assert.equal(ui.posts('/diagnosis')[1].body.episode_id, undefined)
})

test('expired proposals, uncommitted source forms and model-generated tool flags cannot write', async t => {
  let expired = true
  const access = Vue.reactive({ blocked: '先保存或放弃待编辑的来源。', setBusy() {}, saved() {} })
  const ui = await diagnoseChat(t, { opts: { onlineSources: access }, diagnose: () => expired
    ? { ...report, proposal: { ...report.proposal, expires_at: 1 } } : report })
  await ui.ask(diagnosisQuestion); await flush()
  assert.equal(ui.button('同意修改'), undefined)
  assert.match(ui.text(), /方案已过期/)
  await ui.ask('同意'); await flush()
  expired = false
  await ui.button('重新诊断').props.onClick(); await flush()
  assert.ok(ui.button('同意修改').props.disabled)
  await ui.ask('同意'); await ui.button('同意修改').props.onClick()
  assert.equal(ui.posts('/confirm').length, 0)
  access.blocked = ''
  await ui.ask('同意'); await flush()
  assert.equal(ui.posts('/confirm').length, 1)
  const other = await diagnoseChat(t, { chat: { mode: 'ai' } })
  await other.ask(diagnosisQuestion); await flush()
  assert.equal(other.calls.some(row => row.path.startsWith('/api/online/')), false)
})

test('the settings editor receives the reread source if another edit completed during verification', async t => {
  const rows = [diagnosisSource], saved = []
  const latest = { ...diagnosisSource, name: '管理员刚刚更新的名称', api_base_url: report.proposal.new_url }
  const ui = await diagnoseChat(t, { items: rows, opts: { channel: undefined, section: 'sec-online',
    onlineSources: { setBusy() {}, saved: row => saved.push(row) } }, confirm: () => {
    rows[0] = latest
    return { status: 'applied', answer: '接口已经替换。', source: diagnosisSource, evidence: [] }
  } })
  await ui.ask(diagnosisQuestion); await flush()
  await ui.button('同意修改').props.onClick(); await flush()
  assert.deepEqual(saved, [latest])
})
