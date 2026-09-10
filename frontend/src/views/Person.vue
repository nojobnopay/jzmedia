<template>
  <div class="detail" v-if="p">
    <div class="hero">
      <div class="hero-inner">
        <div class="topbar">
          <button @click="$router.back()">‹ 返回</button>
        </div>
        <div class="hero-main">
          <img v-if="p.avatar && p.avatar !== '-'" :src="posterUrl(p.avatar)" class="person-photo" />
          <div v-else class="person-photo avatar-fallback">{{ (p.name || '?').slice(0, 1) }}</div>
          <div class="hero-info">
            <h2>{{ p.name }}</h2>
            <p v-if="countsLine" class="meta-line">{{ countsLine }}</p>
            <p v-if="p.birthday || p.place_of_birth" class="meta-line">{{ [p.birthday, p.place_of_birth].filter(Boolean).join(' · ') }}</p>
          </div>
        </div>
      </div>
    </div>

    <div class="sections">
      <section class="card-block">
        <h3>简介 <button @click="refreshBio" style="margin-left:8px">{{ bioMsg || '刷新简介' }}</button></h3>
        <p v-if="(p.biography || '').trim()" class="overview">{{ p.biography }}</p>
        <p v-else-if="bioLoading" class="empty">简介加载中…</p>
        <p v-else class="empty">暂无简介</p>
      </section>

      <section v-if="p.acting.length" class="card-block">
        <h3>参演 {{ p.acting.length }}</h3>
        <div class="work-wall">
          <div v-for="w in p.acting" :key="w.id" class="work-card" @click="$router.push('/m/' + w.id)">
            <div class="poster-wrap">
              <img v-if="w.poster_path" :src="posterUrl(w.poster_path)" loading="lazy" />
              <ScoreBadge :score="w.tmdb_rating" source="tmdb" />
            </div>
            <div class="cast-name">{{ w.title }} <span v-if="w.year">({{ w.year }})</span></div>
            <div v-if="showChar(w)" class="cast-char">{{ w.character_name }}</div>
          </div>
        </div>
      </section>

      <section v-if="p.directing.length" class="card-block">
        <h3>执导 {{ p.directing.length }}</h3>
        <div class="work-wall">
          <div v-for="w in p.directing" :key="w.id" class="work-card" @click="$router.push('/m/' + w.id)">
            <div class="poster-wrap">
              <img v-if="w.poster_path" :src="posterUrl(w.poster_path)" loading="lazy" />
              <ScoreBadge :score="w.tmdb_rating" source="tmdb" />
            </div>
            <div class="cast-name">{{ w.title }} <span v-if="w.year">({{ w.year }})</span></div>
          </div>
        </div>
      </section>
    </div>
  </div>
  <div v-else class="page"><p>{{ err || '加载中…' }}</p></div>
</template>
<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, posterUrl } from '../api.js'
import ScoreBadge from '../components/ScoreBadge.vue'

const route = useRoute()
const p = ref(null)
const err = ref('')
const bioMsg = ref('')
const bioLoading = ref(false)

const countsLine = computed(() => {
  if (!p.value) return ''
  const parts = []
  if (p.value.acting.length) parts.push(`参演 ${p.value.acting.length} 部`)
  if (p.value.directing.length) parts.push(`执导 ${p.value.directing.length} 部`)
  return parts.join(' · ')
})
// 角色名仅英语原语言可信（与详情页同一规则）
function showChar(w) {
  return String(w.original_language || '').toLowerCase().startsWith('en') && w.character_name
}
async function load() {
  p.value = null
  err.value = ''
  bioLoading.value = false
  const tid = route.params.tmdb_id
  try {
    // 本地数据先秒开（后端纯本地查询，不等 TMDB）
    p.value = await api('/api/persons/' + tid)
    // 简介从未抓过才后台补齐，头像/参演/执导已随首屏展示
    if (!(p.value.biography || '').trim() && !(p.value.bio_fetched_at || 0)) {
      bioLoading.value = true
      try {
        const full = await api('/api/persons/' + tid + '/refresh', { method: 'POST' })
        // 路由已切走则丢弃过期回包
        if (route.params.tmdb_id === tid) p.value = full
      } catch (e) { console.warn('bio auto-fill failed:', e); /* 保持“暂无简介”，用户可点刷新简介重试 */ }
      finally {
        if (route.params.tmdb_id === tid) bioLoading.value = false
      }
    }
  } catch (e) {
    err.value = '人物不存在：' + e.message
  }
}
async function refreshBio() {
  bioMsg.value = '刷新中…'
  try {
    p.value = await api('/api/persons/' + route.params.tmdb_id + '/refresh', { method: 'POST' })
    bioMsg.value = (p.value.biography || '').trim() ? '已更新' : '远端暂无简介'
  } catch (e) {
    bioMsg.value = '刷新失败'
  }
  setTimeout(() => { bioMsg.value = '' }, 3000)
}
onMounted(load)
watch(() => route.params.tmdb_id, load)
</script>
<style scoped>
.detail { padding-bottom: 24px; }
.hero-inner { position: relative; padding: 12px; max-width: 1080px; }
.topbar { display: flex; justify-content: space-between; align-items: center; }
.hero-main { display: flex; gap: 20px; margin-top: 12px; align-items: flex-start; }
.person-photo { width: 160px; border-radius: 12px; box-shadow: 0 8px 28px rgba(0,0,0,.55); }
.avatar-fallback { display: flex; align-items: center; justify-content: center; font-size: 4rem; color: #666; background: #262626; border: 1px solid #3a3a3a; aspect-ratio: 3/4; }
.hero-info { min-width: 0; }
.hero-info h2 { margin: 0 0 8px; font-size: 1.875rem; }
.meta-line { color: #aaa; font-size: 1rem; margin: 8px 0; }
.sections { padding: 0 12px; max-width: 1080px; display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.overview { margin: 0; line-height: 1.8; color: #e6e6e6; font-size: 1rem; white-space: pre-wrap; }
.empty { margin: 0; color: #777; font-size: 0.9375rem; }
.work-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 12px; }
.work-card { cursor: pointer; min-width: 0; }
.cast-name { font-size: 0.875rem; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cast-char { font-size: 0.75rem; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
