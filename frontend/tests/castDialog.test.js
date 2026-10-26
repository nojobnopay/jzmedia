import test from 'node:test'
import assert from 'node:assert/strict'
import { computed, defineComponent, h, ref } from 'vue'
import * as Vue from 'vue'
import { castStateLabel } from '../src/useCasting.js'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush } from './helpers/renderHarness.js'

async function panel(t, initial = {}, props = {}) {
  const image = Object.getOwnPropertyDescriptor(globalThis, 'Image')
  Object.defineProperty(globalThis, 'Image', { configurable: true, value: class { set src(_value) {} } })
  const calls = []
  const session = ref({ id: 'tv-session', active: true, state: 'PLAYING', duration: 300,
    position: 20, position_known: true, last_position: 20, volume: null,
    device: { name: '名字很长的客厅电视，包含中文与特殊字符', volume_control: true }, ...initial })
  const pendingSeek = ref(null)
  const casting = { session, open: ref(true), busy: ref(false), uncertain: ref(false), active: computed(() => !!session.value?.active),
    pendingSeek, seekPending: computed(() => !!pendingSeek.value),
    displayPosition: computed(() => pendingSeek.value?.target ?? (session.value?.position_known ? session.value.position : session.value?.last_position) ?? 0),
    error: ref(''), stateLabel: computed(() => castStateLabel(session.value)),
    control: async (...args) => { calls.push(args); if (args[0] === 'seek') pendingSeek.value = { sessionId: session.value.id, target: args[1] }; return true }, close() {},
    searching: ref(false), searched: ref(false), devices: ref([]), selected: ref('tv'),
    serverUrl: ref(''), manualLocation: ref(''), discover() {}, addManual() {}, play() {} }
  casting.receiverType = ref('dlna')
  casting.appDevices = ref([])
  casting.searchingApps = ref(false)
  casting.appsSearched = ref(false)
  casting.appsError = ref('')
  casting.refreshApps = async () => { calls.push(['refreshApps']) }
  casting.chooseReceiver = async type => { casting.receiverType.value = type; calls.push(['receiver', type]) }
  const component = await loadSfc(new URL('../src/components/CastDialog.vue', import.meta.url), {
    // The host renderer has no DOM events; browser smoke verifies native models.
    vue: { ...Vue, vModelText: {}, vModelRadio: {} },
    './JzDialog.vue': { default: defineComponent({ setup: (_props, { slots }) => () => h('section', [slots.default?.(), slots.footer?.()]) }) },
  })
  const manifest = { state: 'ready', interval: 10, count: 30, columns: 5, rows: 5,
    width: 480, height: 270, pages: ['synthetic-first.jpg', 'synthetic-second.jpg'] }
  const ui = renderHarness(component, { casting, held: true, manifest, ...props })
  t.after(() => { ui.app.unmount(); if (image) Object.defineProperty(globalThis, 'Image', image); else delete globalThis.Image })
  const seek = () => ui.find(node => node.type === 'input' && node.props['aria-label'] === '电视播放进度')
  const volume = () => ui.find(node => node.type === 'input' && node.props['aria-label'] === '设置电视音量')
  const wrap = ui.find(node => node.props.class?.includes?.('seekbar'))
  if (wrap) {
    wrap.getBoundingClientRect = () => ({ width: 280, left: 0, right: 280 })
    wrap.closest = () => null
  }
  await flush()
  return { ...ui, casting, session, calls, seek, volume, wrap }
}

test('TV seek drafts survive polling and use existing thumbnails, then commit one command', async t => {
  const ui = await panel(t)
  const input = ui.seek()
  input.props.onInput({ target: { value: '110' } })
  await flush()
  assert.equal(ui.seek().props.value, 110)
  assert.ok(ui.find(node => node.props.class === 'preview-image'))
  ui.session.value = { ...ui.session.value, position: 25, last_position: 25 }
  await flush()
  assert.equal(ui.seek().props.value, 110, 'A remote poll must not reset a held drag')
  assert.deepEqual(ui.calls, [])
  ui.seek().props.onChange({ target: { value: '110' } })
  await flush()
  assert.deepEqual(ui.calls, [['seek', 110]])
  assert.equal(ui.seek().props.value, 110, 'The submitted target stays visible until the television confirms it')
  assert.match(ui.text(), /正在跳转到 1:50，等待电视确认/)
  assert.match(ui.text(), /电视上次上报：0:25/)
  assert.match(ui.seek().props['aria-valuetext'], /等待电视确认/)
  assert.equal(ui.find(node => node.type === 'button' && node.props['aria-label'] === '前进 10 秒').props.disabled, true)
  ui.session.value = { ...ui.session.value, state: 'TRANSITIONING', position_known: false }
  await flush()
  const pause = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '暂停'))
  assert.ok(pause, 'A pending seek must retain the last confirmed playback action')
  pause.props.onClick()
  assert.deepEqual(ui.calls.at(-1), ['pause'])
  ui.session.value = { ...ui.session.value, position: 27, last_position: 27 }
  await flush()
  assert.equal(ui.seek().props.value, 110, 'An old television update must not move the thumb back')
  ui.session.value = { ...ui.session.value, state: 'PLAYING', position_known: true, position: 111, last_position: 111 }
  ui.casting.pendingSeek.value = null
  await flush()
  assert.equal(ui.seek().props.value, 111)
  assert.doesNotMatch(ui.text(), /待确认/)
})

