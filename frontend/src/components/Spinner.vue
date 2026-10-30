<template>
  <span class="jz-spinner" :style="{ '--jz-spinner-size': size + 'px' }" aria-hidden="true"><AppIcon class="jz-spinner-arc" name="loading" :size="size" /></span>
</template>
<script setup>
import AppIcon from './AppIcon.vue'
defineProps({ size: { type: Number, default: 16 } })
</script>
<style scoped>
.jz-spinner { position: relative; display: inline-flex; flex: none; vertical-align: middle;
  width: var(--jz-spinner-size); height: var(--jz-spinner-size); opacity: 0;
  animation: jz-spinner-reveal var(--jz-motion-fast, 140ms) ease-out .2s forwards; }
/* Match the shared 24-unit loading asset: radius 9 and a 1.7-unit stroke. */
.jz-spinner::before { content: ''; position: absolute; inset: 8.958333%; box-sizing: border-box;
  border: calc(var(--jz-spinner-size) * 1.7 / 24) solid currentColor; border-radius: 50%; opacity: .18; }
.jz-spinner-arc { animation: jz-spin .9s linear .2s infinite; }
@keyframes jz-spinner-reveal { to { opacity: 1; } }
@keyframes jz-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .jz-spinner { animation: none; opacity: 1; }
  .jz-spinner-arc { animation: none; }
}
</style>
