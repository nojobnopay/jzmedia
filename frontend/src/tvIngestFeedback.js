const fileFields = [
  ['replaced', 'replaced_episode', '识别替换', '集'],
  ['changed', 'changed_episode', '更新片源', '集'],
  ['removed', 'removed_episode', '清理失效记录', '条'],
  ['review', 'tv_reconcile_review', '待确认', '项'],
]
const count = value => Math.max(0, Math.trunc(Number(value) || 0))
const issueKey = issue => typeof issue === 'string' ? issue : `${issue?.code || ''}:${issue?.message || ''}`

export function ingestionMetadataResults(summary = {}, libraryId) {
  const plans = summary.plans || []
  const rows = summary.metadata_results?.length ? summary.metadata_results
    : plans.map(plan => ({ ...plan.metadata, show_id: plan.show_id, library_id: plan.library_id }))
  const libraries = new Map(plans.map(plan => [Number(plan.show_id), plan.library_id]))
  const unique = new Map()
  for (const [index, row] of rows.entries()) {
    const library = row.library_id ?? libraries.get(Number(row.show_id))
    if (libraryId != null && library != null && Number(library) !== Number(libraryId)) continue
    unique.set(row.show_id == null ? `row:${index}` : Number(row.show_id), row)
  }
  return [...unique.values()]
}

export function fileReconciliationText(results = [], scanCounts) {
  const parts = []
  for (const [field, status, label, unit] of fileFields) {
    // Scan counters and metadata.files describe the same work, not two batches.
    const total = scanCounts && Object.hasOwn(scanCounts, status) ? count(scanCounts[status])
      : results.reduce((sum, row) => sum + count(row.files?.[field]), 0)
    if (total) parts.push(`${label} ${total} ${unit}`)
  }
  if (results.some(row => row.files?.incomplete)) parts.push('文件检查未完成，已保留原记录')
  return parts.join(' · ')
}

export function hasMetadataIssues(metadata = {}) {
  const fileIssues = new Set((metadata.files?.issues || []).map(issueKey))
  return (metadata.issues || []).some(issue => !fileIssues.has(issueKey(issue)))
    || String(metadata.status || '').startsWith('error') || metadata.status === 'failed'
}

export function ingestionMetadataText(metadata = {}) {
  const base = count(metadata.filled) ? `已补全 ${count(metadata.filled)} 集资料`
    : hasMetadataIssues(metadata) ? '部分资料尚未补全，已保留文件与现有资料' : '资料已检查'
  const files = fileReconciliationText([metadata])
  return files ? `${base} · ${files}` : base
}
