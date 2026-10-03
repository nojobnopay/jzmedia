<template>
  <div class="artwork-placeholder" aria-hidden="true">
    <AppIcon :name="kind === 'person' ? 'person' : kind === 'collection' ? 'collections' : 'image'" :size="32" />
    <span>{{ label || (kind === 'person' ? '暂无头像' : '暂无图片') }}</span>
  </div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'
defineProps({ label: { type: String, default: '' }, kind: { type: String, default: 'poster' } })
</script>
<style scoped>
.artwork-placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: var(--jz-gap-s); box-sizing: border-box; overflow: hidden; padding: var(--jz-gap-s); background: var(--jz-surface-3); color: var(--jz-text-faint); text-align: center; }
.artwork-placeholder span { max-width: 100%; font-family: inherit; font-size: var(--jz-font-s); font-weight: 500; line-height: 1.4; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.artwork-placeholder :deep(svg) { max-width: 40%; flex: none; }
</style>
