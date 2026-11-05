import { api } from './api.js'
import { onlineIdentityKey, validateOnlineIdentity } from './onlinePlayback.js'

const opaque = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.exec(value)?.[0] === value
const states = new Set(['queued', 'downloading', 'paused', 'failed', 'ready', 'deleting'])
export function onlineCacheState(state) {
  return ({ queued: '等待缓存', downloading: '正在缓存', paused: '已暂停', failed: '缓存失败', ready: '已缓存', deleting: '正在删除' })[state] || '状态未知'
}
export function onlineCacheBytes(value) {
  if (!Number.isFinite(value) || value < 0) return '未知'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let amount = value, unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++ }
  return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`
}
export function onlineCacheError(error) {
  return ({
    management_required: '请完成管理验证后再操作缓存。',
    online_management_revoked: '管理验证已过期，请重新验证后重试。',
    online_cache_disabled: '本地缓存尚未启用，请在服务器配置缓存目录。',
    online_cache_not_found: '缓存已删除，请刷新列表。',
    online_cache_item_not_found: '此剧的缓存已删除，请返回本地缓存列表。',
    online_cache_not_ready: '缓存尚未完成或已被删除，请刷新后重试。',
    online_cache_disk_full: '磁盘空间不足，请删除部分缓存后重试。',
    online_cache_insufficient_space: '磁盘可用空间不足，请删除部分缓存后重试。',
    online_cache_space_low: '已暂停：磁盘剩余空间不足，请清理缓存后继续。',
    online_cache_storage_unavailable: '缓存目录不可用，请检查服务器磁盘与目录权限。',
    online_cache_busy: '缓存任务正在处理，请稍后重试。',
    online_cache_in_use: '此集正在播放，请停止播放后再删除。',
    online_cache_corrupt: '缓存文件缺失或损坏，请删除后重新缓存。',
    online_cache_duration_mismatch: '下载的视频时长不完整，请重试或选择其他线路。',
    online_cache_length_mismatch: '下载的文件不完整，请重试缓存。',
    online_cache_range_mismatch: '来源返回的续传内容不一致，请重试缓存。',
    online_cache_network_failed: '连接视频源失败，请检查来源网络设置后重试。',
    online_cache_upstream_status: '视频源暂时无法提供文件，请稍后重试。',
    online_cache_url_expired: '视频地址已过期，请重试以重新解析地址。',
    online_cache_source_changed: '来源配置已变化，请确认配置后重试。',
    online_cache_probe_failed: '无法确认缓存文件可完整播放，请重试或选择其他线路。',
    online_cache_probe_unavailable: '服务器缺少视频检查工具，请检查 FFprobe 配置。',
    online_cache_action_unavailable: '任务状态已变化，请刷新后再操作。',
    online_cache_delete_failed: '缓存文件未能完全删除，请检查目录权限后重试。',
    online_cache_episode_not_found: '来源的此集已变化，请重新打开在线详情选择。',
    online_cache_line_not_found: '来源的此线路已变化，请重新打开在线详情选择。',
    online_cache_no_episodes: '没有可缓存的集数，请重新选择。',
    provider_not_found: '此内容已从来源移除，无法继续缓存。',
    provider_episode_not_found: '此集已从来源移除，请重新选择。',
    online_source_disabled: '来源已停用，已有完整缓存仍可播放。',
    online_cache_unsupported: '此视频暂不支持完整缓存，请选择其他线路。',
  })[typeof error === 'string' ? error : error?.code] || '缓存操作未完成，请检查网络和磁盘后重试。'
}
export function onlineCachedEntry(entries, identity) {
  let key
  try { key = onlineIdentityKey(identity) } catch { return null }
  return entries?.find(entry => {
    try { return onlineIdentityKey(entry.identity) === key } catch { return false }
  }) || null
}
export function onlineCacheProgress(entry) {
  if (entry.state === 'ready') return 1
  if (Number.isFinite(entry.total_bytes) && entry.total_bytes > 0) return Math.min(1, Math.max(0, entry.downloaded_bytes / entry.total_bytes))
  if (entry.total_resources > 0) return Math.min(1, Math.max(0, entry.completed_resources / entry.total_resources))
  return null
}
function checkedEntry(entry) {
  if (!entry || !opaque(entry.id) || !states.has(entry.state) || typeof entry.label !== 'string') throw new Error('invalid cache response')
  validateOnlineIdentity(entry.identity)
  return entry
}
function checkedDetail(value, expectedItem = '', expectedSource = '') {
  if (!/^itm_[0-9a-f]{32}$/.test(value?.detail?.item?.id) || !/^src_[0-9a-f]{32}$/.test(value.detail.source?.id)
    || expectedItem && value.detail.item.id !== expectedItem || expectedSource && value.detail.source.id !== expectedSource
    || !Array.isArray(value.detail.lines) || !Array.isArray(value.entries)) throw new Error('invalid cache response')
  value.entries.forEach(entry => {
    checkedEntry(entry)
    if (entry.identity.item_id !== value.detail.item.id || entry.identity.source_id !== value.detail.source.id) throw new Error('invalid cache response')
  })
  return value
}

// Reads may be replaced. Mutations keep their own controller until completion;
// leaving a viewing scope invalidates both, including late authorization replies.
export function createOnlineCacheClient({ request = api, authorize = async () => true } = {}) {
  let generation = 0, pending = null, closed = false
  const mutations = new Set()
  async function read(path, parse = value => value) {
    if (closed) return null
    const turn = ++generation
    pending?.abort()
    const controller = pending = new AbortController()
    try {
      const value = await request('/api/online/cache' + path, { signal: controller.signal, timeout: 15000 })
      return !closed && generation === turn && !controller.signal.aborted ? parse(value) : null
    } catch (error) { if (!closed && generation === turn && !controller.signal.aborted) throw error; return null }
    finally { if (pending === controller) pending = null }
  }
  async function mutate(path, body) {
    if (closed) return null
    const controller = new AbortController()
    mutations.add(controller)
    try {
      if (!await authorize() || controller.signal.aborted || closed) return null
      const result = await request('/api/online/cache' + path, { method: 'POST', body: JSON.stringify(body),
        signal: controller.signal, silentViewing: true, timeout: 30000 })
      return !closed && !controller.signal.aborted ? result : null
    } catch (error) { if (!closed && !controller.signal.aborted) throw error; return null }
    finally { mutations.delete(controller) }
  }
  function id(value) { if (!opaque(value)) throw new Error('invalid cache identity'); return encodeURIComponent(value) }
  function cancel() { generation++; pending?.abort(); pending = null; mutations.forEach(controller => controller.abort()) }
  return {
    list: () => read('/items', value => {
      if (!Array.isArray(value?.items) || !value.storage) throw new Error('invalid cache response')
      value.items.forEach(item => {
        if (!opaque(item.item_id) || !Array.isArray(item.entries)) throw new Error('invalid cache response')
        item.entries.forEach(entry => { checkedEntry(entry); if (entry.identity.item_id !== item.item_id || entry.identity.source_id !== item.source_id) throw new Error('invalid cache response') })
      })
      return value
    }),
    detail: item => read('/items/' + id(item), value => checkedDetail(value, item)),
    async lookup(source, external) {
      try { return await read('/lookup?' + new URLSearchParams({ source_id: source, external_id: external }), value => checkedDetail(value, '', source)) }
      catch (error) { if (error.status === 404) return null; throw error }
    },
    enqueue: selection => mutate('/tasks', selection),
    action: (task, action) => { if (!['pause', 'resume', 'retry'].includes(action)) throw new Error('invalid cache action'); return mutate('/tasks/' + id(task) + '/actions', { action }) },
    remove: ids => { if (!Array.isArray(ids) || !ids.length || ids.some(value => !opaque(value))) throw new Error('invalid cache selection'); return mutate('/entries/delete', { ids: [...new Set(ids)] }) },
    cancel, close() { closed = true; cancel() },
  }
}
