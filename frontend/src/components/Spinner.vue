<template>
  <span class="jz-spinner" aria-hidden="true"><AppIcon name="loading" :size="size" /></span>
</template>
<script setup>
import AppIcon from './AppIcon.vue'
defineProps({ size: { type: Number, default: 16 } })
</script>
<style scoped>
.jz-spinner { display: inline-flex; flex: none; vertical-align: middle; animation: jz-spin .9s linear infinite; }
@keyframes jz-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .jz-spinner { animation: none; } }
</style>
