import { api } from './api.js'

const SOURCE_ID = /^src_[0-9a-f]{32}$/
const EXTERNAL_ID = /^[A-Za-z0-9_-]{1,128}$/
const GROUP_REASONS = { series: 'inferred_season', related: 'same_category', name_related: 'name_related' }
const validSeason = value => value === null || Number.isSafeInteger(value) && value > 0
const RELATED_ERRORS = {
  provider_timeout: '相关目录读取超时，请稍后重试。',
  network_timeout: '相关目录读取超时，请稍后重试。',
  provider_rate_limited: '视频源暂时限制查询次数，请稍后重试。',
  provider_response_limit: '相关目录返回的资料过大，暂时无法读取。',
  provider_entry_limit: '相关内容的分集总数超过 2000 集，暂时无法读取。',
  provider_network_error: '暂时无法连接视频源，请稍后重试。',
  provider_upstream_error: '视频源暂时无法提供相关资料，请稍后重试。',
  provider_resolution_failed: '视频源暂时无法提供相关资料，请稍后重试。',
  provider_invalid_response: '相关目录返回的资料暂不受支持，请稍后重试。',
  invalid_provider_response: '相关目录返回的资料暂不受支持，请稍后重试。',
  invalid_provider_result: '相关目录返回的资料暂不受支持，请稍后重试。',
  provider_catalog_busy: '当前相关查询较多，请稍后重试。',
  online_catalog_busy: '当前相关查询较多，请稍后重试。',
  provider_auth_failed: '视频源需要上游认证，请检查来源设置。',
  online_source_disabled: '此视频源或在线功能已停用。',
  online_disabled: '此视频源或在线功能已停用。',
  online_source_changed: '视频源配置已变化，请重新打开详情。',
  online_access_revoked: '观看会话已变化，请重新打开在线页面。',
  viewing_session_required: '观看会话已变化，请重新打开在线页面。',
  viewing_session_invalid: '观看会话已变化，请重新打开在线页面。',
}

// Only fixed local text and known codes enter the view; upstream bodies, URLs
// and credentials must not become recommendation error details.
export function onlineRelatedError(problem) {
  const known = typeof problem?.code === 'string' && Object.hasOwn(RELATED_ERRORS, problem.code)
  return { code: known ? problem.code : 'unknown', message: known ? RELATED_ERRORS[problem.code] : '相关内容暂时无法读取。' }
}

// Background recommendations own their timer and request. They never cancel a
// detail, playback-state read, or browse operation owned by another client.
export function createOnlineRelatedClient({ request = api, delay = 300, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let generation = 0, pending = null, closed = false
  function cancel() {
    generation++
    if (!pending) return
    pending.controller.abort()
    if (pending.timer !== null) clearTimer(pending.timer)
    pending.release?.(false)
    pending = null
  }
  async function run(source, externalId, immediate) {
    if (closed) return null
    cancel()
    const turn = generation
    const work = pending = { controller: new AbortController(), timer: null, release: null }
    const current = () => !closed && generation === turn && !work.controller.signal.aborted
    try {
      if (!immediate && delay > 0) {
        const ready = await new Promise(resolve => {
          work.release = resolve
          work.timer = setTimer(() => { work.timer = null; work.release = null; resolve(true) }, delay)
        })
        if (!ready || !current()) return null
      }
      const result = await request('/api/online/catalog/related?' + new URLSearchParams({ source_id: source, external_id: externalId }),
        { signal: work.controller.signal, timeout: 10000 })
      if (!current()) return null
      const partial = result?.partial ?? false
      if (result?.source?.id !== source || result?.external_id !== externalId
        || !['ready', 'deferred', 'unsupported'].includes(result?.status)
        || result.partial !== undefined && typeof result.partial !== 'boolean'
        || partial && result.status !== 'ready'
        || result.status === 'ready' && (!validSeason(result.current_season)
          || Object.entries(GROUP_REASONS).some(([key, reason]) => !Array.isArray(result[key]) || result[key].some(item =>
            typeof item?.external_id !== 'string' || EXTERNAL_ID.exec(item.external_id)?.[0] !== item.external_id
            || typeof item.title !== 'string' || !item.title || item.reason !== reason || !validSeason(item.season))))
        || partial && Object.keys(GROUP_REASONS).every(key => result[key].length === 0)) {
        throw Object.assign(new Error('相关内容响应无效'), { code: 'provider_invalid_response' })
      }
      return { ...result, partial }
    } catch (error) { if (current()) throw error; return null }
    finally { if (pending === work) pending = null }
  }
  return {
    read(source, externalId, { immediate = false } = {}) {
      if (typeof source !== 'string' || SOURCE_ID.exec(source)?.[0] !== source
        || typeof externalId !== 'string' || EXTERNAL_ID.exec(externalId)?.[0] !== externalId) throw new Error('在线内容身份无效')
      return run(source, externalId, immediate)
    },
    cancel,
    close() { closed = true; cancel() },
  }
}
