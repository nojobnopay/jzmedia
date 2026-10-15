import { computed, reactive, watch } from 'vue'
import { api, setArtworkScope } from './api.js'
import { clearBrowse } from './browseHistory.js'

export const viewing = reactive({
  ready: false, loading: false, error: '', mode: 'normal', enabled: false,
  management_unlocked: false, generation: 0, expires_at: 0, revision: 0,
  prompt: null, promptBusy: false, promptError: '', notice: '',
  suspended: false, locallyLocked: false,
})
export const isChildren = computed(() => viewing.mode === 'children')
export const isPrivate = computed(() => viewing.mode === 'private')
export const modeLabel = computed(() => ({ normal: '普通模式', children: '儿童模式', private: '隐私模式' })[viewing.mode])
const CHANGE_KEY = 'jzmedia.viewing.changed'
let promptResolve = null
let loading = null
let stopListening = null
let changeHandler = null
let sessionEpoch = 0
const playbackCleanups = new Set()
export function registerViewingPlaybackCleanup(cleanup) {
  playbackCleanups.add(cleanup)
  return () => playbackCleanups.delete(cleanup)
}
async function finishPlayback() { await Promise.allSettled([...playbackCleanups].map(cleanup => cleanup())) }

export function setViewingChangeHandler(handler) { changeHandler = handler }
export function applyViewingSession(data = {}, { preserveContent = false, authorized = false } = {}) {
  if (!['normal', 'children', 'private'].includes(data.mode) || typeof data.enabled !== 'boolean') throw new Error('观看模式响应无效，请重试')
  if (viewing.locallyLocked && data.mode === 'private' && !authorized) return data
  if (authorized) viewing.locallyLocked = false
  const mode = data.mode
  const generation = Number(data.generation) || 0
  const modeChanged = mode !== viewing.mode
  const changed = viewing.ready && !preserveContent && (modeChanged || generation !== viewing.generation)
  setArtworkScope(data.enabled ? `${mode}-${generation}` : '')
  viewing.expires_at = Number(data.expires_at) || 0
  Object.assign(viewing, { mode, enabled: !!data.enabled, management_unlocked: !!data.management_unlocked,
    generation, expires_at: Number(data.expires_at) || 0, ready: true, error: '' })
  if (changed) {
    clearBrowse()
    viewing.revision++
    changeHandler?.({ modeChanged })
  }
  return data
}
export async function refreshViewing() {
  if (loading) return loading
  viewing.loading = true
  const epoch = sessionEpoch
  const locked = viewing.locallyLocked
  loading = api(locked ? '/api/viewing/lock' : '/api/viewing/session',
    { silentViewing: true, ...(locked ? { method: 'POST', body: '{}' } : {}) }).then(data => {
    if (epoch !== sessionEpoch) return data
    if (locked && data.mode !== 'private') viewing.locallyLocked = false
    const result = applyViewingSession(data)
    if (locked) broadcast()
    return result
  })
    .catch(error => { viewing.error = error.message; throw error })
    .finally(() => { viewing.loading = false; loading = null })
  return loading
}
function broadcast(type = 'mode') {
  try { localStorage.setItem(CHANGE_KEY, `${type}:${Date.now()}:${Math.random()}`) } catch { /* 浏览器可能禁用存储 */ }
}
async function post(path, body, options) {
  const epoch = ++sessionEpoch
  const data = await api(`/api/viewing/${path}`, { method: 'POST', body: JSON.stringify(body), silentViewing: true })
  if (epoch === sessionEpoch) {
    applyViewingSession(data, options)
    if (data.mode !== 'private') viewing.locallyLocked = false
  }
  broadcast()
  return data
}
export async function changeViewingMode(mode, password) {
  if (viewing.mode === 'private' && mode === 'normal') return lockViewing()
  const finishing = finishPlayback()
  if (viewing.mode === 'private' && mode !== 'private') lockViewingLocally()
  await finishing
  return post('mode', { mode, ...(password ? { password } : {}) }, { authorized: !!password })
}
export async function lockViewing() {
  const finishing = finishPlayback()
  lockViewingLocally()
  await finishing
  return post('lock', {})
}
export async function saveProtectionPassword(currentPassword, newPassword) {
  return post('password', { ...(currentPassword ? { current_password: currentPassword } : {}), new_password: newPassword }, { preserveContent: !viewing.enabled })
}
export function requestProtection(purpose = 'unlock', mode = null) {
  if (viewing.prompt) return Promise.resolve(false)
  viewing.promptError = ''
  viewing.prompt = { purpose: viewing.enabled ? purpose : 'setup', mode }
  return new Promise(resolve => { promptResolve = resolve })
}
export function cancelProtection() {
  if (viewing.promptBusy) return
  viewing.prompt = null
  viewing.promptError = ''
  promptResolve?.(false)
  promptResolve = null
}
export async function submitProtection(password, confirmation = '') {
  const prompt = viewing.prompt
  if (!prompt || viewing.promptBusy) return
  if (prompt.purpose === 'setup' && password !== confirmation) {
    viewing.promptError = '两次输入的密码不一致'
    return
  }
  viewing.promptBusy = true
  viewing.promptError = ''
  try {
    if (prompt.purpose === 'setup') await saveProtectionPassword('', password)
    if (prompt.mode) await changeViewingMode(prompt.mode, password)
    else await post('unlock', { password }, { authorized: true })
    viewing.prompt = null
    promptResolve?.(true)
    promptResolve = null
  } catch (error) { viewing.promptError = error.message }
  finally { viewing.promptBusy = false }
}
export async function ensureManagement() {
  if (isChildren.value) return false
  if (viewing.enabled && viewing.management_unlocked) return true
  return requestProtection('unlock')
}
export function isManagementRoute(route) {
  return route.path === '/settings' || route.path === '/setup'
}