test('selected subtitles explain the restriction by name and offer the TV App beside a blocked DLNA start', async t => {
  const ui = await panel(t, { active: false }, { hasSelectedSubtitle: true, subtitleLabel: '简体中文 PGS' })
  assert.match(ui.text(), /当前选择了“简体中文 PGS”字幕.*不能传递/)
  const start = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '开始投屏'))
  assert.equal(start.props.disabled, true)
  assert.equal(start.props['aria-describedby'], 'cast-start-hint')
  assert.match(ui.find(node => node.props.id === 'cast-start-hint').text, /字幕无法通过内置投屏传递/)
  const recommend = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '使用 jzmedia-tv 播放'))
  recommend.props.onClick()
  await flush()
  assert.deepEqual(ui.calls, [['receiver', 'jzmedia_tv']])
  assert.match(ui.text(), /请在目标电视打开 jzmedia-tv/)
})

test('a missing receiver selection explains the disabled action and a retryable App error stays distinct from empty', async t => {
  const ui = await panel(t, { active: false })
  ui.casting.selected.value = ''
  await flush()
  assert.match(ui.text(), /请先选择一台电视/)
  ui.casting.receiverType.value = 'jzmedia_tv'
  ui.casting.appsError.value = '电视 App 查询失败，请重试'
  await flush()
  assert.match(ui.text(), /电视 App 查询失败，请重试/)
  assert.doesNotMatch(ui.text(), /尚未找到可接收/)
  assert.match(ui.text(), /无法确认电视 App 是否可用，请刷新重试/)
  const start = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '在电视 App 播放'))
  assert.equal(start.props.disabled, true)
})

test('a server compatibility error remains readable and offers a switch to the TV App', async t => {
  const ui = await panel(t, { active: false })
  ui.casting.error.value = '电视未声明支持此视频格式（video/x-matroska）'
  await flush()
  assert.match(ui.text(), /电视未声明支持此视频格式/)
  const switchApp = ui.find(node => node.type === 'button' && node.props.class?.includes('cast-error-recovery'))
  assert.ok(switchApp)
  switchApp.props.onClick()
  assert.deepEqual(ui.calls, [['receiver', 'jzmedia_tv']])
})

test('an active DLNA receiver offers the TV App route even when it reports playing with no warning', async t => {
  const ui = await panel(t, { position: 0, last_position: 0, warning: '', error: '' })
  assert.match(ui.text(), /电视无画面、无声音或需要字幕/)
  const switchApp = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '改用 jzmedia-tv'))
  assert.ok(switchApp)
  switchApp.props.onClick()
  await flush()
  assert.deepEqual(ui.calls, [['receiver', 'jzmedia_tv']])
})

test('TV App picker shows an online target without choosing it and labels phone-only subtitle limits', async t => {
  const ui = await panel(t, { active: false }, { localSubtitle: true, audioLabel: '音轨2 国语', subtitleLabel: '手机临时字幕' })
  ui.casting.receiverType.value = 'jzmedia_tv'
  ui.casting.appDevices.value = [{ id: 'tv_online', name: '主卧 App', address: '192.168.1.72' }]
  ui.casting.selected.value = ''
  await flush()
  assert.match(ui.text(), /App 会自动打开这部影片/)
  assert.match(ui.text(), /当前字幕只保存在手机，不能直接传给电视/)
  assert.match(ui.text(), /音轨2 国语/)
  const start = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '在电视 App 播放'))
  assert.equal(start.props.disabled, true)
  const radio = ui.find(node => node.type === 'input' && node.props.name === 'cast-app-device')
  assert.equal(radio.props.value, 'tv_online')
})

test('waiting for TV App acknowledgement keeps cancel available without fake playback controls', async t => {
  const ui = await panel(t, { receiver_type: 'jzmedia_tv', state: 'WAITING_FOR_APP', resume_pending: true,
    resume_target: 92, position_known: false, position: 0, last_position: null })
  await flush()
  assert.match(ui.text(), /保持该 App 在电视前台/)
  assert.match(ui.text(), /将从 1:32 开始/)
  assert.ok(!ui.seek())
  const cancel = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '取消本次播放'))
  assert.equal(cancel.props.disabled, false)
  cancel.props.onClick()
  assert.deepEqual(ui.calls, [['stop']])
})

