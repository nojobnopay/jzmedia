import { api } from './api.js'
import { episodePlaybackLabel } from './episodeTitles.js'

// All TV playback entry points use the same server-side version/range ordering.
export async function followingPlayback(current, showTitle, request = api) {
  if (!current || current.kind && current.kind !== 'episode') return null
  const { next } = await request(`/api/tv/episodes/${current.id}/next`)
  if (!next) return null
  return { kind: 'episode', id: next.id,
    label: episodePlaybackLabel(next, showTitle) }
}
