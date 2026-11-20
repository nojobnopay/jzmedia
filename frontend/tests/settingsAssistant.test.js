import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, deferred, flush, nodeText } from './helpers/renderHarness.js'

async function mount(t, request, extra = {}) {
  const oldDocument = globalThis.document, oldWindow = globalThis.window
  globalThis.document = { activeElement: {}, body: {} }
  globalThis.window = { matchMedia: () => ({ matches: false }) }
  const component = await loadSfc(new URL('../src/components/SettingsAssistantDialog.vue', import.meta.url), {
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
  const textbox = () => ui.find(el => el.type === 'input' && el.props.id === 'settings-question')
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
  assert.equal(ui.textbox(), undefined)
  retry = true
  await ui.find(el => el.type === 'button' && nodeText(el) === '重试').props.onClick()
  await flush()
  assert.match(ui.text(), /还缺少 API Key/)
  assert.equal(ui.textbox(), undefined)
  state = { enabled: true, provider: 'compatible', api_key_set: false }
  ui.props.settingsVersion++
  await flush()
  assert.ok(ui.textbox())
  assert.equal(reads, 3)
})

test('section change keeps completed chat, discards a stale reply, and permits a new request immediately', async t => {
  const pending = deferred(), sent = []
  const ui = await mount(t, async (path, options) => {
    if (path === '/api/ai/settings') return { enabled: true, provider: 'deepseek', api_key_set: true }
    sent.push(options)
    if (sent.length === 2) return pending.promise
    return { answer: sent.length === 1 ? '已完成的说明' : '新分区的说明', topics: [], mode: 'ai' }
  })
  await ui.ask('最初的提问')
  const late = ui.ask('还没返回的提问')
  ui.props.section = 'sec-display'
  await flush()
  assert.equal(sent[1].signal.aborted, true)
  await ui.ask('新分区的问题')
  pending.resolve({ answer: '上一分区的过期答复', topics: [], mode: 'ai',
    suggestion: { kind: 'display', field: 'fontSize', value: 18 } })
  await late
  await flush()
  assert.match(ui.text(), /已完成的说明/)
  assert.match(ui.text(), /新分区的说明/)
  assert.doesNotMatch(ui.text(), /上一分区的过期答复|确认应用/)
  assert.deepEqual(JSON.parse(sent[2].body).history, [{ question: '最初的提问', answer: '已完成的说明' }])
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
  assert.equal(ui.textbox(), undefined)
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
  assert.equal(ui.textbox(), undefined)
  assert.deepEqual(calls, ['GET', 'POST', 'GET'])
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
  const response = deferred(), requests = []
  const ui = await mount(t, async (path, options) => {
    if (path === '/api/ai/settings') return { enabled: true, provider: 'compatible' }
    requests.push(JSON.parse(options.body))
    if (requests.length === 1) return response.promise
    return { answer: '当前的配置方法', topics: [], mode: 'ai' }
  })
  const text = '如何设置字幕？'
  const waiting = ui.ask(text)
  await flush()
  assert.match(ui.text(), /如何设置字幕？/)
  assert.match(ui.text(), /正在查找配置方法/)
  response.reject(new Error('服务暂时不可用'))
  await waiting
  await flush()
  assert.match(ui.text(), /提问失败，可重新发送/)
  await ui.find(el => el.type === 'form').props.onSubmit({ preventDefault() {} })
  await flush()
  assert.deepEqual(requests.map(row => row.question), [text, text])
  assert.deepEqual(requests[1].history, [])
  assert.match(ui.text(), /当前的配置方法/)
  assert.doesNotMatch(ui.text(), /提问失败|正在查找配置方法/)
})
