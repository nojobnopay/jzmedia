import { api } from './api.js'

export const OPEN_CATALOG_BASE = 'https://www.bigbuckbunny.org/'
const SOURCE_ID = /^src_[0-9a-f]{32}$/

export function onlineRouteTarget(query = {}) {
  try { sourceId(query.source) } catch { return null }
  const item = query.item === undefined ? '' : query.item
  if (typeof item !== 'string' || item && /^[A-Za-z0-9_-]{1,128}$/.exec(item)?.[0] !== item) return null
  return { sourceId: query.source, externalId: item }
}

function sourceId(value) {
  if (typeof value !== 'string' || SOURCE_ID.exec(value)?.[0] !== value) throw new Error('在线来源身份无效')
  return value
}

export function onlineCatalogError(problem) {
  return ({
    online_source_disabled: '在线功能或此来源已停用，请返回选择其他来源。',
    online_children_unavailable: '儿童模式暂不支持在线内容。',
    online_access_revoked: '观看权限已变化，请重新连接服务。',
    viewing_session_required: '请重新连接服务后再浏览在线内容。',
    viewing_session_invalid: '当前设备会话已失效，请重新连接服务。',
    provider_not_ready: '此来源的适配器尚未启用。',
    provider_resolution_failed: '视频源暂时无法响应，请稍后重试。',
    provider_invalid_response: '视频源返回的资料格式不受支持，请选择其他来源。',
    invalid_provider_response: '视频源返回的资料格式不受支持，请选择其他来源。',
    invalid_provider_result: '视频源返回的资料格式不受支持，请选择其他来源。',
    online_catalog_busy: '当前在线查询较多，请稍后重试。',
    provider_catalog_busy: '当前在线查询较多，请稍后重试。',
    provider_timeout: '连接视频源超时，请稍后重试。',
    provider_rate_limited: '视频源暂时限制了请求次数，请稍后重试。',
    provider_auth_failed: '视频源要求上游认证，请检查来源配置或选择其他来源。',
    provider_media_unsupported: '此线路没有可直接播放的 MP4 或 HLS 视频，请选择其他线路。',
    provider_not_found: '此内容已从视频源移除，请重新搜索。',
    provider_line_required: '此内容有多条线路，请在详情中选择线路后播放。',
    provider_line_not_found: '此播放线路已变化，请重新打开详情选择线路。',
    provider_episode_not_found: '此分集已变化或不再提供，请重新打开详情选集。',
    provider_network_error: '无法连接视频源，请检查其网络设置后重试。',
    provider_upstream_error: '视频源暂时无法提供资料，请稍后重试。',
    provider_response_limit: '视频源返回的资料过大，暂时无法读取。',
    provider_entry_limit: '此内容的分集总数超过 2000 集，暂时无法读取。',
    provider_ambiguous_identity: '视频源的线路或集名重复，暂时无法稳定选择，请选择其他来源。',
    provider_cancelled: '视频源查询已取消，请重新查询。',
    online_source_changed: '视频源配置已变化，请重新选择来源并查询。',
    online_year_filter_unsupported: '此来源尚未验证年份筛选，请清除年份后重试。',
    network_timeout: '连接视频源超时，请稍后重试。',
    unsupported_online_media: '此来源没有可直接播放的 MP4 或 HLS 视频。',
  }[problem?.code] || '暂时无法读取此视频源，请检查来源设置与网络后重试。')
}

