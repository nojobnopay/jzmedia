// Preserve links from movie/TV details while displaying one settings page at a time.
export const LIBRARY_SECTIONS = new Set([
  'sec-libtools', 'sec-pipeline', 'sec-sync', 'sec-pending', 'sec-organize',
  'sec-tvorganize', 'sec-meta', 'sec-restore', 'sec-files',
])

export const SETTINGS_PAGES = [
  { id: 'sec-status', label: '概览', group: '媒体管理', description: '查看媒体库状态与待办。' },
  { id: 'sec-libraries', label: '媒体库连接', group: '媒体管理', description: '连接存储位置，管理其中的电影库与剧集库。' },
  { id: 'sec-libtools', label: '扫描与整理', group: '媒体管理', description: '选择视频库，扫描新文件、核对匹配或整理目录。' },
  { id: 'sec-tmdb', label: '资料来源', group: '偏好与服务', description: '配置影片资料、海报的来源与离线匹配数据。' },
  { id: 'sec-display', label: '界面显示', group: '偏好与服务', description: '调整当前浏览器的显示效果，修改后自动保存。' },
  { id: 'sec-auth', label: '访问保护', group: '偏好与服务', description: '用令牌保护扫描、编辑、整理和删除等操作。' },
  { id: 'sec-index', label: '系统维护', group: '偏好与服务', description: '修复搜索、释放播放缓存，作用于全部媒体库。' },
]

const first = value => Array.isArray(value) ? value[0] : value
export function positiveId(value) {
  const n = Number(first(value))
  return Number.isInteger(n) && n > 0 ? n : null
}

export function settingsTarget(query = {}) {
  const sec = String(first(query.sec) || '')
  const library = positiveId(query.library)
  const media = positiveId(query.media)
  const ids = String(query.ids || '').split(',').map(positiveId).filter(Boolean)
  const page = LIBRARY_SECTIONS.has(sec) ? 'sec-libtools'
    : SETTINGS_PAGES.some(p => p.id === sec) ? sec
      : library || media || ids.length ? 'sec-libtools' : 'sec-status'
  return { page, sec, library, media, ids }
}

export function toolViewForSection(sec, ids = []) {
  if (sec === 'sec-restore' || ids.length) return 'restore'
  if (sec === 'sec-files') return 'files'
  if (sec === 'sec-meta') return 'maintenance'
  return 'workflow'
}
