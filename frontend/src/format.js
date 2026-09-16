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
