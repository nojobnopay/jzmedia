// 剧集目录整理计划展示辅助（纯函数，node --test 直测）：
// 后端 dry-run 返回每剧分组摘要 {counts, groups[], untouched[], manual[]}，
// 这里负责文案/样例/默认勾选等展示逻辑。

// 整理项目：名称 + 一句人话 + 示例（面板折叠区与行内 chips 共用单源）
export const ACTION_HELP = {
  root: {
    label: '剧根改名',
    desc: '剧文件夹改成「剧名 (年份)」',
    example: 'Breaking.Bad.2008 → 绝命毒师 (2008)',
  },
  seasondir: {
    label: '季目录规范化',
    desc: '季目录统一成 Season NN',
    example: 'season 1 / S04 / 第3季 → Season 01',
  },
  wrapper: {
    label: '包装层拍平',
    desc: '去掉发布组多套的一层目录',
    example: '剧名/Release.Name/Season 01/ → 剧名/Season 01/',
  },
  season: {
    label: '补 Season 目录',
    desc: '散放在剧根下的集移进季目录',
    example: '剧名/01.mkv → 剧名/Season 01/',
  },
  specials: {
    label: '特典归位',
    desc: 'OVA/SP 等特别篇移进 Season 00',
    example: '剧名/OVA01.mkv → 剧名/Season 00/',
  },
  extras: {
    label: '花絮目录上移',
    desc: '幕后/删除片段等目录挪到剧根（文件名不动）',
    example: '剧名/Season 01/Behind The Scenes/ → 剧名/Behind The Scenes/',
  },
  rename: {
    label: '正片统一命名',
    desc: '集文件改成 Plex 模板名',
    example: '01.mkv → 剧名-S01E01-集名.mkv（多版本加 -V2）',
  },
}

export const ACTION_LABELS = Object.fromEntries(
  Object.entries(ACTION_HELP).map(([k, v]) => [k, v.label]))

export const MANUAL_LABELS = {
  absolute: '绝对集号风险（Plex 季集拆分可能不同）',
  unmatched: '未匹配 TMDB',
  needs_review: '待确认集号',
  bad_episode: '集号异常（无法生成规范名）',
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

// 行摘要：`正片统一命名：3 个正片 + 9 个附属 → Season 01/`；目录类：`剧根改名：A/ → B/`
export function groupText(g) {
  if (!g) return ''
  const label = g.label || ACTION_LABELS[g.action] || g.action || ''
  if (g.dir) return `${label}：${basename(g.from)}/ → ${basename(g.to)}/`
  const to = basename(g.to)
  const eps = Number(g.episodes || 0)
  const files = Number(g.files || 0)
  const detail = eps && files ? `${eps} 个正片 + ${files} 个附属`
    : eps ? `${eps} 个正片`
      : files ? `${files} 个附属文件` : `${g.count || 0} 项`
  return `${label}：${detail} → ${to ? to + '/' : ''}`
}

export function groupSamples(g, max = 3) {
  const arr = (g && g.samples) || []
  const lines = arr.slice(0, max).map((s) => ({
    from: basename(s.from), to: basename(s.to), title: `${s.from} → ${s.to}`,
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

// 每剧动作 chips（从 groups 聚合正片/附属/目录数，与展开明细同一口径）
export function planActionChips(plan) {
  const agg = new Map()
  for (const g of (plan && plan.groups) || []) {
    const a = g.action || ''
    const cur = agg.get(a) || { episodes: 0, files: 0, dirs: 0 }
    if (g.dir) cur.dirs += 1
    else {
      cur.episodes += Number(g.episodes || 0)
      cur.files += Number(g.files || 0)
    }
    agg.set(a, cur)
  }
  const parts = []
  for (const k of Object.keys(ACTION_HELP)) {
    const c = agg.get(k)
    if (!c) continue
    const label = ACTION_HELP[k].label
    if (c.dirs) parts.push(`${label} ${c.dirs}`)
    else if (c.episodes && c.files) parts.push(`${label} ${c.episodes}（+${c.files} 附属）`)
    else if (c.episodes) parts.push(`${label} ${c.episodes}`)
    else if (c.files) parts.push(`${label} ${c.files} 附属`)
  }
  return parts
}

// 预览列表分区：有动作=将执行；无动作但有提示（kept/untouched/需手动/冲突）=仅提示
export function splitPlans(plans) {
  const actionPlans = []
  const notePlans = []
  for (const p of plans || []) {
    if (planTotal(p) > 0) actionPlans.push(p)
    else notePlans.push(p)
  }
  return { actionPlans, notePlans }
}

// 「仅提示」一行人话：说明为什么进列表、会不会动文件
export function noteText(plan) {
  if (!plan) return ''
  const parts = []
  const manual = (plan.manual || []).length + Number(plan.manual_more || 0)
  if (manual) parts.push(`需手动 ${manual} 项`)
  if ((plan.conflicts || []).length) parts.push(`冲突 ${plan.conflicts.length} 项`)
  if (plan.untouched_count) parts.push(`深层花絮 ${plan.untouched_count} 个未整理`)
  if (plan.kept_count) parts.push(`保持原名 ${plan.kept_count} 项（本地集）`)
  if (plan.blocked) parts.push('含 .torrent，默认跳过')
  if (!parts.length && (plan.warnings || []).length) parts.push(`提示 ${plan.warnings.length} 条`)
  return parts.join('；') || '无需操作'
}

// 汇总 chips：将执行部数/正片/附属来自 groups；提示计数来自各剧摘要
export function actionTotals(plans) {
  const t = { shows: 0, episodes: 0, files: 0, dirs: 0,
              manual: 0, conflicts: 0, kept: 0, untouched: 0, blocked: 0 }
  for (const p of plans || []) {
    t.shows += 1
    for (const g of (p && p.groups) || []) {
      if (g.dir) t.dirs += 1
      else {
        t.episodes += Number(g.episodes || 0)
        t.files += Number(g.files || 0)
      }
    }
    t.manual += (p.manual || []).length + Number(p.manual_more || 0)
    t.conflicts += (p.conflicts || []).length
    t.kept += Number(p.kept_count || 0)
    t.untouched += Number(p.untouched_count || 0)
    if (p.blocked) t.blocked += 1
  }
  return t
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
  const manual = (plan && plan.manual ? plan.manual.length : 0)
    + Number((plan && plan.manual_more) || 0)
  if (manual) flags.push(`需手动 ${manual}`)
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
