<template>
  <section class="tv-season-catalog" aria-label="全部分集资料" :aria-busy="loading">
    <p class="catalog-note">以下按 TMDB 分集资料对照当前媒体库；观看进度保留在各个本地文件中。</p>
    <p v-if="checkedTime(data?.checked_at)" class="catalog-note">资料更新于 {{ checkedTime(data.checked_at) }}<span v-if="data?.stale"> · 显示缓存资料</span></p>
    <div v-if="error || data?.error" class="catalog-feedback" role="status">
      <span>{{ airingErrorText(error || data.error) }}<template v-if="data?.items?.length">，保留上次目录。</template></span>
      <JzButton size="compact" @click="$emit('retry')" :loading="loading">重试</JzButton>
    </div>
    <p v-if="loading && !data" role="status">正在读取分集资料…</p>
    <p v-else-if="data?.refreshing && !data.items?.length" class="catalog-note" role="status">分集资料正在更新，可稍后重新读取。<JzButton size="compact" variant="ghost" @click="$emit('retry')">重新读取</JzButton></p>
    <p v-else-if="!data?.items?.length && !error && !data?.error" class="catalog-note">暂时没有可用的分集资料。</p>
    <ol v-if="data?.items?.length" class="catalog-episodes">
      <li v-for="episode in data.items" :key="episode.tmdb_episode_id || episode.season + ':' + episode.episode" class="catalog-episode">
        <div class="catalog-episode-heading">
          <span class="catalog-number">{{ tvEpisodeLabel(episode) }}</span>
          <strong>{{ episode.title || '集名待补充' }}</strong>
          <span class="catalog-collection" :class="{ 'catalog-uncollected': episode.collection_state === 'uncollected' }">{{ collectionLabel(episode) }}</span>
        </div>
        <p class="catalog-note">{{ airingLabel(episode) }}<template v-if="episode.air_date"> · {{ episode.air_date }}</template></p>
        <details v-if="episode.overview"><summary>分集简介</summary><p>{{ episode.overview }}</p></details>
        <div v-if="episode.sources?.some(source => sourceEpisodePath(source))" class="catalog-sources">
          <router-link v-for="source in episode.sources.filter(source => sourceEpisodePath(source))" :key="source.episode_id"
            :to="sourceEpisodePath(source)">查看 {{ source.library_name || '本地文件' }} · {{ tvEpisodeLabel(source) }}</router-link>
        </div>
      </li>
    </ol>
  </section>
</template>
<script setup>
import JzButton from './JzButton.vue'
import { airingErrorText, airingLabel, checkedTime, collectionLabel, sourceEpisodePath, tvEpisodeLabel } from '../tvCollection.js'
defineProps({ data: Object, loading: Boolean, error: String })
defineEmits(['retry'])
</script>
<style scoped>
.tv-season-catalog { color: var(--jz-text); line-height: 1.6; }
.catalog-note { color: var(--jz-text-dim); font-size: var(--jz-font-s); margin: var(--jz-gap-xs) 0; }
.catalog-feedback { display: flex; flex-wrap: wrap; align-items: center; gap: var(--jz-gap-m); color: var(--jz-warn); margin: var(--jz-gap-m) 0; overflow-wrap: anywhere; }
.catalog-episodes { list-style: none; padding: 0; margin: var(--jz-gap-l) 0; }
.catalog-episode { border-top: 1px solid var(--jz-border); padding: var(--jz-gap-l) 0; }
.catalog-episode-heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--jz-gap-s); }
.catalog-episode-heading strong { font-weight: 500; overflow-wrap: anywhere; }
.catalog-number { color: var(--jz-link); font-size: var(--jz-font-s); }
.catalog-collection { margin-left: auto; color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.catalog-uncollected { color: var(--jz-warn); }
.catalog-episode details { font-size: var(--jz-font-s); }
.catalog-episode summary { cursor: pointer; width: fit-content; min-height: var(--jz-touch-target); align-content: center; color: var(--jz-text-dim); }
.catalog-episode details p { margin: 0 0 var(--jz-gap-m); max-width: 80ch; overflow-wrap: anywhere; }
.catalog-sources { display: flex; flex-wrap: wrap; gap: var(--jz-gap-m); }
.catalog-sources a { min-height: var(--jz-touch-target); align-content: center; font-size: var(--jz-font-s); }
</style>
