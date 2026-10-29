const SOURCE = /^src_[0-9a-f]{32}$/
const EXTERNAL = /^[A-Za-z0-9_-]{1,128}$/
const FINGERPRINT = /^[0-9a-f]{64}$/

export function validOnlineHostReport(value, sourceId, externalId) {
  const hosts = value?.missing_hosts
  return SOURCE.test(sourceId || '') && EXTERNAL.test(externalId || '')
    && value?.source_id === sourceId && value.external_id === externalId
    && FINGERPRINT.test(value.configuration_fingerprint || '')
    && Array.isArray(hosts) && hosts.length <= 128 && new Set(hosts).size === hosts.length
    && hosts.every(host => typeof host === 'string' && host.length <= 253
      && /^[a-z0-9][a-z0-9.:-]*$/i.test(host) && !host.includes('..'))
}

export function onlineHostError(error, saved = false) {
  if (saved) return '媒体主机已添加，但详情刷新失败。请重新加载详情，不必重复添加。'
  return ({
    online_source_changed: '视频源配置已变化，请重新检查后再确认。',
    provider_timeout: '检查媒体主机超时，请稍后重新检查。',
    network_timeout: '检查媒体主机超时，请稍后重新检查。',
    provider_catalog_busy: '视频源正在处理其他请求，请稍后重新检查。',
    management_required: '请先验证管理权限，再检查媒体主机。',
    online_management_revoked: '管理权限已过期，请重新验证后检查。',
    online_source_disabled: '此视频源已停用，请返回选择其他来源。',
    online_disabled: '在线点播已停用。',
    provider_entry_limit: '此内容的分集数量超过读取上限，暂时无法检查。',
    provider_response_limit: '视频源返回的资料过大，暂时无法检查。',
  })[error?.code] || (error?.status === 401
    ? '无法执行此操作，请检查服务的写操作令牌或观看权限。'
    : '暂时无法检查或添加媒体主机，请稍后重新检查。')
}

// One page lifetime, bounded by the same small set of recently viewed details.
export function createOnlineHostReminders() {
  const seen = new Set()
  return {
    claim(key) {
      if (!key || seen.has(key)) return false
      while (seen.size >= 16) seen.delete(seen.values().next().value)
      seen.add(key)
      return true
    },
    remember(key) {
      if (key) { seen.delete(key); this.claim(key) }
    },
    clear() { seen.clear() },
  }
}
