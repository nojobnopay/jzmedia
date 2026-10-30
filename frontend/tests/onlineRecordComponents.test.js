import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush, nodeText } from './helpers/renderHarness.js'

const artwork = { default: { props: ['path', 'title'], setup: props => () => Vue.h('div', { 'data-test-artwork': props.path }, props.title) } }
const menu = { default: { props: ['label'], setup: (_props, { slots }) => () => Vue.h('div', { 'data-test-menu': true }, slots.default?.()) } }
const overrides = { './OnlineArtwork.vue': artwork, './ActionMenu.vue': menu }
function row(index = 1) {
  return {
    item: { id: `item-${index}`, title: `测试剧集 ${index}`, kind: 'tv' },
    source: { id: 'source-a', name: '来源 A' }, favorite: true, available: true,
    poster_path: `/api/online/artwork/poster-${index}`, episode_label: '第十二集',
    progress: { position: 125, duration: 600, completed: false, updated_at: 1730000000 },
  }
}
async function mount(t, name, props) {
  const component = await loadSfc(new URL(`../src/components/${name}.vue`, import.meta.url), overrides)
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  await flush()
  return ui
}

test('record card gives separate detail and resume actions and preserves item-scoped mutations', async t => {
  const entry = row(), events = []
  const ui = await mount(t, 'OnlineRecordCard', {
    row: entry, onDetail: value => events.push(['detail', value]), onResume: value => events.push(['resume', value]),
    onFavorite: (value, favorite) => events.push(['favorite', value, favorite]), onClear: value => events.push(['clear', value]),
  })
  ui.find(node => node.props['data-online-library-detail'] === entry.item.id).props.onClick()
  ui.find(node => node.props['data-online-resume'] === entry.item.id).props.onClick()
  ui.find(node => node.props['data-online-favorite'] === entry.item.id).props.onClick()
  ui.find(node => node.props['data-online-history-delete'] === entry.item.id).props.onClick()
  assert.deepEqual(events, [['detail', entry], ['resume', entry], ['favorite', entry, false], ['clear', entry]])
  assert.ok(ui.text().includes('第十二集'))
  assert.ok(ui.text().includes('来源 A'))
  assert.ok(ui.text().includes('最近观看'))
  assert.equal(ui.find(node => node.props['data-test-artwork']).props['data-test-artwork'], entry.poster_path)
  assert.equal(ui.find(node => node.props.role === 'progressbar').props['aria-valuenow'], 21)
})

test('unknown duration has no fabricated percentage and completed progress uses replay semantics', async t => {
  const entry = Vue.reactive(row())
  entry.progress.duration = null
  const ui = await mount(t, 'OnlineRecordCard', { row: entry })
  assert.ok(ui.text().includes('已观看 2:05'))
  assert.equal(ui.find(node => node.props.role === 'progressbar'), undefined)
  entry.progress.duration = 600
  entry.progress.position = 900
  await flush()
  assert.equal(ui.find(node => node.props.role === 'progressbar').props['aria-valuenow'], 100)
  entry.progress.completed = true
  await flush()
  assert.ok(ui.text().includes('已看完'))
  assert.equal(ui.find(node => node.props['data-online-resume']).props['aria-label'], '重新播放 测试剧集 1')
})

test('unavailable records retain their explanation and maintenance actions without allowing playback', async t => {
  const entry = { ...row(), available: false, reason: 'disabled_source' }
  const ui = await mount(t, 'OnlineRecordCard', { row: entry, reasonLabel: value => value === 'disabled_source' ? '视频源已停用' : '未知原因' })
  assert.ok(ui.text().includes('视频源已停用'))
  assert.equal(ui.find(node => node.props['data-online-library-detail']).props.disabled, true)
  assert.equal(ui.find(node => node.props['data-online-resume']).props.disabled, true)
  assert.equal(ui.find(node => node.props['data-online-history-delete']).props.disabled, false)
})

test('continue shelf distinguishes empty, loading and failed reads and limits itself to twelve cards', async t => {
  const props = Vue.reactive({ rows: [], loading: false, error: '' })
  let retries = 0
  props.onRetry = () => retries++
  const ui = await mount(t, 'OnlineContinueRow', props)
  assert.equal(ui.find(node => node.props['data-online-continue-row'] !== undefined), undefined)
  props.loading = true
  await flush()
  assert.ok(ui.text().includes('正在读取观看记录'))
  assert.equal(ui.text().includes('暂时没有'), false)
  props.loading = false
  props.error = '读取失败，稍后重试'
  await flush()
  assert.ok(ui.text().includes(props.error))
  ui.find(node => node.type === 'button' && nodeText(node) === '重试').props.onClick()
  assert.equal(retries, 1)
  props.error = ''
  props.rows = Array.from({ length: 20 }, (_, index) => row(index))
  await flush()
  assert.equal(ui.all().filter(node => node.props['data-online-state-item']).length, 12)
  assert.equal(ui.find(node => node.props['data-test-menu']), undefined)
})

test('records view emits tab and page intent, keeps old rows on refresh failure, and never reports loading as empty', async t => {
  const events = []
  const props = Vue.reactive({ rows: [], loading: true, error: '', section: 'continue', offset: 0, total: 0,
    onSection: value => events.push(['section', value]), onPage: value => events.push(['page', value]) })
  const ui = await mount(t, 'OnlineRecordsView', props)
  assert.ok(ui.text().includes('正在读取在线记录'))
  assert.equal(ui.text().includes('暂时没有继续观看'), false)
  props.loading = false
  props.rows = [row()]
  props.total = 41
  props.error = '更新失败，保留已有记录'
  await flush()
  assert.ok(ui.find(node => node.props['data-online-state-item'] === 'item-1'))
  assert.ok(ui.text().includes(props.error))
  ui.find(node => node.props['data-online-library-section'] === 'history').props.onClick()
  ui.find(node => node.type === 'button' && nodeText(node) === '下一页').props.onClick()
  assert.deepEqual(events, [['section', 'history'], ['page', 20]])
  assert.equal(ui.find(node => node.type === 'button' && nodeText(node) === '上一页').props.disabled, true)
})
