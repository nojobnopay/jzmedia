<template>
  <div class="bar">
    <input v-model="q" placeholder="搜剧名" @keyup.enter="load" style="flex:1" />
    <button @click="load">搜索</button>
    <span class="fhint">{{ msg }}</span>
  </div>
  <ContinueWatchingRow ref="cwRef" kind="tv" :media-library-id="mediaParam()"
    @open="openShow" @resume="resume" />
  <div class="wall-head">
    <h3>全部剧集 <span class="fhint">{{ items.length }} 部</span></h3>
  </div>
  <div v-if="!items.length" class="bar fhint">
    当前媒体库还没有剧集。在设置页「媒体库」对应媒体库下添加一个「剧集」类型的视频库并扫描，
    再到「库工具 → 剧集刮削」补元数据即可。
  </div>
  <div class="grid">
    <div v-for="s in items" :key="s.id" class="card show-card" @click="openShow(s.id)">
      <div class="poster-wrap">
        <img v-if="s.poster_path" :src="posterUrl(s.poster_path)" loading="lazy"
          :alt="s.title || '剧集'" />
        <div v-else class="no-poster" aria-hidden="true">{{ (s.title || '?').slice(0, 1) }}</div>
        <span v-if="s.watched_count" class="seen">{{ s.watched_count }}/{{ s.episode_count }}</span>
        <span v-if="s.needs_review" class="review">待确认</span>
        <ScoreBadge :score="s.tmdb_rating" source="tmdb" />
      </div>
      <div class="t">{{ s.title }}<span v-if="s.year" class="yr"> ({{ s.year }})</span></div>
      <div class="t fhint">
        {{ s.season_count }} 季 · {{ s.episode_count }} 集
        <span v-if="s.status"> · {{ statusText(s.status) }}</span>
      </div>
    </div>
  </div>
  <PlayerModal v-if="playing" :version-id="playing.id" :title="playing.label"
    kind="episode" @close="playing = null" @watched="onWatched" />
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { mediaParam, loadLibs, onLibChange } from '../libraries.js'
import ContinueWatchingRow from '../components/ContinueWatchingRow.vue'
import PlayerModal from '../components/PlayerModal.vue'
import ScoreBadge from '../components/ScoreBadge.vue'

const router = useRouter()
const q = ref('')
const items = ref([])
const msg = ref('')
const playing = ref(null)
const cwRef = ref(null)
let unsubLib = null

async function load () {
  msg.value = ''
  try {
    const p = new URLSearchParams()
    if (q.value.trim()) p.set('q', q.value.trim())
    if (mediaParam() != null) p.set('media_library', String(mediaParam()))
    const qs = p.toString()
    const d = await api('/api/tv/shows' + (qs ? '?' + qs : ''))
    items.value = d.items || []
  } catch (e) {
    msg.value = '加载失败：' + e.message
  }
}

function statusText (s) {
  if (s === 'Continuing' || s === 'Returning Series' || s === 'In Production') return '连载中'
  if (s === 'Ended' || s === 'Canceled' || s === 'Cancelled') return '已完结'
  return s
}
function openShow (id) { router.push('/tv/' + id) }
function resume (m) {
  const p = m && m.progress
  if (!p || !p.version_id) return
  playing.value = { id: p.version_id, label: `${m.title}${m.subtitle ? ' ' + m.subtitle : ''}` }
}
async function onWatched () {
  const id = playing.value && playing.value.id
  if (id) {
    try {
      await api(`/api/tv/episodes/${id}/watched`, {
        method: 'POST', body: JSON.stringify({ watched: true }) })
    } catch (e) { /* 静默：墙不因此报错 */ }
  }
  if (cwRef.value && cwRef.value.reload) cwRef.value.reload()
}

onMounted(async () => {
  try { await loadLibs(api) } catch (e) { /* 单库兜底 */ }
  await load()
  unsubLib = onLibChange(() => load())
})
onUnmounted(() => { if (unsubLib) unsubLib() })
</script>

<style scoped>
.show-card { cursor: pointer; }
.wall-head { padding: 12px 12px 0; margin-top: 6px; border-top: 1px solid #2c2c2c; }
.wall-head h3 { margin: 0; font-size: 1.0625rem; color: #eee; }
.no-poster { width: 100%; aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; background: #242424; color: #555; font-size: 2.5rem; font-weight: bold; user-select: none; }
.seen, .review {
  position: absolute; top: 6px; font-size: 0.75rem; padding: 2px 8px;
  border-radius: 999px; background: rgba(0,0,0,.72); color: #7ed321;
}
.seen { left: 6px; }
.review { right: 6px; color: #ffb300; }
.yr { color: #888; font-size: 0.75rem; }
.fhint { color: #777; font-size: 0.8125rem; }
</style>
