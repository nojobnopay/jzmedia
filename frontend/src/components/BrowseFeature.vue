<template>
  <section v-if="feature" class="browse-feature" :class="{ 'browse-feature--landscape': landscape }"
    :aria-labelledby="'feature-title-' + kind">
    <img v-if="artwork" :key="artwork" :src="artwork" alt="" class="browse-feature-art"
      :class="{ 'browse-feature-poster': !landscape }" @error="failedArtwork.add($event.currentTarget.getAttribute('src'))" />
    <div class="browse-feature-copy">
      <h2 :id="'feature-title-' + kind" class="browse-feature-title">{{ feature.title }}</h2>
      <p v-if="metadata.length" class="browse-feature-meta"><span v-for="part in metadata" :key="part" :class="{ 'cw-ep': feature.subtitle && part === feature.subtitle }">{{ part }}</span></p>
      <div class="browse-feature-actions">
        <JzButton v-if="resumable" ref="resumeButton" variant="primary" icon="play" @click="$emit('resume', feature)">继续播放</JzButton>
        <router-link :to="detailPath" :data-browse-focus="'feature-' + detailId" class="feature-detail"
          :class="{ 'feature-detail--primary': !resumable }">
          查看详情<AppIcon name="chevron-right" :size="18" />
        </router-link>
      </div>
    </div>
  </section>
  <template v-for="name in slotOrder" :key="name">
    <slot :name="name" :featured-version="resumable ? feature.progress.version_id : null" />
  </template>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { posterUrl, scopedArtworkUrl } from '../api.js'
import { recentProgressLabel } from '../recentPlayed.js'
import AppIcon from './AppIcon.vue'
import JzButton from './JzButton.vue'

const props = defineProps({
  kind: { type: String, default: 'movie' },
  scope: { type: Number, default: null },
  enabled: Boolean,
  recentReady: Boolean,
  recentItem: { type: Object, default: null },
  firstItem: { type: Object, default: null },
})
defineEmits(['resume'])
const wide = ref(false)
let media
function onWidth(event) { wide.value = event.matches }
onMounted(() => {
  media = window.matchMedia?.('(min-width: 1024px)')
  if (!media) return
  onWidth(media)
  media.addEventListener?.('change', onWidth)
})
onUnmounted(() => media?.removeEventListener?.('change', onWidth))
const slotOrder = computed(() => wide.value ? ['recent', 'tools'] : ['tools', 'recent'])
const feature = computed(() => wide.value && props.enabled && props.recentReady
  ? props.recentItem || props.firstItem : null)
const resumable = computed(() => !!feature.value && feature.value === props.recentItem)
const detailId = computed(() => feature.value?.show_id || feature.value?.id)
const detailPath = computed(() => (props.kind === 'tv' ? '/tv/' : '/m/') + detailId.value)
const metadata = computed(() => {
  const item = feature.value
  if (!item) return []
  return [item.year, item.subtitle || item.genres?.[0], resumable.value && recentProgressLabel(item)].filter(Boolean)
})
const failedArtwork = reactive(new Set())
watch(() => props.scope, () => failedArtwork.clear(), { flush: 'sync' })
const landscape = computed(() => {
  const item = feature.value
  if (!item) return ''
  const url = props.kind === 'tv' ? posterUrl(item.backdrop_path)
    : item.tmdb_id ? scopedArtworkUrl(`/api/movies/${item.id}/backdrop?tmdb=${item.tmdb_id}`) : ''
  return url && !failedArtwork.has(url) ? url : ''
})
const artwork = computed(() => {
  const url = landscape.value || posterUrl(feature.value?.poster_path)
  return url && !failedArtwork.has(url) ? url : ''
})
const resumeButton = ref(null)
function focusResume() {
  if (!resumeButton.value) return false
  resumeButton.value.focus()
  return true
}
defineExpose({ focusResume })
</script>

<style src="../styles/browseFeature.css"></style>
<style scoped>
.browse-feature {
  --feature-gutter: clamp(20px, 3vw, 56px);
  position: relative;
  isolation: isolate;
  display: flex;
  align-items: flex-end;
  box-sizing: border-box;
  min-height: clamp(305px, 29vw, 470px);
  margin: 0 calc(-1 * var(--feature-gutter));
  padding: clamp(82px, 8vw, 140px) var(--feature-gutter) clamp(38px, 4vw, 70px);
  background: var(--jz-surface);
  color: var(--jz-text);
}
.browse-feature-art { position: absolute; inset: 0; z-index: -2; width: 100%; height: 100%; object-fit: cover; object-position: center 8%; }
.browse-feature-poster { left: auto; right: 12%; width: auto; max-width: 48%; object-fit: contain; object-position: right center; }
.browse-feature::before { content: ''; position: absolute; inset: 0; z-index: -1; pointer-events: none;
  background: linear-gradient(90deg, rgba(9,12,15,.93), rgba(9,12,15,.7) 29%, rgba(9,12,15,.16) 62%, transparent 82%),
    linear-gradient(0deg, var(--jz-bg), transparent 28%); }
.browse-feature-copy { max-width: min(58%, 790px); }
.browse-feature-title { margin: 0; font-size: clamp(2.5rem, 4.5vw, 5rem); line-height: 1.14; font-weight: 700; letter-spacing: -.035em; overflow-wrap: anywhere; }
.browse-feature-meta { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 0; margin: 12px 0 0; font-size: clamp(.875rem, 1.15vw, 1rem); line-height: 1.6; color: var(--jz-text); }
.browse-feature-meta span + span::before { content: '·'; margin: 0 .6em; opacity: .7; }
.browse-feature-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-top: clamp(18px, 2.1vw, 30px); }
.browse-feature-actions :deep(.jz-button), .feature-detail { min-height: 44px; padding: 10px 20px; font-weight: 600; }
.feature-detail { display: inline-flex; gap: 3px; align-items: center; box-sizing: border-box; color: var(--jz-text); text-decoration: none; border-radius: var(--jz-radius-s); }
.feature-detail:hover { text-decoration: underline; }
.feature-detail--primary { background: var(--jz-accent); color: var(--jz-on-accent); }
@media (min-width: 1024px) and (max-height: 800px) {
  .browse-feature {
    min-height: clamp(168px, 22vh, 184px);
    padding-block: 20px 12px;
  }
  .browse-feature--landscape {
    /* The navigation overlays landscape art. Leave its full row above the title. */
    min-height: clamp(208px, 28.5vh, 232px);
    padding-top: 72px;
  }
  .browse-feature--landscape .browse-feature-art { object-position: center 20%; }
  .browse-feature-title { font-size: clamp(2.25rem, 3.2vw, 3rem); }
  .browse-feature-meta { margin-top: 4px; }
  .browse-feature-actions { margin-top: 10px; }
}
</style>
