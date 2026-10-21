<template>
  <div class="detail media-detail person-detail" v-if="p">
    <div class="hero media-hero">
      <div class="hero-inner">
        <div class="topbar">
          <JzButton @click="$router.back()" type="button" icon="back">返回</JzButton>
        </div>
        <div class="hero-main">
          <img v-if="p.avatar && p.avatar !== '-'" :src="posterUrl(p.avatar)" class="person-photo" :alt="(p.name || '人物') + ' 头像'" />
          <ArtworkPlaceholder v-else class="person-photo avatar-fallback" kind="person" :label="p.name" />
          <div class="hero-info">
            <div class="hero-heading">
              <h1>{{ p.name }}</h1>
              <p v-if="countsLine" class="meta-line">{{ countsLine }}</p>
              <p v-if="p.birthday || p.place_of_birth" class="meta-line">{{ [p.birthday, p.place_of_birth].filter(Boolean).join(' · ') }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="sections">
      <section class="card-block">
        <div class="section-head"><h3>人物简介</h3><JzButton :disabled="bioLoading || bioRefreshing" @click="refreshBio" type="button" icon="refresh">{{ bioRefreshing ? '更新中…' : '更新简介' }}</JzButton></div>
        <p v-if="bioMsg" class="page-feedback" role="status">{{ bioMsg }}</p>
        <MediaOverview v-if="(p.biography || '').trim()" :text="p.biography" />
        <p v-else-if="bioLoading" class="empty">简介加载中…</p>
        <p v-else class="empty">暂无简介</p>
      </section>

      <section v-if="p.acting.length" class="card-block">
        <h3>参演 {{ p.acting.length }}</h3>
        <div class="work-wall">
          <div v-for="w in p.acting" :key="w.id" class="work-card" tabindex="0" role="link" @keydown.enter.self="$event.currentTarget.click()" @click="$router.push('/m/' + w.id)">
            <div class="poster-wrap">
              <img v-if="w.poster_path" :src="posterUrl(w.poster_path)" loading="lazy" :alt="w.title || '海报'" />
              <ArtworkPlaceholder v-else class="no-poster" kind="poster" :label="w.title" />
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
          <div v-for="w in p.directing" :key="w.id" class="work-card" tabindex="0" role="link" @keydown.enter.self="$event.currentTarget.click()" @click="$router.push('/m/' + w.id)">
            <div class="poster-wrap">
              <img v-if="w.poster_path" :src="posterUrl(w.poster_path)" loading="lazy" :alt="w.title || '海报'" />
              <ArtworkPlaceholder v-else class="no-poster" kind="poster" :label="w.title" />
              <ScoreBadge :score="w.tmdb_rating" source="tmdb" />
            </div>
            <div class="cast-name">{{ w.title }} <span v-if="w.year">({{ w.year }})</span></div>
          </div>
        </div>
      </section>

      <section v-if="p.tv_works && p.tv_works.length" class="card-block">
        <h3>参演剧集 {{ p.tv_works.length }}</h3>
        <div class="work-wall">
          <div v-for="w in p.tv_works" :key="'tv' + w.show_id" class="work-card" tabindex="0" role="link" @keydown.enter.self="$event.currentTarget.click()" @click="$router.push('/tv/' + w.show_id)">
            <div class="poster-wrap">
              <img v-if="w.poster_path" :src="posterUrl(w.poster_path)" loading="lazy" :alt="w.title || '海报'" />
              <ArtworkPlaceholder v-else class="avatar-fallback" kind="person" :label="w.title" />
            </div>
            <div class="cast-name">{{ w.title }} <span v-if="w.year">({{ w.year }})</span></div>
            <div v-if="showTvChar(w)" class="cast-char">{{ w.character }}</div>
          </div>
        </div>
      </section>
    </div>
  </div>
  <div v-else class="page"><p>{{ ensuring ? '人物建档中…' : (err || '加载中…') }}</p><p v-if="err"><JzButton @click="load()" type="button" icon="refresh">重试</JzButton></p></div>
</template>
<script setup>
import ArtworkPlaceholder from '../components/ArtworkPlaceholder.vue'

import JzButton from '../components/JzButton.vue'

import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, posterUrl } from '../api.js'
import { mediaParam, onLibChange } from '../libraries.js'
import MediaOverview from '../components/MediaOverview.vue'
import ScoreBadge from '../components/ScoreBadge.vue'

const route = useRoute()
const p = ref(null)
const err = ref('')
const bioMsg = ref('')
const bioRefreshing = ref(false)
const bioLoading = ref(false)
const ensuring = ref(false)  // TV 跳转建档中（404 承接）：页内 loading，完后刷新显示
let unsubLib = null
let loadGeneration = 0
let controller = null
let disposed = false

function current(context) {
  return !disposed && context.generation === loadGeneration
    && context.id === String(route.params.tmdb_id) && context.media === mediaParam()
}

