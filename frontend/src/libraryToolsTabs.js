// 媒体库工具（视频库 Tab 版）纯函数：Tab 构建 / 默认选中 / 深链解析 / 步骤状态。
// 约定：mediaLibs 来自 libraries.buildMediaLibs（媒体库分组，video_libraries 为扁平视频库）。
// 步骤 key：movie = scan|pending|organize；tv = scan|match|organize。
import { kindText } from './libraryToolGroups.js'

// 每个视频库一个 Tab（保持媒体库与视频库原顺序）；多媒体库时标签带媒体名前缀
export function buildTabs(mediaLibs) {
  const medias = Array.isArray(mediaLibs) ? mediaLibs : []
  const multi = medias.length > 1
  const tabs = []
  for (const m of medias) {
    for (const l of (m.video_libraries || [])) {
      const name = l.name || ('库 ' + l.id)
      tabs.push({
        id: Number(l.id),
        name,
        kind: l.kind || 'movie',
        enabled: l.enabled !== false && l.effective_enabled !== false,
        media_id: m.id != null ? Number(m.id) : null,
        media_name: m.name || '',
        source: m.source || 'local',
        label: multi ? `${m.name || '媒体库'} · ${name}` : name,
        kind_text: kindText(l.kind),
      })
    }
  }
  return tabs
}

// 默认 Tab：URL/记忆的视频库 → 当前媒体库首个视频库 → 首个启用库 → 第一个
export function pickTab(tabs, { storedId = null, currentMediaId = null } = {}) {
  const list = Array.isArray(tabs) ? tabs : []
  if (!list.length) return null
  const sid = storedId == null || storedId === '' ? null : Number(storedId)
  if (sid != null) {
    const hit = list.find((t) => t.id === sid)
    if (hit) return hit
  }
  const mid = currentMediaId == null || currentMediaId === '' ? null : Number(currentMediaId)
  if (mid != null) {
    const hit = list.find((t) => t.media_id === mid)
    if (hit) return hit
  }
  return list.find((t) => t.enabled) || list[0]
}

// 深链区块 → 目标视频库类型（?sec=sec-tvorganize 优先剧集库；电影流程区块优先电影库）
export function kindForSection(sec) {
  if (sec === 'sec-tvorganize') return 'tv'
  if (['sec-pipeline', 'sec-sync', 'sec-pending', 'sec-organize',
    'sec-meta', 'sec-restore'].includes(sec)) return 'movie'
  return null
}

// 深链区块 → 展开的步骤 key（其余区块返回 null，由调用方决定是否展开「更多工具」）
export function stepForSection(sec) {
  return {
    'sec-sync': 'scan',
    'sec-pipeline': 'scan',
    'sec-pending': 'pending',
    'sec-organize': 'organize',
    'sec-tvorganize': 'organize',
  }[sec] || null
}

// 深链区块是否位于「更多工具」折叠区（电影：高级维护/恢复/文件浏览；剧集：文件浏览）
export function sectionNeedsAdvanced(sec) {
  return sec === 'sec-meta' || sec === 'sec-restore' || sec === 'sec-files'
}

// 深链解析目标 Tab：library（视频库 id）优先；media（媒体库 id）按区块类型偏好选库
export function resolveFocusTab(tabs, { media = null, library = null, sec = '' } = {}) {
  const list = Array.isArray(tabs) ? tabs : []
  const lid = library == null || library === '' ? null : Number(library)
  if (lid != null) {
    const hit = list.find((t) => t.id === lid)
    if (hit) return hit
  }
  const mid = media == null || media === '' ? null : Number(media)
  if (mid != null) {
    const inMedia = list.filter((t) => t.media_id === mid)
    if (inMedia.length) {
      const want = kindForSection(sec)
      return (want ? inMedia.find((t) => t.kind === want) : null) || inMedia[0]
    }
  }
  return null
}

// 单步聚焦手风琴：默认展开第一个有未办事项的步骤；都没有则回到第一步
export function pickOpenStep(steps) {
  const list = Array.isArray(steps) ? steps : []
  const hit = list.find((s) => Number(s && s.count) > 0)
  if (hit) return hit.key
  return list.length ? list[0].key : null
}
