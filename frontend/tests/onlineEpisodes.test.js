import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, nodeText } from './helpers/renderHarness.js'
import { ONLINE_EPISODE_GROUP_SIZE, onlineEpisodeGroups, onlineEntryIndex, onlineEntryPlayable } from '../src/onlineEpisodes.js'

const SOURCE = 'src_' + '1'.repeat(32), ITEM = 'itm_' + '2'.repeat(32), LINE = 'ln_' + '3'.repeat(32)
const OTHER_LINE = 'ln_' + '4'.repeat(32), OTHER_ITEM = 'itm_' + '5'.repeat(32)
function identity(number, extra = {}) {
  return { source_id: SOURCE, item_id: ITEM, kind: 'episode', episode_id: 'ep_' + number.toString(16).padStart(32, '0'), line_id: LINE, ...extra }
}
function entries(count) { return Array.from({ length: count }, (_, index) => ({ label: `第${index + 1}集`, supported: true, identity: identity(index + 1), transport: 'mp4' })) }
function freeze(value) { Object.values(value).filter(item => item && typeof item === 'object').forEach(freeze); return Object.freeze(value) }

test('500 provider entries remain in bounded positional groups without renaming, filtering or mutation', () => {
  const rows = entries(500)
  rows[50] = { label: '2026-10-29上', supported: false, reason: 'host_not_allowed', identity: null }
  rows[100].label = '番外·特别篇'
  freeze(rows)
  const asc = onlineEpisodeGroups(rows)
  assert.equal(ONLINE_EPISODE_GROUP_SIZE, 50)
  assert.equal(asc.length, 10)
  assert.deepEqual(asc.map(group => group.label), ['1–50', '51–100', '101–150', '151–200', '201–250', '251–300', '301–350', '351–400', '401–450', '451–500'])
  assert.ok(asc.every(group => group.entries.length === 50))
  assert.equal(asc[1].entries[0].entry, rows[50])
  assert.equal(asc[2].entries[0].entry.label, '番外·特别篇')
  const desc = onlineEpisodeGroups(rows, 'desc')
  assert.deepEqual(desc.map(group => group.index), [9, 8, 7, 6, 5, 4, 3, 2, 1, 0])
  assert.equal(desc[0].entries[0].entry.label, '第500集')
  assert.equal(desc[9].entries.at(-1).entry.label, '第1集')
  assert.equal(rows[0].label, '第1集')
  assert.deepEqual(onlineEpisodeGroups(null), [])
  assert.equal(onlineEpisodeGroups(entries(50)).length, 1)
  assert.equal(onlineEpisodeGroups(entries(51))[1].entries.length, 1)
})

test('progress matches every stable identity field and cannot borrow an item or line', () => {
  const rows = entries(3)
  assert.equal(onlineEntryIndex(rows, identity(2), ITEM, LINE), 1)
  for (const changed of [{ line_id: OTHER_LINE }, { item_id: OTHER_ITEM }, { source_id: 'src_' + '6'.repeat(32) }, { episode_id: identity(3).episode_id }]) {
    const expected = changed.episode_id ? 2 : -1
    assert.equal(onlineEntryIndex(rows, identity(2, changed), ITEM, LINE), expected)
  }
  assert.equal(onlineEntryIndex(rows, identity(2), OTHER_ITEM, LINE), -1)
  assert.equal(onlineEntryIndex(rows, identity(2), ITEM, OTHER_LINE), -1)
  assert.equal(onlineEntryIndex(rows, { ...identity(2), line_id: undefined }, ITEM, LINE), -1)
  assert.equal(onlineEntryIndex(rows, { ...identity(2), caller_url: 'https://example.invalid/' }, ITEM, LINE), -1)
  assert.equal(onlineEntryIndex(rows, null, ITEM, LINE), -1)
  rows[1].supported = false
  assert.equal(onlineEntryIndex(rows, identity(2), ITEM, LINE), -1)
  assert.equal(onlineEntryPlayable(rows[1], ITEM, LINE), false)
  assert.equal(onlineEntryPlayable({ supported: true, identity: {} }, ITEM, LINE), false)
})

