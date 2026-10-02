<script setup>
import { nextTick, onBeforeUnmount, ref } from 'vue'

defineProps({
  src: { type: String, required: true }, alt: { type: String, required: true },
  caption: { type: String, default: '' }, marks: { type: Array, default: () => [] },
})
const dialog = ref(null)
const opener = ref(null)
const expanded = ref(false)
const zoomed = ref(false)
const scrollArea = ref(null)
let previousOverflow = ''

async function show() {
  if (expanded.value) return
  expanded.value = true
  await nextTick()
  if (!dialog.value) return
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  dialog.value.showModal()
}
function close() { dialog.value?.close() }
function closed() {
  document.body.style.overflow = previousOverflow
  expanded.value = false
  zoomed.value = false
  opener.value?.focus()
}
async function toggleZoom() {
  zoomed.value = !zoomed.value
  await nextTick()
  scrollArea.value?.focus({ preventScroll: true })
}
function backdrop(event) { if (event.target === dialog.value) close() }
function position(mark) {
  return { left: `clamp(16px, ${Math.max(0, Math.min(100, Number(mark.x) || 0))}%, calc(100% - 16px))`,
    top: `clamp(16px, ${Math.max(0, Math.min(100, Number(mark.y) || 0))}%, calc(100% - 16px))` }
}
onBeforeUnmount(() => {
  if (expanded.value) { dialog.value?.close(); document.body.style.overflow = previousOverflow }
})
</script>

<template>
  <figure class="doc-figure">
    <button ref="opener" class="doc-figure-open" type="button" :aria-label="`放大图片：${alt}`" @click="show">
      <span class="doc-figure-frame">
        <img :src="src" :alt="alt" loading="lazy" decoding="async" />
        <span v-for="(mark, index) in marks" :key="index" class="doc-figure-mark" :style="position(mark)" aria-hidden="true">{{ mark.label }}</span>
      </span>
      <span class="doc-figure-hint" aria-hidden="true">点击放大</span>
    </button>
    <figcaption v-if="caption">{{ caption }}</figcaption>
    <dialog ref="dialog" class="doc-image-dialog" :aria-label="alt" @close="closed" @click="backdrop">
      <template v-if="expanded">
        <div class="doc-image-toolbar">
          <span>{{ caption || alt }}</span>
          <button type="button" :aria-pressed="zoomed" @click="toggleZoom">{{ zoomed ? '适合窗口' : '放大细节' }}</button>
          <a :href="src" target="_blank" rel="noopener">查看原图 ↗</a>
          <button type="button" autofocus aria-label="关闭图片" @click="close">关闭 ×</button>
        </div>
        <div ref="scrollArea" class="doc-image-scroll" :class="{ 'is-zoomed': zoomed }" tabindex="0" aria-label="图片查看区域，可使用方向键或触摸滚动"><img :src="src" :alt="alt" /></div>
      </template>
    </dialog>
  </figure>
</template>
