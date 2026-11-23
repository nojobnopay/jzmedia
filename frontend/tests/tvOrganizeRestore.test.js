import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { loadSfc } from './helpers/loadSfc.js'
import { flush, nodeText, renderHarness } from './helpers/renderHarness.js'

test('TV organization undo displays conflict paths and permits another preview after confirmation', async t => {
  const requests = []
  const source = 'Demo/Season 01/E01.mkv'
  const oldWindow = globalThis.window
  let ui
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  t.after(() => { ui?.app.unmount(); globalThis.window = oldWindow })
  t.mock.timers.enable({ apis: ['setInterval'] })
  const component = await loadSfc(new URL('../src/components/TvOrganizePanel.vue', import.meta.url), {
    vue: { ...Vue, vModelCheckbox: {}, vModelSelect: {} },
    '../useTvIngestFlow.js': { useTvIngestFlow: () => ({ flow: { reviews: async () => [] } }) },
    '../api.js': { api: async (url, options) => {
      if (url.endsWith('history?limit=30&library_id=3')) return { batches: [] }
      if (options) {
        requests.push(JSON.parse(options.body))
        return { job_id: 'undo' }
      }
      const summary = requests.at(-1).dry_run
        ? { dry_run: true, total: 1, conflicts: 1, batch_id: 'batch-a', preview_id: 'plan-a', plans: [{ title: 'Demo', conflicts: [{ from: source, reason: '源已变化' }] }] }
        : { skipped: 1, failed: 0, restored: 0, errors: [source + '：原路径已被占用'] }
      return { state: 'done', summary }
    } },
  })
  ui = renderHarness(component, { active: true, library: { id: 3, enabled: true } })
  const click = async label => {
    ui.find(n => n.type === 'button' && nodeText(n).trim() === label).props.onClick()
    await flush()
  }
  const poll = async () => { t.mock.timers.tick(1200); await flush() }
  const execute = () => ui.find(n => n.type === 'button' && nodeText(n).trim() === '执行撤销')
  assert.equal(execute().props.disabled, true)
  await click('预览撤销')
  await poll()
  await click('执行撤销')
  await click('空目录')
  assert.equal(execute().props.disabled, true)
  await click('预览撤销')
  await poll()
  assert.ok(ui.text().includes(source + '：源已变化'))
  await click('执行撤销')
  assert.equal(requests.length, 2)
  await click('确认执行撤销')
  assert.equal(requests.at(-1).batch_id, 'batch-a')
  assert.equal(requests.at(-1).preview_id, 'plan-a')
  assert.equal(requests.at(-1).kinds.includes('rmdir'), false)
  await poll()
  assert.ok(ui.text().includes(source + '：原路径已被占用'))
  await click('预览撤销')
  assert.deepEqual(requests.map(r => r.dry_run), [true, true, false, true])
})
