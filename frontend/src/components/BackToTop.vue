<template>
  <JzButton v-if="visible && !blocked" class="back-to-top" :style="{ bottom: bottom + 'px' }"
    aria-label="回到顶部" title="回到顶部" @click="goTop" type="button"><AppIcon name="arrow-up" /><span>顶部</span></JzButton>
</template>
<script setup>
import AppIcon from './AppIcon.vue'

import JzButton from './JzButton.vue'

import { onMounted, onUnmounted, ref } from 'vue'
const visible = ref(false)
const blocked = ref(false)
const bottom = ref(24)
let observer
let frame = 0
function update() {
  frame = 0
  visible.value = window.scrollY > window.innerHeight
  blocked.value = !!document.fullscreenElement || [...document.querySelectorAll('[aria-modal="true"], .dlg-mask, .auth-mask')]
    .some(el => el.getClientRects().length && window.getComputedStyle(el).visibility !== 'hidden')
  const bar = document.querySelector('.floatbar')
  bottom.value = bar ? Math.max(24, window.innerHeight - bar.getBoundingClientRect().top + 12) : 24
}
function schedule() { if (!frame) frame = requestAnimationFrame(update) }
function goTop() {
  window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
}
onMounted(() => {
  update()
  window.addEventListener('scroll', schedule, { passive: true })
  window.addEventListener('resize', schedule)
  document.addEventListener('fullscreenchange', schedule)
  observer = new window.MutationObserver(schedule)
  observer.observe(document.getElementById('app'), { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'hidden'] })
})
onUnmounted(() => {
  observer?.disconnect()
  cancelAnimationFrame(frame)
  window.removeEventListener('scroll', schedule)
  window.removeEventListener('resize', schedule)
  document.removeEventListener('fullscreenchange', schedule)
})
</script>
<style scoped>
.back-to-top { position: fixed; right: max(16px, env(safe-area-inset-right)); margin-bottom: env(safe-area-inset-bottom); z-index: 35; width: 48px; display: flex; flex-direction: column; align-items: center; justify-content: center; box-shadow: 0 3px 16px #0008; }
.back-to-top span { font-size: 11px; }
.back-to-top:focus-visible { outline: 2px solid var(--jz-link); outline-offset: 3px; }
</style>