const countsLine = computed(() => {
  if (!p.value) return ''
  const parts = []
  if (p.value.acting.length) parts.push(`参演 ${p.value.acting.length} 部`)
  if (p.value.directing.length) parts.push(`执导 ${p.value.directing.length} 部`)
  if ((p.value.tv_works || []).length) parts.push(`参演剧集 ${(p.value.tv_works || []).length} 部`)
  return parts.join(' · ')
})
// 角色名仅英语原语言可信（与详情页同一规则）
function showChar(w) {
  return String(w.original_language || '').toLowerCase().startsWith('en') && w.character_name
}
// 剧集角色名同规则（tv_works 用 character 字段）
function showTvChar(w) {
  return String(w.original_language || '').toLowerCase().startsWith('en') && w.character
}
// 作品列表随当前媒体库聚合（v18 读聚合）；单媒体库不带参数=全库
function personUrl(context, refresh = false) {
  return '/api/persons/' + context.id + (refresh ? '/refresh' : '')
    + (context.media != null ? '?media_library=' + context.media : '')
}
async function load() {
  if (disposed) return
  controller?.abort()
  controller = new AbortController()
  const context = { id: String(route.params.tmdb_id), media: mediaParam(),
    generation: ++loadGeneration, signal: controller.signal }
  const qname = String(route.query.name || '').trim()
  const qprofile = String(route.query.profile || '').trim()
  p.value = null
  err.value = ''
  bioMsg.value = ''
  bioRefreshing.value = false
  bioLoading.value = false
  ensuring.value = false
  try {
    // 本地数据先秒开（后端纯本地查询，不等 TMDB）
    const data = await api(personUrl(context), { signal: context.signal })
    if (!current(context)) return
    p.value = data
  } catch (e) {
    if (!current(context)) return
    // TV 跳转带 query 建档：无人物行时自动建档后重取（跳转本身零等待，
    // 加载态收敛在此，杜绝连点竞态）；直接访问无 query 仍 404。
    const msg404 = String((e && e.message) || '')
    if (msg404.startsWith('404') && (qname || qprofile)) {
      ensuring.value = true
      try {
        await api('/api/persons/ensure', {
          method: 'POST',
          body: JSON.stringify({ tmdb_id: Number(context.id), name: qname, profile_path: qprofile }),
          signal: context.signal,
        })
        if (!current(context)) return
        const data = await api(personUrl(context), { signal: context.signal })
        if (!current(context)) return
        p.value = data
      } catch (e2) {
        if (!current(context)) return
        err.value = '人物建档失败：' + e2.message
        return
      } finally {
        if (current(context)) ensuring.value = false
      }
    } else {
      err.value = '人物不存在：' + e.message
      return
    }
  }
  if (!p.value) return
  try {
    // 简介从未抓过才后台补齐，头像/参演/执导已随首屏展示
    if (!(p.value.biography || '').trim() && !(p.value.bio_fetched_at || 0)) {
      bioLoading.value = true
      try {
        const full = await api(personUrl(context, true), { method: 'POST', signal: context.signal })
        // 路由已切走则丢弃过期回包
        if (current(context)) p.value = full
      } catch (e) { if (current(context)) bioMsg.value = '简介加载失败，可点击更新简介重试：' + e.message }
      finally {
        if (current(context)) bioLoading.value = false
      }
    }
  } catch (e) {
    if (current(context)) err.value = '人物不存在：' + e.message
  }
}
async function refreshBio() {
  if (disposed || !p.value || bioRefreshing.value || bioLoading.value) return
  const context = { id: String(route.params.tmdb_id), media: mediaParam(),
    generation: loadGeneration, signal: controller.signal }
  bioRefreshing.value = true
  bioMsg.value = ''
  try {
    const data = await api(personUrl(context, true), { method: 'POST', signal: context.signal })
    if (!current(context)) return
    p.value = data
    bioMsg.value = (p.value.biography || '').trim() ? '已更新' : '远端暂无简介'
  } catch (e) {
    if (current(context)) bioMsg.value = '更新失败：' + e.message
  } finally { if (current(context)) bioRefreshing.value = false }
}
onMounted(() => {
  load()
  unsubLib = onLibChange(() => load())
})
onUnmounted(() => { disposed = true; loadGeneration++; controller?.abort(); unsubLib?.() })
watch(() => route.params.tmdb_id, load)
</script>
<style scoped>
.detail { padding-bottom: 24px; }
.hero-inner { position: relative; padding: 12px; max-width: 1080px; }
.topbar { display: flex; justify-content: space-between; align-items: center; }
.person-photo { width: 160px; border-radius: 12px; box-shadow: 0 8px 28px rgba(0,0,0,.55); }
.avatar-fallback { display: flex; align-items: center; justify-content: center; font-size: 4rem; color: #666; background: #262626; border: 1px solid #3a3a3a; aspect-ratio: 3/4; }
.sections { padding: 0 12px; max-width: 1080px; display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }
.overview { white-space: pre-wrap; }
.work-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 12px; }
.work-card { cursor: pointer; min-width: 0; }
</style>
