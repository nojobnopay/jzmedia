<template>
  <article class="online-record-card" :class="{ 'online-record-card--compact': compact, 'online-record-card--poster': vertical, 'is-unavailable': !row.available }" :data-online-state-item="row.item.id">
    <div class="online-record-artwork-wrap">
      <JzButton class="online-record-artwork-button" variant="ghost" :data-online-library-detail="row.item.id"
        :aria-label="'查看详情 ' + row.item.title" :disabled="disabled || !row.available" @click="$emit('detail', row)">
        <OnlineArtwork class="online-record-artwork" :path="row.poster_path || ''" :title="row.item.title" />
      </JzButton>
      <JzButton v-if="compact && row.progress" class="online-record-cover-play" variant="primary" icon="play" icon-only
        :data-online-resume="row.item.id" :aria-label="playLabel + ' ' + row.item.title" :title="playLabel"
        :disabled="disabled || !row.available || starting" @click="$emit('resume', row)" />
    </div>
    <div class="online-record-body">
      <h3 class="online-record-title"><JzButton variant="ghost" class="online-record-title-button"
        :aria-label="'查看详情 ' + row.item.title" :title="row.item.title" :disabled="disabled || !row.available" @click="$emit('detail', row)">{{ row.item.title }}</JzButton></h3>
      <p v-if="row.episode_label && (!compact || row.available)" class="online-record-episode" :title="row.episode_label">{{ row.episode_label }}</p>
      <p class="online-record-source" :title="row.source.name">{{ row.source.name }}<span v-if="!compact"> · {{ row.item.kind === 'tv' ? '剧集' : '电影' }}</span></p>
      <p v-if="row.progress && (!compact || row.available)" class="online-record-position"><AppIcon v-if="row.progress.completed" name="check" :size="14" />{{ progressLabel }}</p>
      <time v-if="!compact && updatedDate" class="online-record-updated" :datetime="updatedDate.toISOString()">最近观看 {{ updatedLabel }}</time>
      <p v-if="!row.available" class="online-record-unavailable">{{ reasonLabel(row.reason) }}</p>
      <div v-if="!compact && (row.progress || row.favorite)" class="online-record-actions">
        <JzButton v-if="row.progress" :variant="vertical ? 'secondary' : 'primary'" icon="play" :icon-only="vertical"
          :data-online-resume="row.item.id" :aria-label="playLabel + ' ' + row.item.title" :title="playLabel"
          :disabled="disabled || !row.available || starting" @click="$emit('resume', row)"><span v-if="!vertical">{{ playLabel }}</span></JzButton>
        <ActionMenu class="online-record-menu" label="更多">
          <JzButton v-if="row.favorite" variant="ghost" icon="heart-filled" :data-online-favorite="row.item.id" :disabled="busy"
            @click="$emit('favorite', row, false)">取消收藏</JzButton>
          <JzButton v-if="row.progress" variant="ghost" icon="trash" :data-online-history-delete="row.item.id" :disabled="busy"
            @click="$emit('clear', row)">清除观看记录</JzButton>
        </ActionMenu>
      </div>
    </div>
    <div v-if="progressPercent !== null" class="online-record-progress" role="progressbar" :aria-label="row.item.title + ' 观看进度'"
      :aria-valuenow="Math.round(progressPercent)" :aria-valuemin="0" :aria-valuemax="100" :aria-valuetext="progressLabel">
      <span :style="{ width: progressPercent + '%' }" />
    </div>
  </article>
</template>

<script setup>
import { computed } from 'vue'
import { fmtTime } from '../playerLabels.js'
import ActionMenu from './ActionMenu.vue'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'
import OnlineArtwork from './OnlineArtwork.vue'

const props = defineProps({
  row: { type: Object, required: true },
  compact: Boolean,
  vertical: Boolean,
  disabled: Boolean,
  starting: Boolean,
  busy: Boolean,
  reasonLabel: { type: Function, default: () => '视频源暂不可用，记录已保留。' },
})
defineEmits(['detail', 'resume', 'favorite', 'clear'])
const playLabel = computed(() => props.row.progress?.completed ? '重新播放' : '继续播放')
const knownDuration = computed(() => Number.isFinite(props.row.progress?.duration) && props.row.progress.duration > 0)
const position = computed(() => Math.max(0, Number(props.row.progress?.position) || 0))
const progressPercent = computed(() => !knownDuration.value ? null
  : props.row.progress.completed ? 100 : Math.min(100, position.value / props.row.progress.duration * 100))
const progressLabel = computed(() => props.row.progress?.completed ? '已看完'
  : knownDuration.value ? `${fmtTime(position.value)} / ${fmtTime(props.row.progress.duration)}` : `已观看 ${fmtTime(position.value)}`)
