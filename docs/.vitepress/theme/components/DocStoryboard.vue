<script setup>
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import DocLibraryStory from './DocLibraryStory.vue'
import { storyboards } from '../storyboards.mjs'

const props = defineProps({ story: { type: String, required: true } })
const storyboard = computed(() => storyboards[props.story])
const headingTag = computed(() => storyboard.value?.kind === 'casting' ? 'h4' : 'h2')
const id = useId()
const root = ref(null)
const enhanced = ref(false)
const active = ref(0)
const playing = ref(false)
const reducedMotion = ref(false)
const frameCount = computed(() => storyboard.value?.frames.length || 0)
const playbackLabel = computed(() => playing.value ? '暂停演示' : active.value === frameCount.value - 1 ? '重新播放' : '播放演示')
let timer = null
let observer = null
let motionQuery = null
let visible = true

function pause() {
  if (timer !== null) window.clearInterval(timer)
  timer = null
  playing.value = false
}
function select(index) {
  pause()
  active.value = Math.max(0, Math.min(frameCount.value - 1, index))
}
function togglePlayback() {
  if (playing.value) return pause()
  if (reducedMotion.value || !visible || document.hidden || !frameCount.value) return
  if (active.value === frameCount.value - 1) active.value = 0
  playing.value = true
  timer = window.setInterval(() => {
    active.value += 1
    if (active.value === frameCount.value - 1) pause()
  }, 4000)
}
function visibilityChanged() {
  if (document.hidden) pause()
}
function motionChanged() {
  reducedMotion.value = motionQuery.matches
  if (reducedMotion.value) pause()
}
watch(() => props.story, () => {
  pause()
  active.value = 0
})
onMounted(() => {
  enhanced.value = true
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionChanged()
  motionQuery.addEventListener('change', motionChanged)
  document.addEventListener('visibilitychange', visibilityChanged)
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(entries => {
      visible = entries.some(entry => entry.isIntersecting)
      if (!visible) pause()
    })
    observer.observe(root.value)
  }
})
onBeforeUnmount(() => {
  pause()
  observer?.disconnect()
  motionQuery?.removeEventListener('change', motionChanged)
  document.removeEventListener('visibilitychange', visibilityChanged)
})
</script>

