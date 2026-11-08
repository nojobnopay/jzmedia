<script setup>
import { nextTick, onBeforeUnmount, ref } from 'vue'

const props = defineProps({
  src: { type: String, required: true }, alt: { type: String, required: true },
  mobileSrc: { type: String, default: '' }, mobileMarks: { type: Array, default: () => [] },
  phone: { type: Boolean, default: false },
  caption: { type: String, default: '' }, marks: { type: Array, default: () => [] },
})
const pictureImage = ref(null)
const openedSrc = ref('')
const dialog = ref(null)
const opener = ref(null)
const expanded = ref(false)
const zoomed = ref(false)
const scrollArea = ref(null)
let previousOverflow = ''

async function show() {
  if (expanded.value) return
  openedSrc.value = pictureImage.value?.currentSrc ||
    (props.mobileSrc && window.matchMedia('(max-width: 640px)').matches ? props.mobileSrc : props.src)
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
  <figure class="doc-figure" :class="{ 'doc-figure-phone': phone, 'doc-figure-responsive': mobileSrc }">
    <button ref="opener" class="doc-figure-open" type="button" :aria-label="`放大图片：${alt}`" @click="show">
      <span class="doc-figure-frame" :class="{ 'has-mobile-image': mobileSrc }">
        <picture>
          <source v-if="mobileSrc" media="(max-width: 640px)" :srcset="mobileSrc" />
          <img ref="pictureImage" :src="src" :alt="alt" loading="lazy" decoding="async" />
        </picture>
        <span v-for="(mark, index) in marks" :key="`desktop-${index}`" class="doc-figure-mark doc-figure-mark-desktop" :style="position(mark)" aria-hidden="true">{{ mark.label }}</span>
        <span v-for="(mark, index) in mobileSrc ? mobileMarks : []" :key="`mobile-${index}`" class="doc-figure-mark doc-figure-mark-mobile" :style="position(mark)" aria-hidden="true">{{ mark.label }}</span>
      </span>
      <span class="doc-figure-hint" aria-hidden="true">点击放大</span>
    </button>
    <figcaption v-if="caption">{{ caption }}</figcaption>
    <dialog ref="dialog" class="doc-image-dialog" :aria-label="alt" @close="closed" @click="backdrop">
      <template v-if="expanded">
        <div class="doc-image-toolbar">
          <span>{{ caption || alt }}</span>
          <button type="button" :aria-pressed="zoomed" @click="toggleZoom">{{ zoomed ? '适合窗口' : '放大细节' }}</button>
          <a :href="openedSrc" target="_blank" rel="noopener">查看原图 ↗</a>
          <button type="button" autofocus aria-label="关闭图片" @click="close">关闭 ×</button>
        </div>
        <div ref="scrollArea" class="doc-image-scroll" :class="{ 'is-zoomed': zoomed }" tabindex="0" aria-label="图片查看区域，可使用方向键或触摸滚动"><img :src="openedSrc" :alt="alt" /></div>
      </template>
    </dialog>
  </figure>
</template>

<style scoped>
.doc-figure-frame picture { display: block; }
.doc-figure-mark-mobile { display: none; }
.doc-figure-phone .doc-figure-open { max-width: 320px; margin-inline: auto; }
@media (max-width: 640px) {
  .doc-figure-responsive .doc-figure-open { max-width: 320px; margin-inline: auto; }
  .has-mobile-image .doc-figure-mark-desktop { display: none; }
  .has-mobile-image .doc-figure-mark-mobile { display: grid; }
}
</style>