const updatedDate = computed(() => {
  const timestamp = props.row.progress?.updated_at
  if (!Number.isFinite(timestamp) || timestamp <= 0) return null
  const date = new Date(timestamp * 1000)
  return Number.isNaN(date.getTime()) ? null : date
})
const updatedLabel = computed(() => updatedDate.value?.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) || '')
</script>

<style scoped>
.online-record-card { position: relative; display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: var(--jz-gap-m); min-width: 0; padding: var(--jz-gap-m); box-sizing: border-box; background: var(--jz-surface); border-radius: var(--jz-radius-m); }
.online-record-artwork-wrap { position: relative; min-width: 0; align-self: start; }
.online-record-artwork-button.jz-button { display: block; width: 100%; min-width: 0; min-height: 0; padding: 0; border: 0; border-radius: var(--jz-radius-m); }
.online-record-artwork { width: 100%; aspect-ratio: 2 / 3; }
.online-record-body { display: flex; flex-direction: column; align-items: flex-start; min-width: 0; }
.online-record-title { width: 100%; margin: 0; font-size: var(--jz-font-m); line-height: 1.4; }
.online-record-title-button.jz-button { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; width: 100%; min-width: 0; min-height: 0; padding: 0; border: 0; border-radius: 2px; color: var(--jz-text); font-size: inherit; text-align: left; line-height: inherit; overflow-wrap: anywhere; }
.online-record-title-button.jz-button:hover:not(:disabled) { background: transparent; text-decoration: underline; text-underline-offset: 3px; }
.online-record-episode, .online-record-source, .online-record-position, .online-record-updated, .online-record-unavailable { max-width: 100%; margin: var(--jz-gap-xs) 0 0; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.4; }
.online-record-episode, .online-record-source { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.online-record-episode { color: var(--jz-text); }
.online-record-position { display: flex; align-items: center; gap: 4px; font-variant-numeric: tabular-nums; }
.online-record-updated { color: var(--jz-text-faint); }
.online-record-unavailable { overflow-wrap: anywhere; }
.online-record-actions { display: flex; align-items: center; flex-wrap: wrap; width: 100%; gap: var(--jz-gap-s); margin-top: auto; padding-top: var(--jz-gap-m); }
.online-record-actions > .jz-button { padding-inline: var(--jz-gap-s); }
.online-record-menu { margin-left: auto; }
.online-record-menu :deep(summary) { gap: var(--jz-gap-xs); padding-inline: var(--jz-gap-xs); color: var(--jz-text-dim); border-color: transparent; background: transparent; }
.online-record-menu :deep(summary:hover) { background: var(--jz-surface-3); color: var(--jz-text); }
.online-record-progress { position: absolute; inset: auto var(--jz-gap-m) 0; height: 3px; overflow: hidden; background: var(--jz-border); border-radius: var(--jz-radius-pill); }
.online-record-progress > span { display: block; height: 100%; background: var(--jz-accent); }
.online-record-card.is-unavailable .online-record-title-button { color: var(--jz-text-dim); }
.online-record-card--compact { width: 280px; height: 112px; grid-template-columns: 64px minmax(0, 1fr); padding: var(--jz-gap-s); }
.online-record-card--compact .online-record-title { line-height: 1.3; }
.online-record-card--compact .online-record-episode, .online-record-card--compact .online-record-source, .online-record-card--compact .online-record-position { margin-top: 2px; line-height: 1.2; }
.online-record-card--compact .online-record-position { margin-top: auto; }
.online-record-card--compact .online-record-unavailable { display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; margin-top: 4px; }
.online-record-card--compact .online-record-progress { inset-inline: var(--jz-gap-s); }
.online-record-cover-play.jz-button { position: absolute; bottom: 3px; left: 50%; transform: translateX(-50%); width: 44px; height: 44px; min-height: 44px; border-radius: 50%; box-shadow: 0 2px 10px var(--jz-overlay-soft); }
.online-record-card--poster { display: flex; flex-direction: column; gap: 0; padding: 0 0 var(--jz-gap-s); background: transparent; }
.online-record-card--poster .online-record-artwork-wrap { width: 100%; }
.online-record-card--poster .online-record-body { flex: 1; padding-top: var(--jz-gap-s); }
.online-record-card--poster .online-record-actions { padding-top: var(--jz-gap-s); }
.online-record-card--poster .online-record-progress { inset-inline: 0; }
@media (max-width: 700px), (pointer: coarse) {
  .online-record-title-button.jz-button { min-height: 44px; }
  .online-record-card--compact { width: 260px; }
  .online-record-card--compact .online-record-title-button.jz-button { min-height: 44px; }
  .online-record-menu :deep(summary) { min-width: 44px; min-height: 44px; }
}
</style>
