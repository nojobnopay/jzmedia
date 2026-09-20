<template>
  <div class="bar">
    <input v-model="q" placeholder="搜剧名" @keyup.enter="load" style="flex:1" />
    <button @click="load">搜索</button>
    <span class="fhint">{{ msg }}</span>
  </div>
  <div v-if="!items.length" class="bar fhint">
    当前媒体库还没有剧集。在设置页「媒体库」对应媒体库下添加一个「剧集」类型的视频库并扫描即可（只读清单，不刮削/不改名）。
  </div>
  <div class="grid">
    <div v-for="s in items" :key="s.id" class="card show-card" @click="$router.push('/tv/' + s.id)">
      <div class="cover-empty">📺</div>
      <div class="t">{{ s.title }}<span v-if="s.year"> ({{ s.year }})</span></div>
      <div class="t fhint">{{ s.season_count }} 季 · {{ s.episode_count }} 集</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { mediaParam, loadLibs, onLibChange } from '../libraries.js'

const q = ref('')
const items = ref([])
const msg = ref('')
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

onMounted(async () => {
  try { await loadLibs(api) } catch (e) { /* 单库兜底 */ }
  await load()
  unsubLib = onLibChange(() => load())
})
onUnmounted(() => { if (unsubLib) unsubLib() })
</script>

<style scoped>
.show-card { cursor: pointer; }
.cover-empty { aspect-ratio: 2/3; display: flex; align-items: center; justify-content: center; font-size: 2.5rem; background: #262626; border-radius: 8px 8px 0 0; }
.fhint { color: #777; font-size: 0.8125rem; }
</style>