<template>
  <section v-if="storyboard" ref="root" class="doc-storyboard" :class="{ 'is-enhanced': enhanced, 'is-casting': storyboard.kind === 'casting' }" :aria-label="storyboard.title">
    <picture v-if="storyboard.illustration" class="doc-casting-picture">
      <source
        media="(max-width: 640px)"
        :srcset="storyboard.illustration.mobileSrc"
        :width="storyboard.illustration.mobileWidth"
        :height="storyboard.illustration.mobileHeight"
      >
      <img
        class="doc-casting-image"
        :src="storyboard.illustration.src"
        :alt="storyboard.illustration.alt"
        :width="storyboard.illustration.width"
        :height="storyboard.illustration.height"
        loading="lazy"
        decoding="async"
      >
    </picture>

    <div v-if="enhanced" class="doc-storyboard-steps" role="group" aria-label="选择演示步骤">
      <button
        v-for="(frame, index) in storyboard.frames"
        :key="index"
        type="button"
        :aria-pressed="active === index"
        :aria-controls="`${id}-step-${index + 1}`"
        @click="select(index)"
      >
        {{ frame.label }}
      </button>
    </div>

    <div class="doc-storyboard-frames" @pointerdown.capture="pause" @keydown.capture="pause" @focusin.capture="pause">
      <section
        v-for="(frame, index) in storyboard.frames"
        v-show="!enhanced || active === index"
        :id="`${id}-step-${index + 1}`"
        :key="index"
        class="doc-storyboard-frame"
        :data-frame="index + 1"
        :aria-labelledby="`${id}-heading-${index + 1}`"
      >
        <component :is="headingTag" :id="`${id}-heading-${index + 1}`" class="doc-storyboard-heading">{{ frame.heading }}</component>
        <p v-if="frame.summary && frame.summary !== frame.heading" class="doc-storyboard-summary">{{ frame.summary }}</p>
        <DocLibraryStory v-if="storyboard.kind === 'binding' || storyboard.kind === 'tree'" :kind="storyboard.kind" :frame="frame" />
        <dl v-if="frame.participants?.length" class="doc-casting-participants">
          <div v-for="participant in frame.participants" :key="participant.name" class="doc-casting-participant">
            <dt>{{ participant.name }}</dt>
            <dd>{{ participant.action }}<span v-if="participant.time" class="doc-casting-time">{{ participant.time }}</span></dd>
          </div>
        </dl>
        <p v-if="frame.detail" class="doc-storyboard-detail">{{ frame.detail }}</p>
      </section>
    </div>

    <div v-if="enhanced" class="doc-storyboard-controls">
      <button
        class="doc-storyboard-play"
        type="button"
        :disabled="reducedMotion"
        :aria-pressed="playing"
        @click="togglePlayback"
      >
        {{ playbackLabel }}
      </button>
      <span v-if="reducedMotion" class="doc-storyboard-motion-note">可选择步骤逐一查看</span>
      <p class="doc-storyboard-status" role="status" :aria-live="playing ? 'off' : 'polite'" aria-atomic="true">
        第 {{ active + 1 }} 步：{{ storyboard.frames[active]?.heading }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.doc-storyboard { margin: 32px 0 40px; color: var(--vp-c-text-1); }
.doc-casting-picture { display: block; }
.doc-casting-image { display: block; width: 100%; height: auto; border-radius: 0; }
.doc-storyboard-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 8px 0 28px; border-bottom: 1px solid var(--vp-c-divider); }
.doc-storyboard button { min-height: 44px; color: inherit; font: inherit; cursor: pointer; }
.doc-storyboard button:focus-visible { outline: 3px solid var(--vp-c-brand-1); outline-offset: 4px; border-radius: 2px; }
.doc-storyboard-steps button { position: relative; padding: 12px 6px; border: 0; background: none; color: var(--vp-c-text-2); font-size: 15px; line-height: 1.5; text-align: center; }
.doc-storyboard-steps button::after { position: absolute; inset: auto 12px -1px; height: 2px; background: transparent; content: ''; }
.doc-storyboard-steps button[aria-pressed="true"] { color: var(--vp-c-text-1); font-weight: 600; }
.doc-storyboard-steps button[aria-pressed="true"]::after { background: var(--vp-c-brand-1); }
.doc-storyboard-steps button:hover { color: var(--vp-c-brand-1); }
.doc-storyboard-frame { min-width: 0; margin-top: 32px; }
.is-enhanced .doc-storyboard-frame { margin-top: 0; }
.doc-storyboard-heading { margin: 0 0 10px; border: 0; padding: 0; color: var(--vp-c-text-1); font-size: 24px; font-weight: 650; line-height: 1.45; letter-spacing: -.015em; overflow-wrap: anywhere; }
.doc-storyboard-summary { max-width: 42em; margin: 0; color: var(--vp-c-text-2); font-size: 16px; line-height: 1.8; }
.doc-casting-participants { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; margin: 24px 0; }
.doc-casting-participant { min-width: 0; }
.doc-casting-participant dt { margin: 0 0 5px; color: var(--vp-c-text-2); font-size: 13px; line-height: 1.6; }
.doc-casting-participant dd { margin: 0; color: var(--vp-c-text-1); font-size: 16px; line-height: 1.7; overflow-wrap: anywhere; }
.doc-casting-time { display: inline-block; margin-inline-start: .35em; font-variant-numeric: tabular-nums; white-space: nowrap; }
.doc-storyboard-detail { max-width: 46em; margin: 0; color: var(--vp-c-text-2); font-size: 14px; line-height: 1.8; }
.doc-storyboard-controls { display: flex; align-items: center; gap: 12px; margin-top: 16px; }
.doc-storyboard .doc-storyboard-play { display: inline-flex; align-items: center; padding: 8px 4px 8px 0; border: 0; background: none; font-size: 14px; }
.doc-storyboard-play:hover:enabled { color: var(--vp-c-brand-1); }
.doc-storyboard-play:disabled { color: var(--vp-c-text-2); cursor: default; opacity: .65; }
.doc-storyboard-motion-note { color: var(--vp-c-text-2); font-size: 13px; }
.doc-storyboard-status { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
@media (max-width: 640px) {
  .doc-storyboard-steps { margin: 4px 0 24px; }
  .doc-storyboard-steps button { padding-inline: 4px; font-size: 14px; }
  .doc-storyboard-steps button::after { inset-inline: 8px; }
  .doc-storyboard-heading { font-size: 22px; }
  .doc-storyboard-summary { font-size: 15px; }
  .doc-casting-participants { grid-template-columns: minmax(0, 1fr); gap: 12px; margin: 22px 0; }
  .doc-casting-participant { display: grid; grid-template-columns: 4.5em minmax(0, 1fr); column-gap: 12px; align-items: baseline; }
  .doc-casting-participant dt { margin: 0; font-size: 13px; }
  .doc-casting-participant dd { font-size: 15px; }
}
</style>
