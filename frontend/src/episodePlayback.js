import { api } from './api.js'
import { episodePlaybackLabel } from './episodeTitles.js'
import { onScopeDispose, ref, watch } from 'vue'

// All TV playback entry points use the same server-side version/range ordering.
export async function followingPlayback(current, showTitle, request = api, onMissing) {
  if (!current || current.kind && current.kind !== 'episode') return null
  const { next } = await request(`/api/tv/episodes/${current.id}/next`)
  if (!next) return null
  if (next.exists === false || Number(next.missing) === 1) {
    onMissing?.(episodePlaybackLabel(next, showTitle))
    return null
  }
  return { kind: 'episode', id: next.id,
    label: episodePlaybackLabel(next, showTitle) }
}

export function useEpisodeAutoplay(playing, context, request = api) {
  const notice = ref('')
  let generation = 0
  watch([playing, context], () => { generation++; notice.value = '' }, { flush: 'sync' })
  onScopeDispose(() => { generation++ })
  async function onEnded(showTitle) {
    const current = playing.value
    if (!current || current.kind && current.kind !== 'episode') return
    const turn = ++generation
    let missing = ''
    try {
      const next = await followingPlayback(current, showTitle, request, label => { missing = label })
      if (turn !== generation) return
      playing.value = next
      if (missing) notice.value = `下一集文件缺失，已停止连播（${missing}）。`
    } catch (error) {
      if (turn !== generation) return
      playing.value = null
      notice.value = '自动连播失败：' + error.message
    }
  }
  return { onEnded, notice }
}
