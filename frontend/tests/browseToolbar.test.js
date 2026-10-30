import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { renderHarness, flush } from './helpers/renderHarness.js'

async function mount(t, props) {
  const visibility = (node, binding) => { node.props['data-test-visible'] = binding.value }
  const component = await loadSfc(new URL('../src/components/BrowseToolbar.vue', import.meta.url), {
    vue: { ...Vue, vShow: { beforeMount: visibility, updated: visibility } },
  })
  const ui = renderHarness(component, props)
  t.after(() => ui.app.unmount())
  await flush()
  return ui
}
function invoke(handler, event) {
  for (const listener of Array.isArray(handler) ? handler : [handler]) listener?.(event)
}

test('plain search removes suggestion semantics and leaves ordinary editing keys alone', async t => {
  let moves = 0, closes = 0, prevented = 0
  const ui = await mount(t, { id: 'online', inputId: 'online-query', maxlength: 100, label: '搜索在线视频', suggestionsEnabled: false,
    suggestOpen: true, suggestItems: [{ id: 1, title: '隐藏联想' }], suggestIdx: 0,
    onMove: () => moves++, onClose: () => closes++ })
  const input = ui.find(node => node.type === 'input')
  assert.equal(input.props.id, 'online-query')
  assert.equal(input.props.maxlength, 100)
  for (const name of ['role', 'aria-autocomplete', 'aria-expanded', 'aria-controls', 'aria-activedescendant']) assert.equal(input.props[name], undefined)
  assert.equal(ui.find(node => node.props.role === 'listbox'), undefined)
  for (const key of ['ArrowDown', 'ArrowUp', 'Escape']) invoke(input.props.onKeydown, { key, preventDefault() { prevented++ }, stopPropagation() { prevented++ } })
  assert.deepEqual([moves, closes, prevented], [0, 0, 0])
})

test('existing toolbar defaults preserve the combobox and its keyboard selection behavior', async t => {
  const moves = []
  let closed = 0, prevented = 0
  const ui = await mount(t, { id: 'movies', label: '搜索电影', suggestOpen: true,
    suggestItems: [{ id: 1, title: '电影' }], suggestIdx: 0, onMove: value => moves.push(value), onClose: () => closed++ })
  const input = ui.find(node => node.type === 'input')
  assert.equal(input.props.id, 'movies-input')
  assert.equal(input.props.maxlength, undefined)
  assert.equal(input.props.role, 'combobox')
  assert.equal(input.props['aria-activedescendant'], 'movies-option-0')
  assert.ok(ui.find(node => node.props.role === 'listbox'))
  for (const key of ['ArrowDown', 'ArrowUp', 'Escape']) invoke(input.props.onKeydown, { key, preventDefault() { prevented++ }, stopPropagation() { prevented++ } })
  assert.deepEqual(moves, [1, -1])
  assert.equal(closed, 1)
  assert.equal(prevented, 3)
})

test('busy, disabled and composition states prevent duplicate enter searches', async t => {
  let submissions = 0
  const props = Vue.reactive({ id: 'online', label: '搜索在线视频', suggestionsEnabled: false, loading: true, disabled: false, onEnter: () => submissions++ })
  const ui = await mount(t, props)
  const input = () => ui.find(node => node.type === 'input')
  invoke(input().props.onKeyup, { key: 'Enter', isComposing: false })
  assert.equal(submissions, 0)
  assert.equal(ui.find(node => node.type === 'button' && node.props['aria-label'] === props.label).props.disabled, true)
  props.loading = false
  props.disabled = true
  await flush()
  invoke(input().props.onKeyup, { key: 'Enter', isComposing: false })
  assert.equal(input().props.disabled, true)
  props.disabled = false
  await flush()
  invoke(input().props.onKeyup, { key: 'Enter', isComposing: true })
  assert.equal(submissions, 0)
  invoke(input().props.onKeyup, { key: 'Enter', isComposing: false })
  assert.equal(submissions, 1)
})

test('search-only disabling keeps the query editable for sources that require a query', async t => {
  let submissions = 0, query = ''
  const ui = await mount(t, { id: 'online', label: '搜索在线视频', suggestionsEnabled: false, searchDisabled: true,
    'onUpdate:modelValue': value => { query = value }, onEnter: () => submissions++ })
  const input = ui.find(node => node.type === 'input')
  assert.equal(input.props.disabled, false)
  invoke(input.props.onInput, { target: { value: '新查询' } })
  assert.equal(query, '新查询')
  invoke(input.props.onKeyup, { key: 'Enter', isComposing: false })
  assert.equal(submissions, 0)
  assert.equal(ui.find(node => node.type === 'button' && node.props['aria-label'] === '搜索在线视频').props.disabled, true)
})
