// 播放器纯展示逻辑（评审 R14-Q1/R13-Q1）：从 PlayerModal 抽出，可 node 单测。
// 播放/字幕选项的文案与类型判定只依赖数据字段，不碰 DOM/会话。

export function subKind(s) {
  if (!s) return 'none'
  if (s.image) return String(s.codec || '').toLowerCase() === 'pgs' ? 'pgs' : 'burn'
  const c = String(s.codec || '').toLowerCase()
  return (c === 'ass' || c === 'ssa') ? 'ass' : 'vtt'
}

export function audioLabel(a, i) {
  const parts = [`音轨${i + 1}`]
  if (a.codec) parts.push(String(a.codec).toUpperCase())
  if (a.channels) parts.push(a.channels + 'ch')
  if (a.lang) parts.push(a.lang)
  if (a.title) parts.push(a.title)
  return parts.join(' ')
}

export function subLabel(s, i) {
  const parts = [`字幕${i + 1}`]
  if (s.lang) parts.push(s.lang)
  if (s.title) parts.push(s.title)
  if (s.codec && !s.image) parts.push(String(s.codec).toUpperCase())
  return parts.join(' ')
}

// 字幕下拉角标：烧录/PGS/ASS 样式 + 外挂来源
export function subBadge(s) {
  const kind = subKind(s)
  const parts = []
  if (kind === 'burn') parts.push('烧录')
  else if (kind === 'pgs') parts.push('PGS')
  else if (kind === 'ass') parts.push('ASS 样式')
  if (s && s.source === 'sidecar') parts.push('外挂')
  return parts.length ? '（' + parts.join('·') + '）' : ''
}

export function fmtTime(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0))
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}
