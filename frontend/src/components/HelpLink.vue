<template>
  <a class="help-link" :href="page ? `/help/${page}.html` : '/help/'" target="_blank" rel="noopener noreferrer"
    :title="`${label}（在新标签页打开）`" @click.stop>{{ label }}<span class="help-new-tab" aria-hidden="true"> ↗</span><span class="help-sr">（在新标签页打开）</span></a>
</template>

<script setup>
defineProps({
  page: { type: String, default: '' },
  label: { type: String, default: '图文帮助' }
})
</script>

<style scoped>
.help-link { color: #9acbff; font-size: .875rem; line-height: 1.7; text-underline-offset: 3px; }
.help-link:hover { text-decoration: underline; }
.help-link:focus-visible { outline: 2px solid currentColor; outline-offset: 4px; border-radius: 2px; }
.help-new-tab { font-size: .75em; }
.help-sr { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