async function setup(t, initial = {}, { focusCalls, scrollCalls } = {}) {
  const props = Vue.reactive({ itemKey: ITEM, lineId: LINE, entries: entries(123), busy: false, currentIdentity: null, lastIdentity: null, ...initial })
  const played = []
  const overrides = {}
  if (focusCalls) overrides['./JzButton.vue'] = { default: {
    inheritAttrs: false, props: ['disabled', 'variant'],
    setup(buttonProps, { attrs, slots, expose }) {
      const el = { focus: options => focusCalls.push({ index: attrs['data-online-entry-index'], options }),
        scrollIntoView: options => scrollCalls.push({ index: attrs['data-online-entry-index'], options }) }
      expose({ el, focus: options => el.focus(options) })
      return () => Vue.h('button', { ...attrs, disabled: buttonProps.disabled }, slots.default?.())
    },
  } }
  const component = await loadSfc(new URL('../src/components/OnlineEpisodes.vue', import.meta.url), overrides)
  // The reactive object lets a later progress response update this mount.
  const active = renderHarness(component, Object.assign(props, { onPlay: entry => played.push(entry) }))
  t.after(() => active.app.unmount())
  await flush()
  return { ui: active, props, played }
}
const attr = (ui, name) => ui.find(node => node.props[name] !== undefined)
const visible = ui => ui.all().filter(node => node.type === 'button' && node.props['data-online-entry'] !== undefined)
function choose(ui, name, value) { attr(ui, name).props.onChange({ target: { value } }) }

test('an asynchronously loaded last identity chooses its range without scrolling, focusing or playing', async t => {
  const focusCalls = [], scrollCalls = []
  const { ui, props, played } = await setup(t, { entries: [], busy: true }, { focusCalls, scrollCalls })
  props.entries = entries(123); props.lastIdentity = identity(117); props.busy = false
  await flush()
  assert.equal(attr(ui, 'data-online-entry-group').props.value, '2')
  assert.equal(visible(ui).length, 23)
  assert.equal(attr(ui, 'data-online-entry-last').props['data-online-entry'], '第117集')
  assert.deepEqual(focusCalls, [])
  assert.deepEqual(scrollCalls, [])
  assert.deepEqual(played, [])
})

test('sort and range controls only change visible provider positions and never playback order', async t => {
  const rows = entries(123); rows[100].label = '番外·特别篇'; rows[122].label = '终章（下）'
  const { ui, played } = await setup(t, { entries: rows })
  assert.equal(visible(ui)[0].props['data-online-entry'], '第1集')
  choose(ui, 'data-online-entry-order', 'desc'); await flush()
  assert.equal(attr(ui, 'data-online-entry-group').props.value, '2')
  assert.equal(visible(ui)[0].props['data-online-entry'], '终章（下）')
  assert.equal(visible(ui).at(-1).props['data-online-entry'], '番外·特别篇')
  choose(ui, 'data-online-entry-group', '1'); await flush()
  assert.equal(visible(ui)[0].props['data-online-entry'], '第100集')
  assert.equal(visible(ui).length, 50)
  choose(ui, 'data-online-entry-order', 'asc'); await flush()
  assert.equal(visible(ui)[0].props['data-online-entry'], '第1集')
  assert.equal(rows[0].label, '第1集')
  assert.equal(rows[122].label, '终章（下）')
  assert.deepEqual(played, [])
})

test('explicit last-position navigation waits for a new range and only focuses its stable entry', async t => {
  const focusCalls = [], scrollCalls = []
  const { ui, played } = await setup(t, { lastIdentity: identity(117) }, { focusCalls, scrollCalls })
  choose(ui, 'data-online-entry-group', '0'); await flush()
  assert.equal(visible(ui).length, 50)
  const result = await attr(ui, 'data-online-locate-last').props.onClick()
  await flush()
  assert.equal(result, true)
  assert.equal(attr(ui, 'data-online-entry-group').props.value, '2')
  assert.deepEqual(focusCalls, [{ index: 116, options: { preventScroll: true } }])
  assert.equal(scrollCalls[0].index, 116)
  assert.deepEqual(played, [])
})

