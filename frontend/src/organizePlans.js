// 归档整理计划展示辅助（纯函数，node --test 直测）：
// 目录计划行徽标与「目录内影片改名」明细（用户反馈：目录行只见目录名，怕不改影片）。
export function dirFileCount(plan) {
  return plan && Array.isArray(plan.files) ? plan.files.length : 0
}

export function dirBadge(plan) {
  const n = dirFileCount(plan)
  return n > 0 ? `目录 · ${n} 片` : '目录'
}

function basename(p) {
  const s = String(p || '')
  const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'))
  return i >= 0 ? s.slice(i + 1) : s
}

// 返回 {lines: [{nameFrom, nameTo, from, to}], more}；max 之后的只报数量。
export function dirFileLines(plan, max = 3) {
  const files = plan && Array.isArray(plan.files) ? plan.files : []
  const lines = files.slice(0, Math.max(0, max)).map((f) => ({
    nameFrom: basename(f.from), nameTo: basename(f.to),
    from: f.from || '', to: f.to || '',
  }))
  return { lines, more: Math.max(0, files.length - lines.length) }
}

function dirname(p) {
  const s = String(p || '')
  const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'))
  return i >= 0 ? s.slice(0, i) : ''
}

function stripTrailing(s) {
  return String(s || '').trim().replace(/[/\\]+$/, '')
}

// 逐行动作即时投影（显示用，不改执行参数；与后端 _target_for(target_root) 同构）：
// - skip → 标记保持不动，路径不变
// - 全局=就地下「强制搬到顶层」→ 换前缀为 toDir/<规范目录名>[/<文件名>]
// - auto / 全局已是顶层 → 原样返回（不二次投影）
export function projectPlan(plan, { action = 'auto', orgMode = 'inplace', toDir = '电影' } = {}) {
  if (!plan) return { plan, skipped: false, forcedRelocate: false }
  if (action === 'skip') return { plan, skipped: true, forcedRelocate: false }
  const forced = action === 'relocate' && orgMode !== 'relocate'
  if (!forced || !plan.to) return { plan, skipped: false, forcedRelocate: false }
  const root = stripTrailing(toDir) || '电影'
  if (plan.kind === 'dir') {
    const dir = `${root}/${basename(plan.to)}`
    const files = (plan.files || []).map((f) => ({ ...f, to: `${dir}/${basename(f.to)}` }))
    return { plan: { ...plan, to: dir, files }, skipped: false, forcedRelocate: true }
  }
  const canonical = basename(dirname(plan.to))
  const to = canonical ? `${root}/${canonical}/${basename(plan.to)}` : `${root}/${basename(plan.to)}`
  return { plan: { ...plan, to }, skipped: false, forcedRelocate: true }
}
