// 显示偏好：localStorage 持久化，main.js 启动即应用，设置页滑杆实时改。
const KEY = 'jz.prefs'

export const PREF_DEFAULTS = { fontSize: 16, posterMin: 150, tvUpdates: 'weekly' }

export const DISPLAY_FIELDS = {
  fontSize: { label: '字体大小', min: 13, max: 20, step: 1 },
  posterMin: { label: '海报大小', min: 120, max: 200, step: 10 },
  tvUpdates: { label: '剧集更新推荐', options: { weekly: '每周提醒一次', off: '关闭推荐' } },
}

export function loadPrefs() {
  try {
    const prefs = { ...PREF_DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') }
    prefs.tvUpdates = prefs.tvUpdates === 'off' ? 'off' : 'weekly'
    return prefs
  } catch (e) {
    return { ...PREF_DEFAULTS }
  }
}

export function applyPrefs(p) {
  const root = document.documentElement
  root.style.fontSize = (Number(p.fontSize) || 16) + 'px'
  root.style.setProperty('--poster-min', (Number(p.posterMin) || 150) + 'px')
}

export function savePrefs(p) {
  localStorage.setItem(KEY, JSON.stringify(p))
  applyPrefs(p)
}
