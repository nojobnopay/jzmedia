<template>
  <div class="page" v-if="show">
    <div class="bar">
      <button @click="$router.push('/tv')">← 剧集</button>
      <h2 style="margin:0">{{ show.title }}<span v-if="show.year" class="fhint"> ({{ show.year }})</span></h2>
      <span class="fhint">{{ show.season_count }} 季 · {{ show.episode_count }} 集</span>
    </div>
    <div class="bar">
      <button v-for="s in show.seasons" :key="s.season"
        :class="{ on: season === s.season }" class="chip" @click="season = s.season">
        第 {{ s.season }} 季（{{ s.episode_count }}）
      </button>
    </div>
    <table class="ep-table">
      <tr v-for="e in seasonEps" :key="e.id">
        <td class="ep-no">S{{ pad(e.season) }}E{{ pad(e.episode) }}</td>
        <td class="ep-title">{{ e.title || '（未解析集名）' }}</td>
        <td>
          <button v-if="e.exists" @click="play(e)">播放</button>
          <span v-else class="fhint">文件缺失</span>
        </td>
      </tr>
    </table>
  </div>
  <div v-else class="bar">{{ msg || '加载中…' }}</div>
  <PlayerModal v-if="playing" :version-id="playing.id" :title="playing.label"
    kind="episode" @close="playing = null" />
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api.js'
import PlayerModal from '../components/PlayerModal.vue'

const route = useRoute()
const show = ref(null)
const msg = ref('')
const season = ref(null)
const playing = ref(null)

const seasonEps = computed(() =>
  (show.value?.episodes || []).filter(e => Number(e.season) === Number(season.value)))
function pad (n) { return String(n).padStart(2, '0') }
function play (e) {
  playing.value = { id: e.id, label: `${show.value.title} S${pad(e.season)}E${pad(e.episode)}${e.title ? ' · ' + e.title : ''}` }
}

onMounted(async () => {
  try {
    show.value = await api('/api/tv/shows/' + route.params.id)
    season.value = show.value.seasons?.[0]?.season ?? 0
  } catch (e) {
    msg.value = '加载失败：' + e.message
  }
})
</script>

<style scoped>
.ep-table { width: 100%; border-collapse: collapse; font-size: 0.875rem; margin-top: 8px; }
.ep-table td { padding: 8px; border-bottom: 1px solid #2c2c2c; }
.ep-no { color: #9ecfff; white-space: nowrap; }
.ep-title { color: #ddd; }
.chip { font-size: 0.8125rem; padding: 4px 10px; border: 1px solid #444; border-radius: 999px; cursor: pointer; background: #1c1c1c; }
.chip.on { border-color: #e50914; color: #ff8a8a; }
.fhint { color: #777; font-size: 0.75rem; }
</style>
