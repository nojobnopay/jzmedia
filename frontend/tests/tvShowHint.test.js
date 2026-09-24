import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  hintNeeds, hintExecBody, hintReasonText,
} from '../src/tvOrganizePlans.js'

test('hintNeeds: needs 或风险/手动/冲突都弹窗', () => {
  assert.equal(hintNeeds(null), false)
  assert.equal(hintNeeds({ needs: false, reason: 'no_plan' }), false)
  assert.equal(hintNeeds({ needs: true }), true)
  assert.equal(hintNeeds({ needs: false, absolute_risk: true }), true)
  assert.equal(hintNeeds({ needs: false, blocked: true }), true)
  assert.equal(hintNeeds({ needs: false, manual: [{ file: 'a', reason: 'x' }] }), true)
  assert.equal(hintNeeds({ needs: false, manual_more: 2 }), true)
  assert.equal(hintNeeds({ needs: false, conflicts: [{ from: 'a' }] }), true)
})

test('hintExecBody: 复用 params，回退 show_id，过滤非法动作', () => {
  const h = { show_id: 7, params: { ids: [7], actions: ['season', 'rename'] } }
  assert.deepEqual(hintExecBody(h, ['season'], false),
    { ids: [7], actions: ['season'], dry_run: false, allow_absolute_shows: [] })
  assert.deepEqual(hintExecBody(h, ['season'], true).allow_absolute_shows, [7])
  // 未选动作时用 hint 默认动作集
  assert.deepEqual(hintExecBody(h, [], false).actions, ['season', 'rename'])
  // 非法动作过滤 + 无 params 时回退 show_id
  assert.deepEqual(hintExecBody({ show_id: 9 }, ['rename', 'nope'], false),
    { ids: [9], actions: ['rename'], dry_run: false, allow_absolute_shows: [] })
})

test('hintReasonText: 各 reason 文案', () => {
  assert.equal(hintReasonText({ needs: true }), '检测到可执行的目录/改名计划')
  assert.equal(hintReasonText({ needs: false, reason: 'absolute' }),
    '该剧依赖绝对集号映射：目录动作可先做，改名需勾选确认')
  assert.equal(hintReasonText({ needs: false, reason: 'unmatched' }),
    '该剧尚未匹配 TMDB：目录动作可先做，正片改名需匹配后进行')
  assert.equal(hintReasonText({ needs: false, reason: 'no_plan' }), '目录已规范，无需整理')
  assert.equal(hintReasonText(null), '')
})
