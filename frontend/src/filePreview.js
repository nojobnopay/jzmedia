const VIDEO_EXTS = new Set(['mp4', 'm4v', 'mkv', 'webm', 'mov', 'avi', 'ts', 'm2ts', 'flv', 'wmv', 'rm', 'rmvb', 'mpg', 'mpeg'])
const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif'])
const TEXT_EXTS = new Set(['txt', 'srt', 'ass', 'ssa', 'vtt', 'lrc', 'nfo', 'md'])

export function filePreviewKind(name) {
  const ext = String(name || '').split('.').pop().toLowerCase()
  if (VIDEO_EXTS.has(ext)) return 'video'
  if (IMAGE_EXTS.has(ext)) return 'image'
  if (ext === 'pdf') return 'pdf'
  if (TEXT_EXTS.has(ext)) return 'text'
  return 'unknown'
}

// A parent movie/show ID on an extra is association metadata, never the file to play.
export function filePreviewPlayer(file) {
  if (!file || file.isDir || filePreviewKind(file.name || file.rel) !== 'video') return null
  if (Number(file.episode_id) > 0) return { id: Number(file.episode_id), kind: 'episode' }
  if (Number(file.extra_id) > 0) return { id: Number(file.extra_id), kind: 'extra' }
  if (file.kind === 'feature' && Number(file.movie_id) > 0) return { id: Number(file.movie_id), kind: 'movie' }
  return null
}

export function filePreviewUrl(base, mode = '') {
  const separator = base.includes('?') ? '&' : '?'
  return mode ? base + separator + (mode === 'text' ? 'mode=text' : 'inline=1') : base
}

export function fsBlobUrl(libraryId, path) {
  return '/api/fs/blob?' + new URLSearchParams({ library: libraryId, path })
}

// Run before removing a playing video: removing the DOM alone can leave the source active.
export function clearFilePreviewElement(element) {
  if (!element) return
  if (typeof element.pause === 'function') element.pause()
  element.removeAttribute('src')
  if (typeof element.load === 'function') element.load()
}

export function filePreviewReadError(status) {
  return ({ 403: '没有读取此文件的权限。', 404: '文件已不存在，请刷新目录核对。', 422: '文件路径无效，请刷新目录后重试。', 416: '文件为空或读取范围无效，请下载检查。', 503: '媒体库暂时离线，请检查连接后重试。' }[status] || `无法读取文件（HTTP ${status}），请稍后重试。`)
}