// Each page owns its outstanding work. Replacing a query or leaving the page
// invalidates every later completion, including providers that ignore abort.
export function createOnlineCatalogClient({ request = api } = {}) {
  let generation = 0
  let pending = null
  let disposed = false
  async function run(path) {
    if (disposed) return null
    const turn = ++generation
    pending?.abort()
    const controller = pending = new AbortController()
    try {
      const result = await request(path, { signal: controller.signal, timeout: 35000 })
      return !disposed && turn === generation && !controller.signal.aborted ? result : null
    } catch (error) {
      if (disposed || turn !== generation || controller.signal.aborted) return null
      throw error
    } finally { if (pending === controller) pending = null }
  }
  function cancel() { generation++; pending?.abort(); pending = null }
  return {
    sources: () => run('/api/online/catalog/sources'),
    browse(source, page = 1, category = '', year = '') {
      if (!Number.isInteger(page) || page < 1 || page > 1000
        || category && (typeof category !== 'string' || /^[A-Za-z0-9_-]{1,128}$/.exec(category)?.[0] !== category)
        || typeof year !== 'string' || year && /^[1-9][0-9]{3}$/.exec(year)?.[0] !== year) throw new Error('在线分类条件无效')
      const params = new URLSearchParams({ source_id: sourceId(source), page: String(page), category_id: category })
      if (year) params.set('year', year)
      return run('/api/online/catalog/browse?' + params)
    },
    search(source, query, page = 1) {
      const q = String(query || '').trim()
      if (q.length > 100 || !Number.isInteger(page) || page < 1 || page > 1000) throw new Error('在线搜索条件无效')
      return run('/api/online/catalog/search?' + new URLSearchParams({ source_id: sourceId(source), q, page: String(page) }))
    },
    detail(source, externalId) {
      if (typeof externalId !== 'string' || externalId.length > 128
        || /^[A-Za-z0-9_-]+$/.exec(externalId)?.[0] !== externalId) throw new Error('在线内容身份无效')
      return run('/api/online/catalog/detail?' + new URLSearchParams({ source_id: sourceId(source), external_id: externalId }))
    },
    cancel,
    close() { disposed = true; cancel() },
  }
}

// Optional updates own a separate cancellation generation and never replace a
// foreground detail or search. The server may quietly defer this low-priority
// read, but cannot turn an unknown response into a successful empty directory.
export function createOnlineRefreshClient({ request = api } = {}) {
  const client = createOnlineCatalogClient({ request: async (path, options) => {
    const [pathname, query] = path.split('?')
    const params = new URLSearchParams(query)
    params.set('priority', 'background')
    const result = await request(pathname + '?' + params, { ...options, timeout: 10000 })
    if (result?.status === 'deferred' && Object.keys(result).length === 1) return result
    if (result?.status === 'ready' && result.source?.id === params.get('source_id')
      && typeof result.configuration_token === 'string' && /^[0-9a-f]{64}$/.test(result.configuration_token)
      && Array.isArray(result.items) && result.items.length <= 100
      && Number.isInteger(result.page) && result.page === Number(params.get('page'))
      && Number.isInteger(result.page_count) && result.page_count >= 0 && result.page_count <= 1000
      && Number.isInteger(result.total) && result.total >= 0) return result
    throw Object.assign(new Error('在线更新响应无效'), { code: 'invalid_provider_response' })
  } })
  return { browse: client.browse, search: client.search, cancel: client.cancel, close: client.close }
}

// Status reads are independent of catalogue work and never initiate a probe.
export function createOnlineHealthClient({ request = api } = {}) {
  let pending = null, generation = 0, closed = false
  function cancel() { generation++; pending?.abort(); pending = null }
  return {
    async read(source) {
      if (closed) return null
      cancel()
      const turn = generation, control = pending = new AbortController()
      try {
        const value = await request('/api/online/catalog/health?' + new URLSearchParams({ source_id: sourceId(source) }), { signal: control.signal, timeout: 10000 })
        return !closed && generation === turn && !control.signal.aborted ? value : null
      } catch (error) { if (!closed && generation === turn && !control.signal.aborted) throw error; return null }
      finally { if (pending === control) pending = null }
    },
    cancel,
    close() { closed = true; cancel() },
  }
}

export function onlineHealthLabel(value) {
  const state = value?.state
  const observed = ({ unknown: '未观察', stale: '观察已过期', failure: '近期失败', disabled: '已停用', local: '本地目录' })[state]
  const success = value?.stage === 'manifest' ? '清单已读' : value?.stage === 'bytes' ? '取流通过' : '查询通过'
  return `${observed || (state === 'success' ? success : '未观察')} · ${value?.mode === 'proxy' ? '代理' : '直连'}`
}

export function networkRuleChoice(rule) {
  if (rule?.mode === 'proxy') return 'profile:' + rule.profile_id
  if (rule?.mode === 'direct' && rule.profile_id) return 'profile:' + rule.profile_id
  return rule?.mode === 'direct' ? 'direct' : 'inherit'
}

export function choiceNetworkRule(choice, profiles = []) {
  if (choice === 'inherit' || choice === 'direct') return { mode: choice, profile_id: null }
  const profile = profiles.find(value => 'profile:' + value.id === choice)
  if (!profile || !['direct', 'proxy'].includes(profile.mode)) throw new Error('请选择有效的网络档案')
  return { mode: profile.mode, profile_id: profile.id }
}
