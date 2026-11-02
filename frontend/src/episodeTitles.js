// The server derives range-aware titles; older servers keep their original titles.
export function episodeDisplayTitle(episode) {
  return episode?.display_title ?? episode?.title ?? ''
}

export function episodePlaybackLabel(episode, showTitle) {
  const pad = number => String(number).padStart(2, '0')
  const range = Number(episode.episode_end) > Number(episode.episode) ? `-E${pad(episode.episode_end)}` : ''
  const title = episodeDisplayTitle(episode)
  return `${showTitle} S${pad(episode.season)}E${pad(episode.episode)}${range}${title ? ' · ' + title : ''}`
    + (Number(episode.version) > 1 ? `（V${episode.version}）` : '')
}
