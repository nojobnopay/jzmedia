<script setup>
import DocFigure from './DocFigure.vue'
defineProps({ src: { type: String, required: true }, alt: { type: String, required: true }, caption: { type: String, default: '' } })
</script>
<template>
  <div class="doc-diagram"><DocFigure :src="src" :alt="alt" :caption="caption" /></div>
</template>
