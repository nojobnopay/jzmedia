import test from 'node:test'
import assert from 'node:assert/strict'
import { fileReconciliationText, hasMetadataIssues, ingestionMetadataResults, ingestionMetadataText } from '../src/tvIngestFeedback.js'

test('file outcomes share one wording and scan counters do not double count metadata files', () => {
  const files = { replaced: 1, changed: 2, removed: 3, review: 4 }
  const expected = '识别替换 1 集 · 更新片源 2 集 · 清理失效记录 3 条 · 待确认 4 项'
  assert.equal(fileReconciliationText([{ files }]), expected)
  assert.equal(fileReconciliationText([{ files }], { replaced_episode: 1, changed_episode: 2, removed_episode: 3, tv_reconcile_review: 4 }), expected)
  assert.equal(fileReconciliationText([{ files }], { replaced_episode: 0 }), '更新片源 2 集 · 清理失效记录 3 条 · 待确认 4 项')
})

test('old responses without file outcomes keep the original metadata message', () => {
  assert.equal(ingestionMetadataText({ filled: 8 }), '已补全 8 集资料')
  assert.equal(ingestionMetadataText({}), '资料已检查')
  assert.equal(fileReconciliationText(), '')
})

test('file ambiguity does not claim metadata failed; real metadata issues remain retryable', () => {
  const fileIssue = { code: 'ambiguous_replacement', message: '同一集有多个版本，请核对' }
  const result = { status: 'complete', filled: 0, files: { review: 1, issues: [fileIssue] }, issues: [{ ...fileIssue }] }
  assert.equal(hasMetadataIssues(result), false)
  assert.equal(ingestionMetadataText(result), '资料已检查 · 待确认 1 项')
  result.issues.push({ code: 'season_unavailable', message: '分集资料暂不可用' })
  assert.equal(hasMetadataIssues(result), true)
  assert.match(ingestionMetadataText(result), /^部分资料尚未补全/)
})

test('metadata result selection avoids duplicate plan counts and respects the video library', () => {
  const summary = { plans: [{ show_id: 7, library_id: 2, metadata: { filled: 9 } }, { show_id: 8, library_id: 4 }],
    metadata_results: [{ show_id: 7, filled: 1, files: { replaced: 1 } }, { show_id: 8, filled: 2, files: { removed: 2 } }] }
  const rows = ingestionMetadataResults(summary, 2)
  assert.equal(rows.length, 1)
  assert.equal(rows[0].filled, 1)
  assert.equal(fileReconciliationText(rows), '识别替换 1 集')
  delete summary.metadata_results
  assert.equal(ingestionMetadataResults(summary, 2)[0].filled, 9)
})

test('an incomplete file check reports retained records rather than a clean result', () => {
  assert.match(ingestionMetadataText({ files: { incomplete: true, removed: 0 } }), /文件检查未完成，已保留原记录/)
})
