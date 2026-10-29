import { api } from './api.js'

const SOURCE_ID = /^src_[0-9a-f]{32}$/
const EXTERNAL_ID = /^[A-Za-z0-9_-]{1,128}$/
const GROUP_REASONS = { series: 'inferred_season', related: 'same_category', name_related: 'name_related' }
const validSeason = value => value === null || Number.isSafeInteger(value) && value > 0

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
      if (result?.source?.id !== source || result?.external_id !== externalId
        || !['ready', 'deferred', 'unsupported'].includes(result?.status)
        || result.status === 'ready' && (!validSeason(result.current_season)
          || Object.entries(GROUP_REASONS).some(([key, reason]) => !Array.isArray(result[key]) || result[key].some(item =>
            typeof item?.external_id !== 'string' || EXTERNAL_ID.exec(item.external_id)?.[0] !== item.external_id
            || typeof item.title !== 'string' || !item.title || item.reason !== reason || !validSeason(item.season))))) {
        throw Object.assign(new Error('相关内容响应无效'), { code: 'provider_invalid_response' })
      }
      return result
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
