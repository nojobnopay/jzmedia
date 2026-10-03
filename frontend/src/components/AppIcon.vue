<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path v-for="(path, index) in resolveIcon(name)" :key="index" :d="path.d"
      :fill="path.fill" :stroke="path.stroke" :fill-rule="path.fillRule" />
  </svg>
</template>

<script setup>
import { resolveIcon } from '../generated/design-icons.js'
defineProps({ name: { type: String, required: true }, size: { type: Number, default: 20 } })
</script>

<style scoped>
svg { display: inline-block; vertical-align: middle; flex: none; }
</style>
