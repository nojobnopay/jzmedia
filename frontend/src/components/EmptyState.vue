<template>
  <div :class="['jz-empty', 'jz-empty--' + state]" :role="state === 'error' ? 'alert' : 'status'"
    :aria-busy="state === 'loading'">
    <Spinner v-if="state === 'loading'" :size="24" />
    <div class="jz-empty-body">
      <h2 v-if="title" class="jz-empty-title">{{ title }}</h2>
      <p v-if="text" class="jz-empty-text">{{ text }}</p>
      <div v-if="retry || $slots.default" class="jz-empty-actions">
        <JzButton v-if="retry" @click="$emit('retry')">重新加载</JzButton>
        <slot />
      </div>
    </div>
  </div>
</template>
<script setup>
import Spinner from './Spinner.vue'
import JzButton from './JzButton.vue'

// text + default slot remain compatible; explicit states share loading/error UI.
defineProps({
  text: { type: String, default: '加载中…' },
  title: { type: String, default: '' },
  state: { type: String, default: 'plain' },
  retry: { type: Boolean, default: false },
})
defineEmits(['retry'])
</script>
<style scoped>
.jz-empty { margin-block: var(--jz-gap-l); padding: var(--jz-gap-xl); display: flex; gap: var(--jz-gap-m); align-items: flex-start; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-l); background: var(--jz-surface); }
.jz-empty-body { min-width: 0; }
.jz-empty-title { margin: 0 0 var(--jz-gap-xs); color: var(--jz-text); font-size: var(--jz-font-xl); }
.jz-empty-text { margin: 0; color: var(--jz-text-dim); font-size: var(--jz-font-m); line-height: 1.6; overflow-wrap: anywhere; }
.jz-empty--error { border-color: var(--jz-danger); }
.jz-empty-actions { display: flex; flex-wrap: wrap; gap: var(--jz-gap-s); align-items: center; margin-top: var(--jz-gap-m); }
.jz-empty-actions :deep(a) { color: var(--jz-link); }
</style>
