// 评分显示共享：一位小数去尾零；0-10分 → 5星（四舍五入，无半星，数字补精度）
export function hasScore(v) {
  return typeof v === 'number' && Number.isFinite(v) && v > 0
}

export function fmtScore(v) {
  if (!hasScore(v)) return ''
  return String(Math.round(v * 10) / 10).replace(/\.0$/, '')
}

export function fullStars(v) {
  if (!hasScore(v)) return 0
  return Math.max(0, Math.min(5, Math.round(v / 2)))
}

export function starRow(v) {
  const f = fullStars(v)
  return Array.from({ length: 5 }, (_, index) => index < f ? 'star-filled' : 'star')
}
