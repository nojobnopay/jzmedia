<template>
  <section v-if="(items || []).length" class="card-block cast-sec">
    <h3>{{ title }} <span class="dim">{{ items.length }}<template v-if="subtitle"> · {{ subtitle }}</template></span></h3>
    <div class="cast-wall">
      <component :is="castId(p.raw || p) ? RouterLink : 'div'" v-for="p in items" :key="castId(p.raw || p) || castName(p.raw || p)"
        class="cast-card" :class="{ clickable: !!castId(p.raw || p) }"
        :title="p.character ? `${p.name} 饰 ${p.character}` : p.name"
        :to="castId(p.raw || p) ? personLink(p.raw || p) : undefined">
        <img v-if="p.avatarSrc" :src="p.avatarSrc" loading="lazy"
          class="cast-avatar" :alt="p.name || '演员'" @error="onImgError(p)" />
        <div v-else class="avatar-fallback" aria-hidden="true">{{ (p.name || '?').slice(0, 1) }}</div>
        <div class="cast-name">{{ p.name }}</div>
        <div v-if="showChar && p.character" class="cast-char">{{ p.character }}</div>
        <div v-if="p.guest" class="guest-badge">客串</div>
      </component>
    </div>
  </section>
</template>
<script setup>
// 全站演职员墙单源（P1）：圆形 150px（剧集基准），电影/剧/季/集四页复用。
// 图片走 cast.js 归一解析（TMDB profile_path 走代理，本地 avatar 走 /posters），
// 失败回退首字母占位；无 id 不可点；角色名仅英文原语言展示。
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { shouldShowCharacter, normalizeCast, castId, castName } from '../cast.js'

const props = defineProps({
  cast: { type: Array, default: () => [] },
  originalLanguage: { type: String, default: '' },
  title: { type: String, default: '演职员' },
  subtitle: { type: String, default: '' },
})
const items = computed(() => (props.cast || []).map(normalizeCast))
const showChar = computed(() => shouldShowCharacter(props.originalLanguage))

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
.cast-wall { display: flex; gap: var(--jz-gap-l); overflow-x: auto; padding: 3px 3px var(--jz-gap-s); margin: -3px; scroll-snap-type: x proximity; }
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
</style>
