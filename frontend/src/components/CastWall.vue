<template>
  <section v-if="(items || []).length" class="card-block cast-sec">
    <h3>{{ title }} <span class="dim">{{ items.length }}<template v-if="subtitle"> · {{ subtitle }}</template></span></h3>
    <div class="cast-wall">
      <div v-for="p in items" :key="castId(p.raw || p) || castName(p.raw || p)"
        class="cast-card" :class="{ clickable: !!castId(p.raw || p) }"
        :title="p.character ? `${p.name} 饰 ${p.character}` : p.name"
        @click="onPick(p.raw || p)">
        <img v-if="p.avatarSrc" :src="p.avatarSrc" loading="lazy"
          class="cast-avatar" :alt="p.name || '演员'" @error="onImgError(p)" />
        <div v-else class="avatar-fallback" aria-hidden="true">{{ (p.name || '?').slice(0, 1) }}</div>
        <div class="cast-name">{{ p.name }}</div>
        <div v-if="showChar && p.character" class="cast-char">{{ p.character }}</div>
        <div v-if="p.guest" class="guest-badge">客串</div>
      </div>
    </div>
  </section>
</template>
<script setup>
// 全站演职员墙单源（P1）：圆形 150px（剧集基准），电影/剧/季/集四页复用。
// 图片走 cast.js 归一解析（TMDB profile_path 走代理，本地 avatar 走 /posters），
// 失败回退首字母占位；无 id 不可点；角色名仅英文原语言展示。
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { shouldShowCharacter, normalizeCast, castId, castName } from '../cast.js'

const props = defineProps({
  cast: { type: Array, default: () => [] },
  originalLanguage: { type: String, default: '' },
  title: { type: String, default: '演职员' },
  subtitle: { type: String, default: '' },
})
const router = useRouter()
const items = computed(() => (props.cast || []).map(normalizeCast))
const showChar = computed(() => shouldShowCharacter(props.originalLanguage))

function onImgError(p) {
  // 图片 502/坏图时切占位：清掉 raw 的双字段，normalize 后 avatarSrc 为空
  const raw = p.raw || {}
  raw.profile_path = ''
  raw.avatar = ''
  p.avatarSrc = ''
}
function onPick(raw) {
  const tid = castId(raw)
  if (!Number.isFinite(tid) || tid <= 0) return
  const profile = typeof raw.profile_path === 'string' && raw.profile_path.startsWith('/')
    ? raw.profile_path : ''
  router.push({ path: '/p/' + tid,
    query: { name: castName(raw) || '', profile } })
}
</script>
<style scoped>
.cast-sec { margin: 12px; }
.cast-sec h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.cast-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
.cast-card { text-align: center; min-width: 0; }
.avatar-fallback { width: var(--jz-cast-size, 150px); height: var(--jz-cast-size, 150px); margin: 0 auto 6px; border-radius: 50%; background: #2a2a2a; color: #888; font-size: 2.5rem; font-weight: bold; display: flex; align-items: center; justify-content: center; user-select: none; }
.cast-avatar { width: var(--jz-cast-size, 150px); height: var(--jz-cast-size, 150px); margin: 0 auto 6px; border-radius: 50%; object-fit: cover; object-position: center 20%; display: block; background: #2a2a2a; transition: transform .15s ease; }
.cast-card.clickable { cursor: pointer; }
.cast-card.clickable:hover .cast-avatar { transform: scale(1.06); }
.guest-badge { display: inline-block; margin-top: 2px; font-size: 0.6875rem; padding: 0 6px; border-radius: 3px; color: #7aaaff; border: 1px solid rgba(120, 170, 255, .4); }
.dim { color: #777; }
</style>
