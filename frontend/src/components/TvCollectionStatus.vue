<template>
  <section class="tv-collection-status" aria-label="播出与收藏信息">
    <div class="collection-head">
      <p v-if="snapshot?.latest_episode" class="latest-update">
        {{ latestHeading }} {{ tvEpisodeLabel(snapshot.latest_episode) }}<span v-if="snapshot.latest_episode.air_date"> · {{ snapshot.latest_episode.air_date }}</span>
        <span> · {{ collectionLabel(snapshot.latest_episode) }}</span>
      </p>
      <p v-else-if="loading && !snapshot">正在读取播出与收藏资料…</p>
      <p v-else-if="!snapshot?.checked_at">{{ matched ? '尚未获取播出资料' : '确认 TMDB 匹配后可查看最新播出资料' }}</p>
      <p v-else>暂无近期播出信息</p>
      <JzButton v-if="matched" size="compact" variant="ghost" icon="refresh" :loading="checking"
        @click="$emit('check')">{{ checking ? '检查中…' : '检查更新' }}</JzButton>
    </div>
    <p v-if="missing.length">尚未收藏：{{ missing.map(tvSeasonLabel).join('、') }}</p>
    <p class="collection-note">收藏范围：{{ scopeLabel || '当前媒体库' }}<template v-if="checkedTime(snapshot?.checked_at)"> · 资料更新于 {{ checkedTime(snapshot.checked_at) }}</template></p>
    <p v-if="notice" class="collection-note" role="status">{{ notice }}<JzButton size="compact" variant="ghost" @click="$emit('retry')">重新读取</JzButton></p>
    <p v-if="error || snapshot?.error" class="collection-error" role="status">{{ airingErrorText(error || snapshot.error) }}<template v-if="snapshot?.checked_at">，正在显示上次资料。</template>
      <JzButton size="compact" variant="ghost" @click="$emit('retry')">重新读取</JzButton>
    </p>
  </section>
</template>
<script setup>
import { computed } from 'vue'
import JzButton from './JzButton.vue'
import { airingErrorText, checkedTime, collectionLabel, tvEpisodeLabel, tvSeasonLabel } from '../tvCollection.js'
const props = defineProps({ snapshot: Object, matched: Boolean, loading: Boolean, checking: Boolean, error: String, notice: String, scopeLabel: String })
defineEmits(['check', 'retry'])
const latestHeading = computed(() => ({ aired: '最近播出', upcoming: '即将播出' })[props.snapshot?.latest_episode?.airing_state] || '播出时间未知')
const missing = computed(() => (props.snapshot?.missing_seasons || [])
  .map(value => Number(typeof value === 'object' ? value.season : value)).filter(value => Number.isInteger(value) && value > 0))
</script>
<style scoped>
.tv-collection-status { margin: var(--jz-gap-m) 0; padding: var(--jz-gap-m) 0; border-top: 1px solid var(--jz-border); color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.7; }
.tv-collection-status p { margin: 0; overflow-wrap: anywhere; }
.collection-head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--jz-gap-s); }
.collection-head > p { flex: 1 1 240px; }
.latest-update { color: var(--jz-text); }
.collection-note { color: var(--jz-text-dim); }
.collection-error { color: var(--jz-warn); }
</style>
