<template>
  <a class="help-link" :href="page ? `/help/${page}.html` : '/help/'" target="_blank" rel="noopener noreferrer"
    :title="`${label}（在新标签页打开）`" @click.stop><AppIcon v-if="icon" :name="icon" /><span>{{ label }}<AppIcon class="help-new-tab" name="external-link" :size="12" /></span><span class="help-sr">（在新标签页打开）</span></a>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
defineProps({
  page: { type: String, default: '' },
  label: { type: String, default: '图文帮助' },
  icon: { type: String, default: '' }
})
</script>

<style scoped>
.help-link { color: var(--jz-help-color, #9acbff); font-size: var(--jz-help-font-size, .875rem); line-height: var(--jz-help-line-height, 1.7); text-underline-offset: 3px; }
.help-link:hover { text-decoration: underline; }
.help-link:focus-visible { outline: 2px solid currentColor; outline-offset: 4px; border-radius: 2px; }
.help-new-tab { display: inline-block; vertical-align: middle; margin-left: 3px; }
.help-sr { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
