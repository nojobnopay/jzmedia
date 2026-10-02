<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path v-for="(path, index) in paths[name] || paths.warning" :key="index" :d="path" />
  </svg>
</template>

<script setup>
defineProps({ name: { type: String, required: true }, size: { type: Number, default: 20 } })
const paths = {
  close: ['m6 6 12 12M18 6 6 18'],
  back: ['m10 5-7 7 7 7M3 12h18'],
  forward: ['m14 5 7 7-7 7M21 12H3'],
  refresh: ['M20 10a8 8 0 0 0-14-5L3 8m0-5v5h5', 'M4 14a8 8 0 0 0 14 5l3-3m0 5v-5h-5'],
  search: ['M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0', 'm15 15 6 6'],
  more: ['M5 12h.01M12 12h.01M19 12h.01'],
  folder: ['M3 7V5h6l2 2h10v12H3z'],
  'chevron-down': ['m5 9 7 7 7-7'],
  check: ['m5 12 4 4L19 6'],
  warning: ['m12 3 10 18H2z', 'M12 9v5m0 3v.01'],
}
</script>

<style scoped>
svg { display: block; flex: none; }
</style>
