<template>
  <JzButton v-if="visible && !blocked" ref="button" class="back-to-top" :style="{ bottom: bottom + 'px' }"
    aria-label="回到顶部" title="回到顶部" @click="goTop" type="button"><AppIcon name="arrow-up" /><span>顶部</span></JzButton>
</template>
<script setup>
import AppIcon from './AppIcon.vue'

import JzButton from './JzButton.vue'

import { onMounted, onUnmounted, ref } from 'vue'
const visible = ref(false)
const blocked = ref(false)
const bottom = ref(24)
const button = ref(null)
let observer
let frame = 0
function update() {
  frame = 0
  visible.value = window.scrollY > window.innerHeight
  blocked.value = !!document.fullscreenElement || [...document.querySelectorAll('[aria-modal="true"], .dlg-mask, .auth-mask')]
    .some(el => el.getClientRects().length && window.getComputedStyle(el).visibility !== 'hidden')
  const buttonHeight = button.value?.el?.getBoundingClientRect().height || 80
  const safeMargin = button.value?.el ? parseFloat(window.getComputedStyle(button.value.el).marginBottom) || 0 : 0
  const defaultTop = window.innerHeight - 24 - safeMargin - buttonHeight
  const bars = [...document.querySelectorAll('.floatbar, .settings-form-actions')].filter(el => {
    const rect = el.getBoundingClientRect()
    // A sticky form footer scrolling out through the top is not a bottom
    // obstruction. Only move above bars intersecting the default button area.
    return rect.height && rect.top < window.innerHeight && rect.bottom > defaultTop && ['fixed', 'sticky'].includes(window.getComputedStyle(el).position)
  })
  const desiredBottom = Math.max(24, ...bars.map(bar => window.innerHeight - bar.getBoundingClientRect().top + 12))
  bottom.value = Math.min(desiredBottom, Math.max(0, window.innerHeight - buttonHeight - safeMargin - 12))
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
