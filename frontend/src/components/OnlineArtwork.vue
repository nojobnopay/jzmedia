<template>
  <div ref="root" class="online-poster" :data-online-artwork-state="phase" :aria-busy="phase === 'loading' || phase === 'retry'" :role="image ? undefined : 'img'" :aria-label="image ? undefined : [title, stateText].filter(Boolean).join('，')">
    <img v-if="image" :src="image" :alt="title" decoding="async" referrerpolicy="no-referrer" @load="loaded" @error="decodeFailed" />
    <ArtworkPlaceholder v-else :label="title" />
    <span v-if="!image" class="online-artwork-note" aria-hidden="true">{{ stateText }}</span>
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onlineArtworkPath, onlineArtworkQueue } from '../onlineArtwork.js'
import { refreshViewing, registerViewingPlaybackCleanup, viewing } from '../viewing.js'
import ArtworkPlaceholder from './ArtworkPlaceholder.vue'
const props = defineProps({ path: { type: String, default: '' }, title: { type: String, default: '' } })
const root = ref(null), image = ref(''), phase = ref('waiting')
const path = computed(() => onlineArtworkPath(props.path))
const allowed = computed(() => viewing.ready && !viewing.suspended && !viewing.locallyLocked && viewing.mode !== 'children')
const stateText = computed(() => !path.value ? '暂无海报' : ({ loading: '正在加载海报', retry: '正在重试海报', error: '海报暂时无法加载' }[phase.value] || '等待加载海报'))
let mounted = false, visible = false, paused = false, generation = 0, attempts = 0
let observer = null, task = null, retryTimer = null, priority = 0
function current(turn) { return mounted && generation === turn && allowed.value && !paused }
function stop() {
  generation++
  clearTimeout(retryTimer); retryTimer = null
  task?.cancel(); task = null
  image.value = ''; phase.value = path.value ? 'waiting' : 'missing'
}
async function load() {
  if (!mounted || !visible || !allowed.value || paused || !path.value || task || image.value || attempts >= 2) return
  const turn = ++generation
  attempts++
  phase.value = 'loading'
  let reading
  try {
    reading = onlineArtworkQueue.enqueue(path.value, { priority, onEvict() {
      if (generation !== turn) return
      image.value = ''; task = null; phase.value = 'waiting'; attempts = 0
    } })
    task = reading
    const asset = await reading.promise
    if (!current(turn)) { reading.cancel(); return }
    if (asset) image.value = asset.url
    else { task = null; phase.value = 'waiting' }
  } catch (error) {
    if (!current(turn)) return
    task = null
    if ([401, 403].includes(error?.status)) {
      paused = true
      phase.value = 'error'
      void refreshViewing().catch(() => {})
    } else if (error?.retryable && attempts < 2 && visible) {
      phase.value = 'retry'
      retryTimer = setTimeout(() => { retryTimer = null; void load() }, 400)
    } else phase.value = 'error'
  }
}
function loaded(event) {
  if (image.value && (!event?.target?.src || event.target.src === image.value)) phase.value = 'ready'
}
function decodeFailed(event) {
  if (!image.value || event?.target?.src && event.target.src !== image.value) return
  stop(); phase.value = 'error'; attempts = 2
}
function observe(entries) {
  for (const entry of entries) {
    visible = entry.isIntersecting
    priority = visible ? Math.max(0, entry.boundingClientRect?.top || 0) : Infinity
    task?.setPriority(priority)
    if (visible) void load()
    else if (!image.value) { stop(); attempts = 0 }
  }
}
const stopCleanup = registerViewingPlaybackCleanup(() => { paused = true; stop() })
watch(() => props.path, () => { stop(); attempts = 0; void load() })
watch(() => [viewing.ready, viewing.mode, viewing.generation, viewing.revision, viewing.suspended, viewing.locallyLocked], () => {
  paused = false; stop(); attempts = 0; void load()
}, { flush: 'sync' })
onMounted(() => {
  mounted = true
  if (typeof IntersectionObserver === 'function') {
    observer = new IntersectionObserver(observe, { rootMargin: '0px', threshold: 0 })
    observer.observe(root.value)
  } else { visible = true; void load() }
})
onUnmounted(() => { mounted = false; observer?.disconnect(); stopCleanup(); stop() })
</script>
<style scoped>
.online-poster { position: relative; }
.online-artwork-note { position: absolute; inset: auto 0 0; padding: var(--jz-gap-s); background: var(--jz-surface-2); color: var(--jz-text-faint); font-size: var(--jz-font-s); text-align: center; }
</style>
