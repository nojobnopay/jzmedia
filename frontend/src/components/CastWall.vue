<template>
  <section v-if="(items || []).length" class="card-block cast-sec">
    <h3>{{ title }} <span class="dim">{{ items.length }}<template v-if="subtitle"> · {{ subtitle }}</template></span></h3>
    <div class="cast-wrap">
      <JzButton v-if="bar.show" class="cast-nav left" icon="chevron-left" icon-only aria-label="向左滚动" :disabled="!canLeft" @click="scrollByDir(-1)" />
      <div ref="rowRef" class="cast-wall" @scroll="onScroll">
        <component :is="castId(p.raw || p) ? RouterLink : 'div'" v-for="p in items" :key="castId(p.raw || p) || castName(p.raw || p)"
          class="cast-card" :class="{ clickable: !!castId(p.raw || p) }"
          :title="p.character ? `${p.name} 饰 ${p.character}` : p.name"
          :to="castId(p.raw || p) ? personLink(p.raw || p) : undefined">
          <img v-if="p.avatarSrc" :src="p.avatarSrc" loading="lazy"
            class="cast-avatar" :alt="p.name || '演员'" @error="onImgError(p)" />
          <ArtworkPlaceholder v-else class="avatar-fallback" kind="person" :label="p.name" />
          <div class="cast-name">{{ p.name }}</div>
          <div v-if="showChar && p.character" class="cast-char">{{ p.character }}</div>
          <div v-if="p.guest" class="guest-badge">客串</div>
        </component>
      </div>
      <JzButton v-if="bar.show" class="cast-nav right" icon="chevron-right" icon-only aria-label="向右滚动" :disabled="!canRight" @click="scrollByDir(1)" />
      <div v-if="bar.show" class="cast-bar" aria-hidden="true">
        <div class="cast-bar-thumb" :style="{ left: bar.left + '%', width: bar.width + '%' }"></div>
      </div>
    </div>
  </section>
</template>
<script setup>
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'
import JzButton from './JzButton.vue'

// 全站演职员墙单源（P1）：圆形 150px（剧集基准），电影/剧/季/集四页复用。
// 图片走 cast.js 归一解析（TMDB profile_path 走代理，本地 avatar 走 /posters），
// 失败回退首字母占位；无 id 不可点；角色名仅英文原语言展示。
// 横向滚动与库中类似行同语义：隐藏原生滚动条，箭头按实测滚动量显隐 + 3px 细进度线。
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { shouldShowCharacter, normalizeCast, castId, castName } from '../cast.js'
import { canScroll, scrollStep } from '../recentPlayed.js'

const props = defineProps({
  cast: { type: Array, default: () => [] },
  originalLanguage: { type: String, default: '' },
  title: { type: String, default: '演职员' },
  subtitle: { type: String, default: '' },
})
const items = computed(() => (props.cast || []).map(normalizeCast))
const showChar = computed(() => shouldShowCharacter(props.originalLanguage))

const rowRef = ref(null)
const canLeft = ref(false)
const canRight = ref(false)
const bar = ref({ show: false, left: 0, width: 100 })
let rowIO = null

function updateScroll() {
  const el = rowRef.value
  if (!el) {
    canLeft.value = false
    canRight.value = false
    bar.value = { show: false, left: 0, width: 100 }
    return
  }
  const s = canScroll({ scrollLeft: el.scrollLeft, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth })
  canLeft.value = s.left
  canRight.value = s.right
  const max = el.scrollWidth - el.clientWidth
  if (max <= 0) {
    bar.value = { show: false, left: 0, width: 100 }
    return
  }
  const w = Math.max(8, (el.clientWidth / el.scrollWidth) * 100)
  const l = (el.scrollLeft / max) * (100 - w)
  bar.value = { show: true, left: l, width: w }
}
function onScroll() { updateScroll() }
function scrollByDir(dir) {
  const el = rowRef.value
  if (!el) return
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollBy({ left: dir * scrollStep(el.clientWidth), behavior: reduce ? 'instant' : 'smooth' })
}

