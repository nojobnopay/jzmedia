import { api } from './api.js'

// All TV playback entry points use the same server-side version/range ordering.
export async function followingPlayback(current, showTitle, request = api) {
  if (!current || current.kind && current.kind !== 'episode') return null
  const { next } = await request(`/api/tv/episodes/${current.id}/next`)
  if (!next) return null
  const pad = n => String(n).padStart(2, '0')
  return { kind: 'episode', id: next.id,
    label: `${showTitle} S${pad(next.season)}E${pad(next.episode)}${next.title ? ' · ' + next.title : ''}${next.version > 1 ? `（V${next.version}）` : ''}` }
}
