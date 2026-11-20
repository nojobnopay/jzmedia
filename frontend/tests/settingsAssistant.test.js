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
