// Collection is compared across the media library; playback and watch state keep
// their original local show/episode identities.
export function tvSeasonLabel(value) {
  return Number(value) === 0 ? '特别篇（SP）' : `第 ${Number(value)} 季`
}

export function tvEpisodeLabel(value) {
  return `S${String(Number(value?.season) || 0).padStart(2, '0')}E${String(Number(value?.episode) || 0).padStart(2, '0')}`
}

export function collectionLabel(value) {
  if (value?.collection_state === 'uncollected') return '未收藏'
  if (value?.collection_state === 'collected') return '已收藏'
  return ({ numbering_unresolved: '分集编号待对照', match_review: '分集匹配需确认', coverage_incomplete: '收藏范围暂未确定' })[value?.collection_reason] || '资料待补全'
}

export function collectionBadge(value) {
  if (value?.collection_state === 'uncollected') return '未收藏'
  if (value?.collection_state === 'uncertain' && ['numbering_unresolved', 'match_review'].includes(value.collection_reason)) return collectionLabel(value)
  return ''
}

export function collectionExplanation(value) {
  if (value?.collection_state !== 'uncertain') return ''
  return ({
    catalog_missing: '官方分集资料尚不完整；已有收藏仍保留，进入「全部分集」可补充目录，无需重新匹配。',
    numbering_unresolved: '部分本地分集尚未对应官方编号；请查看本季分集编号与官方目录的对应关系。',
    match_review: '部分分集匹配尚未确认；请查看本季分集的匹配信息。',
    coverage_incomplete: '本剧部分分集的对应范围尚未确定，暂不判断这些季是否缺集。',
  })[value.collection_reason] || ''
}

export function airingLabel(value) {
  if (value?.airing_state === 'upcoming') return '尚未播出'
  if (value?.airing_state === 'aired') return '已播出'
  return '播出时间未知'
}

export function collectionCountText(value) {
  const official = Number(value?.official_count)
  const hasOfficial = value?.official !== false && !!value?.collection_state
    && value.collection_reason !== 'show_unconfirmed' && Number.isInteger(official) && official > 0
  const confirmed = Math.max(0, Number(value?.collected_count) || 0)
  // Season catalogs can be fresher than the show's independently cached total.
  const confirmedText = hasOfficial && confirmed <= official ? `${confirmed}/${official} 集`
    : `${confirmed} 集${hasOfficial ? ' · 总数待更新' : ''}`
  if (!value || value.collection_state === 'uncertain' || !value.collection_state || value.official === false) {
    const local = Math.max(0, Number(value?.local_count) || 0)
    // Confirmed counts use official coordinates. A local season may span or map
    // to different official seasons, so its count cannot form that same ratio.
    if (hasOfficial && confirmed > 0) {
      const localText = local > confirmed ? ` · 本地 ${local} 集` : ''
      return `已确认收藏 ${confirmedText}${localText}`
    }
    const officialText = hasOfficial ? ` · 官方 ${official} 集` : ''
    if (local > 0) return `已收藏 ${local} 集${officialText}`
    if (confirmed > 0) return `已确认收藏 ${confirmed} 集`
    return `收藏信息待补全${officialText}`
  }
  return `已收藏 ${confirmedText}`
}

export function checkedTime(value) {
  const seconds = Number(value)
  if (!(seconds > 0)) return ''
  const date = new Date(seconds * 1000)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

export function airingErrorText(value) {
  const code = typeof value === 'string' ? value : value?.code || value?.message || ''
  if (/^404\s+not found\s*$/i.test(code)) return '服务端尚未提供播出与收藏接口，请更新并重启服务后重试（404）'
  return ({ not_configured: '尚未配置 TMDB 凭据', invalid_credentials: 'TMDB 凭据无效，请在在线资料服务中检查配置',
    rate_limited: '资料服务暂时限制请求，稍后会重试', timeout: '资料服务请求超时',
    unavailable: '资料服务暂时不可用', invalid_response: '资料服务返回的内容暂时无法使用',
    not_found: '资料服务暂未找到对应内容', invalid_request: '暂时无法查询这些分集资料' })[code] || code || '资料暂时无法更新'
}

export function localSeasonCount(value) {
  // `total` in the legacy show response can be an official metadata count when
  // no local files exist. Never use it to claim ownership.
  return Math.max(0, Number(value?.distinct) || 0)
}

export function mergeTvSeasons(local = [], collection = []) {
  const rows = new Map()
  for (const season of local || []) {
    const number = Number(season.season)
    if (!Number.isInteger(number) || number < 0) continue
    rows.set(number, { ...season, season: number, local: season,
      collection_state: undefined, local_count: localSeasonCount(season), collected_count: localSeasonCount(season) })
  }
  for (const season of collection || []) {
    const number = Number(season.season)
    if (!Number.isInteger(number) || number < 0) continue
    const previous = rows.get(number)
    rows.set(number, { ...previous, ...season, season: number,
      name: season.name || previous?.name || '',
      poster_path: previous?.poster_path || season.poster_path || '',
      local: previous?.local || null })
  }
  return [...rows.values()].sort((a, b) => a.season - b.season)
}

export function sourceSeasonPath(source) {
  const show = Number(source?.show_id), season = Number(source?.season)
  if (!Number.isInteger(show) || show <= 0 || !Number.isInteger(season) || season < 0) return ''
  return `/tv/${show}/s/${season}`
}

export function sourceEpisodePath(source) {
  const season = sourceSeasonPath(source), episode = Number(source?.episode_id)
  return season && Number.isInteger(episode) && episode > 0 ? `${season}/e/${episode}` : ''
}

export function uniqueSeasonSources(sources = []) {
  const seen = new Set()
  return (sources || []).filter(source => {
    const key = sourceSeasonPath(source)
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}
