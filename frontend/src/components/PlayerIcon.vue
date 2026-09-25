<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path v-if="name === 'play'" d="M8 5.5v13l10-6.5z" fill="currentColor" stroke="none" />
    <g v-else :transform="name === 'forward' ? 'translate(24 0) scale(-1 1)' : undefined">
      <path v-for="(d, i) in paths[name] || paths.info" :key="i" :d="d" />
    </g>
    <text v-if="name === 'rewind' || name === 'forward'" x="12" y="15.8" text-anchor="middle"
      fill="currentColor" stroke="none" font-size="8" font-weight="600" font-family="system-ui, sans-serif">10</text>
  </svg>
</template>

<script setup>
defineProps({ name: { type: String, required: true }, size: { type: Number, default: 22 } })
const paths = {
  pause: ['M8 5v14M16 5v14'],
  close: ['m6 6 12 12M18 6 6 18'],
  volume: ['M11 4 6 8H3v8h3l5 4z', 'M15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14'],
  mute: ['M11 4 6 8H3v8h3l5 4z', 'm16 9 6 6m0-6-6 6'],
  settings: ['M9.5 3h5l.6 2.3 2 1.2 2.4-.6 2.5 4.2-1.8 1.7v.4l1.8 1.7-2.5 4.2-2.4-.6-2 1.2-.6 2.3h-5l-.6-2.3-2-1.2-2.4.6L2 13.9l1.8-1.7v-.4L2 10.1l2.5-4.2 2.4.6 2-1.2z', 'M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0'],
  fullscreen: ['M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5'],
  exitFullscreen: ['M3 8h5V3m13 5h-5V3M8 21v-5H3m13 5v-5h5'],
  rewind: ['M3 8a9 9 0 1 1-.3 7', 'M3 3v5h5'],
  forward: ['M3 8a9 9 0 1 1-.3 7', 'M3 3v5h5'],
  chevron: ['m8 5 7 7-7 7'],
  check: ['m5 12 4 4L19 6'],
  external: ['M14 3h7v7m0-7L10 14', 'M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5'],
  info: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', 'M12 11v6m0-10v.1'],
  subtitles: ['M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1', 'M6 11h3m3 0h6M6 15h6m3 0h3'],
}
</script>

<style scoped>
svg { flex: none; display: block; }
</style>
