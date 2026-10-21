import test from 'node:test'
import assert from 'node:assert/strict'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush } from './helpers/renderHarness.js'

const manifest = () => ({ state: 'ready', completion: 'known_tail_missing', duration: 9449.147,
  interval: 10, count: 945, available_count: 925, coverage_end: 9250,
  columns: 5, rows: 5, width: 480, height: 270, pages: Array.from({ length: 37 }, (_, i) => 'page' + i),
  warning: { code: 'confirmed_tail_damage', message: '已确认片源尾部损坏，末尾部分没有进度预览' } })

async function seekbar(t, data = manifest()) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'Image')
  Object.defineProperty(globalThis, 'Image', { configurable: true, value: class { set src(_value) {} } })
  const component = await loadSfc(new URL('../src/components/PlayerSeekbar.vue', import.meta.url))
  const ui = renderHarness(component, { manifest: data, duration: 9449.147, value: 0 })
  t.after(() => { ui.app.unmount(); if (previous) Object.defineProperty(globalThis, 'Image', previous); else delete globalThis.Image })
  const wrap = ui.find(node => node.props.class === 'seekbar')
  wrap.getBoundingClientRect = () => ({ width: 390, left: 0, right: 390 })
  wrap.closest = () => null
  await flush()
  return { ...ui, hover: async time => { ui.find(node => node.type === 'input').props.onInput({ target: { value: time } }); await flush() } }
}

test('the real seekbar hides images at the exclusive tail boundary while keeping the original seek duration', async t => {
  const ui = await seekbar(t)
  await ui.hover(9249)
  assert.ok(ui.find(node => node.props.class === 'preview-image'))
  await ui.hover(9250)
  assert.equal(ui.find(node => node.props.class === 'preview-image'), undefined)
  assert.match(ui.text(), /片尾缺少预览/)
  assert.doesNotMatch(ui.text(), /可在设置中生成/)
  assert.equal(ui.find(node => node.type === 'input').props.max, 9449)
  await ui.hover(9440)
  assert.equal(ui.find(node => node.props.class === 'preview-image'), undefined)
})

test('the real seekbar rejects malformed coverage instead of suggesting a normal preview generation', async t => {
  const ui = await seekbar(t, { ...manifest(), coverage_end: Infinity })
  await ui.hover(100)
  assert.equal(ui.find(node => node.props.class === 'preview-image'), undefined)
  assert.match(ui.text(), /预览覆盖信息无效/)
  assert.doesNotMatch(ui.text(), /可在设置中生成/)
})
