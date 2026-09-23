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

// 默认勾选：做种阻断 / 绝对集号风险剧不勾（需用户显式确认）
export function defaultChecked(plan) {
  return !(plan && (plan.blocked || plan.absolute_risk))
}

export function showFlags(plan) {
  const flags = []
  if (plan && plan.blocked) flags.push('做种阻断')
  if (plan && plan.absolute_risk) flags.push('绝对集号风险')
  if (plan && (plan.manual || []).length) flags.push(`需手动 ${plan.manual.length}`)
  return flags
}