test('a changed line or item invalidates pending focus and discards old selection and direction', async t => {
  const focusCalls = [], scrollCalls = []
  const { ui, props, played } = await setup(t, { lastIdentity: identity(117) }, { focusCalls, scrollCalls })
  choose(ui, 'data-online-entry-order', 'desc'); await flush()
  const locating = attr(ui, 'data-online-locate-last').props.onClick()
  props.lineId = OTHER_LINE
  props.entries = entries(3).map(entry => ({ ...entry, identity: { ...entry.identity, line_id: OTHER_LINE } }))
  await flush()
  assert.equal(await locating, false)
  assert.equal(attr(ui, 'data-online-entry-order').props.value, 'asc')
  assert.equal(attr(ui, 'data-online-locate-last'), undefined)
  assert.equal(attr(ui, 'data-online-entry-last'), undefined)
  props.lineId = LINE; props.itemKey = OTHER_ITEM; props.entries = entries(2)
  await flush()
  assert.ok(visible(ui).every(node => node.props.disabled))
  assert.deepEqual(focusCalls, [])
  assert.deepEqual(scrollCalls, [])
  assert.deepEqual(played, [])
})

test('manual browsing is not displaced by later progress and current playing identity takes initial priority', async t => {
  const { ui, props } = await setup(t, { lastIdentity: identity(117), currentIdentity: identity(52) })
  assert.equal(attr(ui, 'data-online-entry-group').props.value, '1')
  const current = attr(ui, 'data-online-entry-current')
  assert.equal(current.props['data-online-entry'], '第52集')
  assert.equal(current.props['aria-current'], 'true')
  assert.match(nodeText(current), /正在播放/)
  choose(ui, 'data-online-entry-group', '0'); await flush()
  props.lastIdentity = identity(118); props.currentIdentity = identity(53)
  await flush()
  assert.equal(attr(ui, 'data-online-entry-group').props.value, '0')
  assert.equal(visible(ui)[0].props['data-online-entry'], '第1集')
})

test('unsupported and busy entries stay disabled and cannot emit play even through direct callbacks', async t => {
  const rows = entries(3); rows[1] = { ...rows[1], supported: false, reason: 'host_not_allowed', identity: null }
  const { ui, props, played } = await setup(t, { entries: rows, reasonLabel: reason => `原因：${reason}` })
  assert.equal(visible(ui)[1].props.disabled, true)
  assert.match(ui.text(), /原因：host_not_allowed/)
  visible(ui)[1].props.onClick(); assert.deepEqual(played, [])
  visible(ui)[0].props.onClick(); assert.equal(played.length, 1)
  assert.equal(played[0].identity.episode_id, identity(1).episode_id)
  props.busy = true; await flush()
  visible(ui)[2].props.onClick(); choose(ui, 'data-online-entry-order', 'desc'); await flush()
  assert.equal(played.length, 1)
  assert.equal(attr(ui, 'data-online-entry-order').props.value, 'asc')
})

test('a single movie version retains its label and works without browser DOM focus methods', async t => {
  const row = { label: '原版', supported: true, identity: { source_id: SOURCE, item_id: ITEM, kind: 'movie', episode_id: null, line_id: LINE } }
  const { ui, played } = await setup(t, { kind: 'movie', entries: [row], lastIdentity: row.identity })
  assert.equal(attr(ui, 'data-online-entry-order'), undefined)
  assert.equal(attr(ui, 'data-online-entry-group'), undefined)
  assert.match(ui.text(), /播放版本/)
  assert.equal(await attr(ui, 'data-online-locate-last').props.onClick(), true)
  assert.deepEqual(played, [])
  visible(ui)[0].props.onClick()
  assert.equal(played[0].label, '原版')
})