export function lockViewingLocally({ notifyTabs = true } = {}) {
  sessionEpoch++
  viewing.locallyLocked = true
  viewing.suspended = false
  applyViewingSession({ ...viewing, mode: isChildren.value ? 'children' : 'normal', management_unlocked: false })
  viewing.notice = '保护会话已锁定，请重新验证密码。'
  if (notifyTabs) broadcast('lock')
}

export function idleExpired(now, lastInput, lastPlayback = 0) {
  return now - Math.max(lastInput, lastPlayback) >= 15 * 60 * 1000
}

// Only input from the user extends idle time; polling never counts as activity.
export function startViewingLifecycle() {
  if (stopListening) return stopListening
  let userActivity = false
  let pending = false
  let lastInput = Date.now(), lastPlayback = 0, managementInput = Date.now()
  const playingNow = () => [...document.querySelectorAll('video')].some(video => !video.paused && !video.ended && video.readyState >= 2)
  const stopClockWatch = watch(() => [viewing.ready, viewing.mode, viewing.management_unlocked], ([ready, mode, management], previous = []) => {
    if (ready && (mode !== previous[1] || !previous[0])) {
      lastInput = mode === 'private' && viewing.expires_at ? Math.min(Date.now(), viewing.expires_at * 1000 - 15 * 60 * 1000) : Date.now()
      lastPlayback = 0
    }
    if (management && !previous[2]) managementInput = Date.now()
  }, { flush: 'sync' })
  const onActivity = () => { userActivity = true; lastInput = Date.now(); managementInput = Date.now() }
  const sync = () => refreshViewing().catch(() => {})
  const revalidate = async () => {
    if (viewing.suspended) return
    if (viewing.mode !== 'private') { sync(); return }
    viewing.suspended = true
    try { await refreshViewing() }
    catch { lockViewingLocally() }
    finally { viewing.suspended = false }
  }
  const onStorage = event => {
    if (event.key !== CHANGE_KEY) return
    if (event.newValue?.startsWith('lock:')) {
      lockViewingLocally({ notifyTabs: false })
      sync()
    } else revalidate()
  }
  const onVisible = () => { if (document.visibilityState === 'visible') revalidate() }
  const onGeneration = event => { if (Number(event.detail?.generation) > viewing.generation) sync() }
  const checkLocalIdle = () => {
    const now = Date.now()
    if (playingNow()) lastPlayback = now
    if (viewing.mode === 'private' && idleExpired(now, lastInput, lastPlayback)) {
      lockViewingLocally()
      sync()
    } else if (viewing.management_unlocked && idleExpired(now, managementInput)) viewing.management_unlocked = false
  }
  const heartbeat = async () => {
    if (pending || !viewing.ready || !viewing.enabled) return
    if (viewing.locallyLocked) { await refreshViewing().catch(() => {}); return }
    pending = true
    const activity = userActivity
    const epoch = sessionEpoch
    userActivity = false
    const playing = playingNow()
    try {
      const data = await api('/api/viewing/activity', { method: 'POST',
        body: JSON.stringify({ user_activity: activity, playing }), silentViewing: true })
      if (epoch === sessionEpoch) {
        applyViewingSession(data)
        if (data.mode === 'private' && data.expires_at) lastInput = Math.max(lastInput, data.expires_at * 1000 - 15 * 60 * 1000)
      }
    } catch { /* 下次心跳或实际请求会重新检查会话 */ }
    finally { pending = false }
  }
  const onError = event => {
    const code = event.detail?.code
    if (code === 'viewing_locked') {
      applyViewingSession({ ...viewing, mode: isChildren.value ? 'children' : 'normal', management_unlocked: false })
      viewing.notice = '保护会话已锁定，请重新验证密码。'
      sync()
    } else if (['management_required', 'protection_password_required', 'protection_setup_required'].includes(code) && !isChildren.value) {
      requestProtection('unlock').then(ok => { if (ok) viewing.notice = '已验证保护密码，请重试刚才的操作。' })
    }
  }
  for (const event of ['pointerdown', 'keydown', 'touchstart']) window.addEventListener(event, onActivity, { passive: true })
  window.addEventListener('storage', onStorage)
  window.addEventListener('online', sync)
  window.addEventListener('focus', revalidate)
  window.addEventListener('jzmedia:viewing-error', onError)
  window.addEventListener('jzmedia:viewing-generation', onGeneration)
  document.addEventListener('visibilitychange', onVisible)
  const timer = window.setInterval(heartbeat, 30000)
  const idleTimer = window.setInterval(checkLocalIdle, 1000)
  stopListening = () => {
    for (const event of ['pointerdown', 'keydown', 'touchstart']) window.removeEventListener(event, onActivity)
    window.removeEventListener('storage', onStorage)
    window.removeEventListener('online', sync)
    window.removeEventListener('focus', revalidate)
    window.removeEventListener('jzmedia:viewing-error', onError)
    window.removeEventListener('jzmedia:viewing-generation', onGeneration)
    document.removeEventListener('visibilitychange', onVisible)
    window.clearInterval(timer)
    window.clearInterval(idleTimer)
    stopClockWatch()
    stopListening = null
  }
  return stopListening
}
