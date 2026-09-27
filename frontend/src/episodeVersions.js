// 剧集多版本（V1/V2）展示辅助（纯函数，node --test 直测）：
// 版本号与后端 `store.episode_version` 同源规则——文件名 `剧名-V2-S01E01-…`（前缀）
// 或 `…-V2.ext`（旧后缀）；无标记 = 1。优先用后端返回的 `version` 字段。

export function basename(p) {
  const s = String(p || '')
  const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'))
  return i >= 0 ? s.slice(i + 1) : s
}

export function episodeVersion(ep) {
  const n = Number(ep && ep.version)
  if (Number.isFinite(n) && n >= 1) return Math.floor(n)
  const stem = basename(ep && ep.file_path).replace(/\.[^.]+$/, '')
  const m = /-V(\d+)(?:-|$)/i.exec(stem)
  const v = m ? parseInt(m[1], 10) : 1
  return Number.isFinite(v) && v >= 1 ? v : 1
}

// 按版本分组（版本升序；组内保持传入顺序 = 季/集顺序）
export function groupEpisodesByVersion(eps) {
  const map = new Map()
  for (const e of eps || []) {
    const v = episodeVersion(e)
    if (!map.has(v)) map.set(v, [])
    map.get(v).push(e)
  }
  return [...map.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([version, episodes]) => ({ version, episodes }))
}

// 季 chip 展示：去重集数（同一集多版本只算一集）+ 版本数
export function seasonStats(eps) {
  const keys = new Set()
  const vers = new Set()
  for (const e of eps || []) {
    const first = Number(e.episode) || 0
    for (let n = first; n <= Math.max(first, Number(e.episode_end) || 0); n++) keys.add(`${Number(e.season) || 0}:${n}`)
    vers.add(episodeVersion(e))
  }
  return { distinct: keys.size, versions: vers.size }
}