test('single-audio preparation exposes real progress and cancellation rather than Play or Seek', async t => {
  const ui = await panel(t, { state: 'PREPARING', prepare_progress: .4, position_known: false })
  await flush()
  assert.match(ui.text(), /40%/)
  assert.match(ui.text(), /首次准备需要等待/)
  assert.ok(!ui.seek())
  const progress = ui.find(node => node.type === 'progress')
  assert.equal(progress.props.value, .4)
  const cancel = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '取消音轨准备'))
  cancel.props.onClick()
  assert.deepEqual(ui.calls, [['stop']])
})

test('cancelling an unacknowledged handoff shows its stopped state rather than the stale requested target', async t => {
  const ui = await panel(t, { active: false, state: 'STOPPED', resume_pending: true, resume_target: 92 })
  await flush()
  assert.match(ui.text(), /投屏已停止/)
  assert.doesNotMatch(ui.text(), /正在接续/)
})

test('cancelled TV seek gestures send no command and the next keyboard change remains usable', async t => {
  const ui = await panel(t)
  const input = ui.seek()
  input.props.onPointerdown({ clientX: 80, currentTarget: { setPointerCapture() {} }, pointerId: 1 })
  input.props.onInput({ target: { value: '90' } })
  input.props.onPointercancel()
  input.props.onChange({ target: { value: '90' } })
  await flush()
  assert.deepEqual(ui.calls, [])
  assert.equal(ui.seek().props.value, 20)
  ui.seek().props.onKeydown()
  ui.seek().props.onInput({ target: { value: '21' } })
  ui.seek().props.onChange({ target: { value: '21' } })
  assert.deepEqual(ui.calls, [['seek', 21]])
})

test('unknown TV volume is labelled as unknown; dragging sends only the chosen final value', async t => {
  const ui = await panel(t)
  assert.match(ui.text(), /电视未上报音量/)
  assert.doesNotMatch(ui.text(), /50%/)
  assert.match(ui.volume().props['aria-valuetext'], /电视未上报音量/)
  ui.volume().props.onInput({ target: { value: '65' } })
  await flush()
  assert.match(ui.text(), /设置为 65%/)
  ui.session.value = { ...ui.session.value, volume: 30 }
  await flush()
  assert.equal(ui.volume().props.value, 65)
  assert.deepEqual(ui.calls, [])
  ui.volume().props.onChange({ target: { value: '65' } })
  assert.deepEqual(ui.calls, [['volume', 65]])
})

test('cancelled volume and closing control drafts do not issue seek or volume writes', async t => {
  const ui = await panel(t)
  ui.volume().props.onPointerdown()
  ui.volume().props.onInput({ target: { value: '70' } })
  ui.volume().props.onPointercancel()
  ui.volume().props.onChange({ target: { value: '70' } })
  ui.seek().props.onInput({ target: { value: '100' } })
  ui.casting.open.value = false
  await flush()
  assert.deepEqual(ui.calls, [])
  assert.equal(ui.seek().props.value, 20)
  assert.match(ui.text(), /电视未上报音量/)
})

test('pending continuation displays a requested target without inventing TV progress and keeps Stop available', async t => {
  const ui = await panel(t, { resume_pending: true, resume_target: 145, position_known: false,
    position: 0, last_position: null })
  assert.match(ui.text(), /正在接续到 2:25/)
  assert.match(ui.text(), /电视未上报进度/)
  assert.equal(ui.seek().props.disabled, true)
  const stop = ui.find(node => node.type === 'button' && node.children.some(child => child.text === '停止投屏'))
  assert.ok(stop)
  assert.equal(stop.props.disabled, false)
  assert.ok(ui.find(node => node.type === 'button' && node.props['aria-label'] === '后退 10 秒').props.disabled)
})

test('TV skips clamp to the real duration and controls do not expose phone-only track choices', async t => {
  const ui = await panel(t, { position: 295, last_position: 295, volume: 42 })
  const forward = ui.find(node => node.type === 'button' && node.props['aria-label'] === '前进 10 秒')
  forward.props.onClick()
  assert.deepEqual(ui.calls, [['seek', 299]])
  assert.match(ui.text(), /42%/)
  assert.equal(ui.all().some(node => node.type === 'input' && node.props.type === 'number'), false)
  assert.equal(ui.all().some(node => node.type === 'select'), false)
})

test('unknown creation results offer a readback action without another Play or a premature phone handoff', async t => {
  const ui = await panel(t)
  let checked = 0
  ui.casting.uncertain.value = true
  ui.casting.reconcile = () => { checked++ }
  ui.session.value = null
  ui.casting.error.value = '无法确认电视是否已开始播放，手机保持暂停'
  await flush()
  const buttons = ui.all().filter(node => node.type === 'button')
  assert.equal(buttons.some(node => node.children.some(child => /^(开始投屏|手机播放)$/.test(child.text))), false)
  const retry = buttons.find(node => node.children.some(child => child.text === '重新检查投屏状态'))
  assert.ok(retry)
  retry.props.onClick()
  assert.equal(checked, 1)
  assert.match(ui.text(), /手机保持暂停/)
})
