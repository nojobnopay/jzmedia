// 剧集目录整理计划展示辅助（纯函数，node --test 直测）：
// 后端 dry-run 返回每剧分组摘要 {counts, groups[], untouched[], manual[]}，
// 这里负责文案/样例/默认勾选等展示逻辑。

export const ACTION_LABELS = {
  root: '剧根改名',
  seasondir: '季目录规范化',
  wrapper: '包装层拍平',
  season: '补 Season 目录',
  specials: '特典归位',
  extras: '花絮目录上移',
  rename: '正片统一命名',
}

export const MANUAL_LABELS = {
  absolute: '绝对集号风险（Plex 季集拆分可能不同）',
  unmatched: '未匹配 TMDB',
  needs_review: '待确认集号',
  range: '多集区间过长',
  no_title: '缺少剧名',
  exists: '目标名冲突',
}

export function basename(p) {
  const s = String(p || '')
  const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'))
  return i >= 0 ? s.slice(i + 1) : s
}

export function dirname(p) {
  const s = String(p || '')
  const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'))
  return i > 0 ? s.slice(0, i) : ''
}

export function planTotal(plan) {
  const c = (plan && plan.counts) || {}
  return Object.values(c).reduce((a, b) => a + (Number(b) || 0), 0)
}

// 行摘要：`正片统一命名：12 项 → Season 01/`；目录类：`剧根改名：A/ → B/`
export function groupText(g) {
  if (!g) return ''
  const label = g.label || ACTION_LABELS[g.action] || g.action || ''
  if (g.dir) return `${label}：${basename(g.from)}/ → ${basename(g.to)}/`
  const to = basename(g.to)
  return `${label}：${g.count || 0} 项 → ${to ? to + '/' : ''}`
}

export function groupSamples(g, max = 3) {
  const arr = (g && g.samples) || []
  const lines = arr.slice(0, max).map((s) => ({
    from: basename(s.from), to: basename(s.to),
  }))
  return { lines, more: Math.max(0, (g.count || 0) - lines.length) }
}

export function untouchedText(t) {
  const d = basename(t && t.dir)
  return `${d ? d + '/' : '（根）'}：${(t && t.count) || 0} 个（层级过深，需手动整理）`
}

export function manualText(m) {
  const reason = MANUAL_LABELS[m && m.reason] || (m && m.reason) || ''
  const file = basename(m && m.file)
  return `${file}：${reason}`
}

// 每剧徽标文案（只列非零动作）
export function showTotalText(plan) {
  const c = (plan && plan.counts) || {}
  const parts = []
  for (const k of Object.keys(ACTION_LABELS)) {
    if (c[k]) parts.push(`${ACTION_LABELS[k]} ${c[k]}`)
  }
  return parts.join('，')
}

// 默认勾选：做种阻断 / 绝对集号风险剧不勾（需用户显式确认；allowAbs=已确认时勾上）
export function defaultChecked(plan, { allowAbs = false } = {}) {
  if (!plan) return false
  return !(plan.blocked || (plan.absolute_risk && !allowAbs))
}

// 绝对集号风险剧 id 列表（需显式确认才执行正片改名）
export function riskShowIds(plans) {
  return (plans || []).filter((p) => p && p.absolute_risk).map((p) => p.show_id)
}

// 最终归属目录分布（summarize_plan.dir_totals，含无需移动的正片）：
// `Season 01 480 · Season 02 873 …`——预览统计的是"要改什么"，这里回答"执行后每季多少集"。
export function dirTotalsText(plan, max = 8) {
  const rows = (plan && plan.dir_totals) || []
  const parts = rows.slice(0, max).map((r) => `${basename(r.dir) || '剧根'} ${r.count}`)
  if (rows.length > max) parts.push(`…共 ${rows.length} 个目录`)
  return parts.join(' · ')
}

export function showFlags(plan) {
  const flags = []
  if (plan && plan.blocked) flags.push('做种阻断')
  if (plan && plan.absolute_risk) flags.push('绝对集号风险')
  if (plan && (plan.manual || []).length) flags.push(`需手动 ${plan.manual.length}`)
  return flags
}

// 单剧 hint（GET /api/tv/shows/:id/organize-hint）展示辅助：
// needs 为 false 但有风险/手动/冲突时仍弹窗解释原因，不静默。
export function hintNeeds(h) {
  if (!h) return false
  if (h.needs) return true
  return Boolean(h.absolute_risk || h.blocked
    || (h.manual || []).length || (h.manual_more || 0)
    || (h.conflicts || []).length)
}

// hint 弹窗「直接执行」回传体：复用 hint.params， only 显式动作子集。
export function hintExecBody(h, actions, allowAbs) {
  const p = (h && h.params) || {}
  const ids = (p.ids && p.ids.length ? p.ids : (h && h.show_id != null ? [h.show_id] : []))
    .map(Number).filter(Number.isFinite)
  const acts = (actions && actions.length ? actions : (p.actions || []))
    .filter((a) => ACTION_LABELS[a])
  return {
    ids,
    actions: acts,
    dry_run: false,
    allow_absolute_shows: allowAbs && ids.length ? ids : [],
  }
}

// hint 弹窗首行解释文案（needs=true 时为可执行计划提示）。
export function hintReasonText(h) {
  if (!h) return ''
  if (h.needs) return '检测到可执行的目录/改名计划'
  return {
    unmatched: '该剧尚未匹配 TMDB：目录动作可先做，正片改名需匹配后进行',
    absolute: '该剧依赖绝对集号映射：目录动作可先做，改名需勾选确认',
    manual: '部分文件需手动确认（见“需手动处理”），其余目录动作可先做',
    blocked: '该剧含 .torrent（做种保护）：默认跳过，可到设置页放行',
    conflicts: '存在目标冲突：请先手动处理冲突项',
    read_only: '该库为只读：无法整理',
    no_plan: '目录已规范，无需整理',
  }[(h && h.reason) || ''] || ''
}
