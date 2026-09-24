<template>
  <section v-if="(directors || []).length" class="card-block crew-sec">
    <p class="crew"><span class="role">导演</span>
      <span v-for="(d, i) in directors" :key="'d' + (castId(d) || d.name)">
        <span class="actor-chip" :class="{ clickable: !!castId(d) }" @click="onPick(d)">{{ d.name }}</span><span v-if="i < directors.length - 1"> </span>
      </span>
    </p>
  </section>
</template>
<script setup>
// 全站导演行单源（P1）：电影可点 chip 与集纯文本统一为可点 chip；
// 无 id（纯文本导演）时不可点、无手型；剧/季无导演数据时不渲染。
import { useRouter } from 'vue-router'
import { castId } from '../cast.js'

defineProps({
  directors: { type: Array, default: () => [] },
})
const router = useRouter()
function onPick(d) {
  const tid = castId(d)
  if (!Number.isFinite(tid) || tid <= 0) return
  const profile = typeof d.profile_path === 'string' && d.profile_path.startsWith('/')
    ? d.profile_path : ''
  router.push({ path: '/p/' + tid,
    query: { name: d.name || '', profile } })
}
</script>
<style scoped>
.crew-sec { margin: 12px; }
.crew { margin: 0; font-size: 0.9375rem; color: #ddd; }
.crew .role { color: #888; font-size: 0.8125rem; margin-right: 8px; }
.actor-chip { display: inline-block; padding: 2px 12px; border-radius: 999px; background: #262626; border: 1px solid #3a3a3a; color: #ddd; font-size: 0.875rem; }
.actor-chip.clickable { cursor: pointer; }
.actor-chip.clickable:hover { border-color: #6ab0ff; color: #9ecfff; }
</style>
