// 通用展示格式化（评审 R14-Q5 纯逻辑单源）：从 Library.vue 抽出，可 node 单测。

export function fmtBytes(n) {
  n = Number(n) || 0
  if (n < 1024) return n + 'B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'M'
  return (n / 1024 / 1024 / 1024).toFixed(2) + 'G'
}

// 长路径中间缩写：保留头部 + ... + 尾部文件名，hover 用 title 看全名
export function midEllipsis(s, max = 48) {
  s = String(s || '')
  if (s.length <= max) return s
  const tail = 17, head = Math.max(1, max - tail - 3)
  return s.slice(0, head) + '...' + s.slice(-tail)
}

// 秒级时间戳 → 本地 YYYY-MM-DD（0/非法返回 ''，调用方据此隐藏）
export function fmtDate(epoch) {
  const t = Number(epoch) || 0
  if (t <= 0) return ''
  const d = new Date(t * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// 剩余时长（秒）→ 「剩 1 时 08 分」「剩 42 分钟」，不足 1 分钟也给最小文案
export function fmtRemaining(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0))
  if (s < 60) return '剩 <1 分钟'
  const mins = Math.floor(s / 60)
  if (mins < 60) return `剩 ${mins} 分钟`
  const h = Math.floor(mins / 60)
  return `剩 ${h} 时 ${String(mins % 60).padStart(2, '0')} 分`
}
