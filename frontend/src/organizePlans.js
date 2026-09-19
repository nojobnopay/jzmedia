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
