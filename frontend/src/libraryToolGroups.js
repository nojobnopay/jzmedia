// 媒体库工具：按视频库分组/筛选/作用域纯函数（node --test 直测）。
// 约定：列表项上的归属字段为 library_id（后端读接口统一下发）；视频库字段
// 来自 GET /api/libraries 的增强视图（id/name/kind/subpath/enabled）。
export function videoLibIds(videoLibs, { kind = null, enabledOnly = false } = {}) {
  return (videoLibs || [])
    .filter((l) => (kind ? (l.kind || 'movie') === kind : true))
    .filter((l) => (enabledOnly
      ? (l.enabled !== false && l.effective_enabled !== false)
      : true))
    .map((l) => Number(l.id))
    .filter((n) => Number.isFinite(n))
}

export function libIdsParam(videoLibs, options) {
  return videoLibIds(videoLibs, options).join(',')
}

export function libById(videoLibs, id) {
  return (videoLibs || []).find((l) => Number(l.id) === Number(id)) || null
}

export function kindText(kind) {
  return kind === 'tv' ? '剧集' : '电影'
}

export function libLabel(lib) {
  if (!lib) return '未识别库'
  return `${lib.name || ('库 ' + lib.id)} · ${kindText(lib.kind)}`
}

// 按视频库分组（保持 videoLibs 顺序；未识别的 library_id 归入末尾 lib=null 组）
export function groupByVideoLib(items, videoLibs, { includeEmpty = false } = {}) {
  const list = Array.isArray(items) ? items : []
  const groups = []
  const known = new Set()
  for (const lib of videoLibs || []) {
    const lid = Number(lib.id)
    known.add(lid)
    const rows = list.filter((it) => Number(it && it.library_id) === lid)
    if (!rows.length && !includeEmpty) continue
    groups.push({ library_id: lid, lib, items: rows })
  }
  const rest = list.filter((it) => {
    const lid = Number(it && it.library_id)
    return !Number.isFinite(lid) || !known.has(lid)
  })
  if (rest.length) groups.push({ library_id: null, lib: null, items: rest })
  return groups
}

export function filterByLib(items, libFilter) {
  const list = Array.isArray(items) ? items : []
  if (libFilter == null || libFilter === '') return list
  const lid = Number(libFilter)
  return list.filter((it) => Number(it && it.library_id) === lid)
}

export function countByLib(items) {
  const out = {}
  for (const it of items || []) {
    const lid = Number(it && it.library_id)
    if (Number.isFinite(lid)) out[lid] = (out[lid] || 0) + 1
  }
  return out
}

// 媒体库待处理徽章：只统计属于该媒体库视频库的条目
export function mediaPendingCount(unmatchedData, libIds) {
  const set = new Set((libIds || []).map(Number))
  let n = 0
  const keys = ['unmatched', 'needs_review', 'suspect_title_high', 'orphan_extras']
  for (const key of keys) {
    for (const it of (unmatchedData && unmatchedData[key]) || []) {
      if (set.has(Number(it.library_id))) n += 1
    }
  }
  return n
}

// 文件浏览媒体根：目录 rel 是否恰为某视频库 subpath（是则进入该视频库上下文）
export function libBySubpath(videoLibs, rel) {
  const norm = String(rel || '').replace(/^\/+|\/+$/g, '')
  if (!norm) return null
  return (videoLibs || []).find((l) => (
    String(l.subpath || '').replace(/^\/+|\/+$/g, '') === norm)) || null
}

// 目录 rel 是否位于某视频库 subpath 的更浅层级（用于“含视频库”提示）
export function subpathContains(videoLibs, rel) {
  const norm = String(rel || '').replace(/^\/+|\/+$/g, '')
  if (!norm) return false
  return (videoLibs || []).some((l) => {
    const sub = String(l.subpath || '').replace(/^\/+|\/+$/g, '')
    return sub.startsWith(norm + '/')
  })
}
