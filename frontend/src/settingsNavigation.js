// Preserve links from movie/TV details while displaying one settings page at a time.
export const LIBRARY_SECTIONS = new Set([
  'sec-libtools', 'sec-pipeline', 'sec-sync', 'sec-pending', 'sec-organize',
  'sec-tvorganize', 'sec-meta', 'sec-restore',
])

export const SETTINGS_PAGES = [
  { id: 'sec-status', icon: 'home', label: '概览', group: '媒体与文件', description: '查看媒体库状态与待办。' },
  { id: 'sec-libraries', icon: 'library', label: '媒体库连接', group: '媒体与文件', description: '连接存储位置，管理其中的电影库与剧集库。' },
  { id: 'sec-libtools', icon: 'organize', label: '扫描与整理', group: '媒体与文件', description: '选择视频库，扫描新文件、核对匹配或整理目录。' },
  { id: 'sec-files', icon: 'folder', label: '文件管理', group: '媒体与文件', description: '浏览文件，预览内容或管理勾选项。' },
  { id: 'sec-online', icon: 'play', label: '在线视频源', group: '媒体与文件', description: '管理在线点播开关、视频源及其网络连接。' },
  { id: 'sec-tmdb', icon: 'link', label: '在线资料服务', group: '资料与智能', description: '配置 TMDB 连接，查看资料服务运行状态。配置对全部视频库生效。' },
  { id: 'sec-matching', icon: 'match', label: '匹配规则', group: '资料与智能', description: '为每个视频库选择资料来源、调整优先级并测试已保存规则。' },
  { id: 'sec-ai', icon: 'ai', label: '智能辅助', group: '资料与智能', description: '配置智能搜索、匹配建议和设置助手共用的模型服务。' },
  { id: 'sec-offline', icon: 'download', label: '离线资料', group: '资料与智能', description: '导入本地 IMDb 数据，为全部视频库提供离线标题与年份索引。' },
  { id: 'sec-auth', icon: 'lock', label: '访问保护', group: '安全与显示', description: '设置儿童与隐私保护密码，管理访问令牌。' },
  { id: 'sec-display', icon: 'image', label: '界面显示', group: '安全与显示', description: '修改后自动保存到当前浏览器。' },
  { id: 'sec-index', icon: 'settings', label: '系统维护', group: '存储与维护', description: '查看应用占用，调整容量或清理缓存。' },
  { id: 'sec-maintenance', icon: 'refresh', label: '资料维护', group: '存储与维护', description: '补全儿童筛选与剧集播出资料，修复搜索索引。' },
]

// Secondary tasks retain their own URL and draft boundary, without crowding the directory.
export const SETTINGS_TASK_PAGES = [
  { id: 'sec-token', parent: 'sec-auth', icon: 'lock', label: '访问令牌', description: '保护修改内容、网页播放和进度保存。' },
]

export function settingsBrowseTarget(value, media) {
  if (typeof value === 'string' && /^\/(?:\?|$|m\/\d+(?:\?|$)|p\/\d+(?:\?|$)|tv(?:\/\d+(?:\/s\/\d+(?:\/e\/\d+)?)?)?(?:\?|$)|collections(?:\?|$)|c\/\d+(?:\?|$)|online(?:\?|$))/.test(value)) return value
  const id = positiveId(media)
  return id ? '/?media=' + id : '/'
}

const first = value => Array.isArray(value) ? value[0] : value
export function positiveId(value) {
  const n = Number(first(value))
  return Number.isInteger(n) && n > 0 ? n : null
}
// 文件管理深链目录：仅接受库内相对路径，拒绝空段之外的 '.' 与任何 '..'，超长视为无效。
export function sanitizeFilesPath(value) {
  if (typeof value !== 'string') return ''
  if (value.length > 1024) return ''
  const parts = value.split('/')
  const clean = []
  for (const part of parts) {
    if (!part || part === '.') continue
    if (part === '..') return ''
    clean.push(part)
  }
  return clean.join('/')
}

export function settingsTarget(query = {}) {
  const sec = String(first(query.sec) || '')
  const library = positiveId(query.library)
  const media = positiveId(query.media)
  const ids = String(query.ids || '').split(',').map(positiveId).filter(Boolean)
  const files_path = sanitizeFilesPath(first(query.files_path))
  const page = LIBRARY_SECTIONS.has(sec) ? 'sec-libtools'
    : [...SETTINGS_PAGES, ...SETTINGS_TASK_PAGES].some(p => p.id === sec) ? sec
      : library || media || ids.length ? 'sec-libtools' : 'sec-status'
  return { page, sec, library, media, ids, files_path }
}

export function toolViewForSection(sec, ids = []) {
  if (sec === 'sec-restore' || ids.length) return 'restore'
  if (sec === 'sec-files') return 'files'
  if (sec === 'sec-meta') return 'maintenance'
  return 'workflow'
}
