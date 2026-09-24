<template>
  <div class="jz-empty" role="status">
    <p class="jz-empty-text">{{ text }}</p>
    <div v-if="$slots.default" class="jz-empty-actions">
      <slot />
    </div>
  </div>
</template>
<script setup>
// 全站空态/加载/错误单源（P0）：页面不再各写 .bar/.empty-guide/.msg 三套文案样式。
defineProps({
  text: { type: String, default: '加载中…' },
})
</script>
<style scoped>
.jz-empty { padding: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.jz-empty-text { margin: 0; color: #aaa; font-size: 0.875rem; }
.jz-empty-actions { display: flex; gap: 8px; align-items: center; }
</style>
