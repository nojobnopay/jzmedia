<template>
  <section v-if="(directors || []).length" class="card-block crew-sec">
    <p class="crew"><span class="role">导演</span>
      <span v-for="(d, i) in directors" :key="'d' + (castId(d) || d.name)">
        <component :is="castId(d) ? RouterLink : 'span'" class="actor-chip" :class="{ clickable: !!castId(d) }" :to="castId(d) ? personLink(d) : undefined">{{ d.name }}</component><span v-if="i < directors.length - 1"> </span>
      </span>
    </p>
  </section>
</template>
<script setup>
// 全站导演行单源（P1）：电影可点 chip 与集纯文本统一为可点 chip；
// 无 id（纯文本导演）时不可点、无手型；剧/季无导演数据时不渲染。
import { RouterLink } from 'vue-router'
import { castId } from '../cast.js'

defineProps({
  directors: { type: Array, default: () => [] },
})
function personLink(d) {
  const tid = castId(d)
  if (!Number.isFinite(tid) || tid <= 0) return
  const profile = typeof d.profile_path === 'string' && d.profile_path.startsWith('/')
    ? d.profile_path : ''
  return { path: '/p/' + tid, query: { name: d.name || '', profile } }
}
</script>
<style scoped>
.crew-sec { margin: 0; padding: 0; background: transparent; }
.crew { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--jz-gap-s); margin: 0; font-size: var(--jz-font-m); }
.role { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.actor-chip { display: inline-block; padding: var(--jz-gap-xs) 0; color: var(--jz-text); text-decoration: none; }
.actor-chip.clickable:hover { color: var(--jz-link); text-decoration: underline; }
@media (max-width: 700px) { .crew-sec { padding: 0; }.actor-chip { min-height: var(--jz-touch-target); align-content: center; box-sizing: border-box; } }
</style>