watch(items, () => nextTick(updateScroll))
onMounted(() => {
  updateScroll()
  window.addEventListener('resize', updateScroll)
  try {
    if (window.ResizeObserver && rowRef.value) {
      rowIO = new ResizeObserver(updateScroll)
      rowIO.observe(rowRef.value)
    }
  } catch (e) { /* 不支持则只在滚动/重载时更新 */ }
})
onUnmounted(() => {
  window.removeEventListener('resize', updateScroll)
  if (rowIO) { try { rowIO.disconnect() } catch (e) { /* 忽略 */ } rowIO = null }
})

function onImgError(p) {
  // 图片 502/坏图时切占位：清掉 raw 的双字段，normalize 后 avatarSrc 为空
  const raw = p.raw || {}
  raw.profile_path = ''
  raw.avatar = ''
  p.avatarSrc = ''
}
function personLink(raw) {
  const tid = castId(raw)
  if (!Number.isFinite(tid) || tid <= 0) return
  const profile = typeof raw.profile_path === 'string' && raw.profile_path.startsWith('/')
    ? raw.profile_path : ''
  return { path: '/p/' + tid, query: { name: castName(raw) || '', profile } }
}
</script>
<style scoped>
.cast-sec { margin: 0; padding: 0; background: transparent; }
.cast-sec h3 { margin: 0 0 var(--jz-gap-l); font-size: 1.125rem; font-weight: 600; }
.cast-wrap { position: relative; }
.cast-wall { display: flex; gap: var(--jz-gap-l); overflow-x: auto; padding: 3px 3px var(--jz-gap-s); margin: -3px; scroll-snap-type: x proximity; scroll-padding-inline: 3px; scroll-behavior: smooth; scrollbar-width: none; }
.cast-wall::-webkit-scrollbar { display: none; }
.cast-bar { position: relative; height: 3px; margin: 0 2px; }
.cast-bar-thumb { position: absolute; top: 0; height: 100%; border-radius: 999px; background: rgba(255,255,255,.18); transition: background .15s; }
.cast-wrap:hover .cast-bar-thumb { background: rgba(255,255,255,.32); }
.cast-nav { position: absolute; top: 21px; z-index: 2; width: 32px; height: 44px; opacity: 0; transition: opacity .15s; }
.cast-nav.left { left: 0; }
.cast-nav.right { right: 0; }
.cast-wrap:hover .cast-nav, .cast-nav:focus-visible { opacity: 1; }
/* 到头时保留占位并置灰（不用 v-if 移除，避免箭头消失导致误点下方卡片） */
.cast-wrap .cast-nav:disabled { opacity: 0; }
.cast-wrap:hover .cast-nav:disabled, .cast-nav:disabled:focus-visible { opacity: .35; }
.cast-card { flex: 0 0 96px; min-width: 0; text-align: center; text-decoration: none; color: var(--jz-text); scroll-snap-align: start; }
.cast-avatar, .avatar-fallback { width: 80px; height: 80px; margin: 0 auto var(--jz-gap-s); border-radius: 50%; background: var(--jz-surface-3); }
.cast-avatar { display: block; object-fit: cover; object-position: center 20%; }
.avatar-fallback { display: flex; align-items: center; justify-content: center; color: var(--jz-text-dim); font-size: 1.5rem; font-weight: 500; }
.cast-card.clickable:hover .cast-name { color: var(--jz-link); }
.cast-card:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 2px; border-radius: var(--jz-radius-s); }
.cast-name { font-size: var(--jz-font-m); }
.cast-char { margin-top: 3px; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.guest-badge { display: inline-block; margin-top: 3px; font-size: var(--jz-font-s); color: var(--jz-text-dim); }
.dim { color: var(--jz-text-dim); font-size: var(--jz-font-s); font-weight: 400; margin-left: var(--jz-gap-xs); }
@media (max-width: 700px) { .cast-sec { padding: 0; }.cast-wall { gap: var(--jz-gap-s); }.cast-card { flex-basis: 84px; }.cast-avatar, .avatar-fallback { width: 72px; height: 72px; } }
@media (hover: none) { .cast-nav { display: none; } }
@media (prefers-reduced-motion: reduce) { .cast-wall { scroll-behavior: auto; }.cast-bar-thumb, .cast-nav { transition: none; } }
</style>
