import { onlineIdentityKey, validateOnlineIdentity } from './onlinePlayback.js'

export const ONLINE_EPISODE_GROUP_SIZE = 50

// Ranges describe positions in the provider's list. Labels such as specials,
// dates and split episodes remain untouched, and unsupported entries count too.
export function onlineEpisodeGroups(entries, order = 'asc') {
  if (!Array.isArray(entries)) return []
  const groups = []
  for (let offset = 0; offset < entries.length; offset += ONLINE_EPISODE_GROUP_SIZE) {
    const end = Math.min(offset + ONLINE_EPISODE_GROUP_SIZE, entries.length)
    const rows = entries.slice(offset, end).map((entry, index) => ({ entry, index: offset + index }))
    groups.push({ index: groups.length, start: offset + 1, end,
      label: `${offset + 1}–${end}`, entries: order === 'desc' ? rows.reverse() : rows })
  }
  return order === 'desc' ? groups.reverse() : groups
}

function scopedIdentity(value, itemKey, lineId) {
  try {
    const identity = validateOnlineIdentity(value)
    if (itemKey && identity.item_id !== itemKey || lineId && identity.line_id !== lineId) return null
    return identity
  } catch { return null }
}

export function onlineEntryPlayable(entry, itemKey = '', lineId = '') {
  return entry?.supported === true && scopedIdentity(entry.identity, itemKey, lineId) !== null
}

export function onlineEntryIndex(entries, identity, itemKey = '', lineId = '') {
  const selected = scopedIdentity(identity, itemKey, lineId)
  if (!selected || !Array.isArray(entries)) return -1
  const key = onlineIdentityKey(selected)
  return entries.findIndex(entry => onlineEntryPlayable(entry, itemKey, lineId)
    && onlineIdentityKey(entry.identity) === key)
}

export function onlineEntryReason(reason) {
  return ['host_not_allowed', 'target_host_not_allowed'].includes(reason)
    ? '媒体主机尚未加入此来源允许列表' : '当前线路不支持直接播放，请选择其他线路'
}
