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
  filter: ['M3 6h18M5 12h14M8 18h8', 'M8 3v6M16 9v6M11 15v6'],
  home: ['M3 10.5 12 3l9 7.5', 'M5 9v12h5v-7h4v7h5V9'],
  movie: ['M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z', 'M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4M7 12h10'],
  tv: ['M4 7h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z', 'm8 3 4 4 4-4'],
  collections: ['M7 3h10M5 6h14', 'M5 9h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z'],
  library: ['M3 4h4v17H3ZM10 4h4v17h-4Z', 'm16 5 4-1 3 16-4 1Z'],
  settings: ['m9 3-.6 2.4-1.5.9-2.4-.7-2.1 3.6 1.8 1.7v1.8l-1.8 1.7 2.1 3.6 2.4-.7 1.5.9L9 21h6l.6-2.4 1.5-.9 2.4.7 2.1-3.6-1.8-1.7v-1.8l1.8-1.7-2.1-3.6-2.4.7-1.5-.9L15 3Z', 'M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0'],
  help: ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', 'M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2-3 4M12 17h.01'],
  more: ['M5 12h.01M12 12h.01M19 12h.01'],
  folder: ['M3 7V5h6l2 2h10v12H3z'],
  file: ['M5 3h9l5 5v13H5z', 'M14 3v6h5M8 13h8M8 17h6'],
  'chevron-down': ['m5 9 7 7 7-7'],
  check: ['m5 12 4 4L19 6'],
  warning: ['m12 3 10 18H2z', 'M12 9v5m0 3v.01'],
}
</script>

<style scoped>
svg { display: block; flex: none; }
</style>
