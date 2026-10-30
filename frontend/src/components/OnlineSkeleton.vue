<template>
  <section class="online-skeleton" :class="'online-skeleton--' + variant" role="status" aria-live="polite" aria-busy="true">
    <p class="online-skeleton-status">{{ label }}</p>
    <div v-if="variant === 'detail'" class="online-skeleton-detail" aria-hidden="true">
      <span class="online-skeleton-shape online-skeleton-poster" />
      <div class="online-skeleton-summary">
        <span class="online-skeleton-shape online-skeleton-line short" />
        <span class="online-skeleton-shape online-skeleton-title" />
        <span class="online-skeleton-shape online-skeleton-line" />
        <span class="online-skeleton-shape online-skeleton-line short" />
        <span class="online-skeleton-shape online-skeleton-action" />
      </div>
      <div class="online-skeleton-episodes"><span v-for="index in 12" :key="index" class="online-skeleton-shape" /></div>
    </div>
    <div v-else class="online-skeleton-items" aria-hidden="true">
      <div v-for="index in count" :key="index" class="online-skeleton-item">
        <span class="online-skeleton-shape online-skeleton-poster" />
        <div class="online-skeleton-summary">
          <span class="online-skeleton-shape online-skeleton-line" />
          <span class="online-skeleton-shape online-skeleton-line short" />
        </div>
      </div>
    </div>
    <slot />
  </section>
</template>

<script setup>
defineProps({
  variant: { type: String, default: 'catalogue', validator: value => ['catalogue', 'detail', 'records', 'continue'].includes(value) },
  count: { type: Number, default: 6 },
  label: { type: String, default: '正在加载内容…' },
})
</script>

<style scoped>
.online-skeleton { min-width: 0; margin-block: var(--jz-gap-l); }
.online-skeleton-status { margin: 0 0 var(--jz-gap-m); color: var(--jz-text-dim); font-size: var(--jz-font-m); }
.online-skeleton-items { display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--poster-min, 150px), 1fr)); gap: var(--jz-gap-l); }
.online-skeleton-item, .online-skeleton-summary { min-width: 0; }
.online-skeleton-shape { display: block; border-radius: var(--jz-radius-s); background: var(--jz-surface-3); animation: online-skeleton-pulse 1.8s ease-in-out .2s infinite alternate; }
.online-skeleton-poster { aspect-ratio: 2 / 3; border-radius: var(--jz-radius-m); }
.online-skeleton-line { height: 12px; margin-top: var(--jz-gap-m); width: 84%; }
.online-skeleton-line.short { width: 55%; }
.online-skeleton-title { height: 30px; margin-block: var(--jz-gap-xl); width: 88%; }
.online-skeleton-action { margin-top: var(--jz-gap-xl); width: 180px; height: var(--jz-control-current); }
.online-skeleton-detail { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: var(--jz-gap-xl); }
.online-skeleton-detail > .online-skeleton-summary { max-width: 650px; }
.online-skeleton-episodes { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: var(--jz-gap-s); margin-top: var(--jz-gap-l); }
.online-skeleton-episodes > span { height: 44px; }
.online-skeleton--records .online-skeleton-items { grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr)); }
.online-skeleton--records .online-skeleton-item, .online-skeleton--continue .online-skeleton-item { display: grid; grid-template-columns: 64px minmax(0, 1fr); align-items: start; gap: var(--jz-gap-m); padding: var(--jz-gap-s); border-radius: var(--jz-radius-m); background: var(--jz-surface); }
.online-skeleton--continue .online-skeleton-items { display: grid; grid-auto-flow: column; grid-template-columns: none; grid-auto-columns: 280px; overflow: hidden; }
.online-skeleton--continue .online-skeleton-item { height: 112px; box-sizing: border-box; }
@keyframes online-skeleton-pulse { from { opacity: .55; } to { opacity: 1; } }
@media (max-width: 700px) {
  .online-skeleton-items { grid-template-columns: repeat(auto-fill, minmax(min(var(--jz-mobile-poster-min), 100%), 1fr)); gap: var(--jz-gap-m); }
  .online-skeleton-detail { grid-template-columns: 96px minmax(0, 1fr); gap: var(--jz-gap-m); }
  .online-skeleton-title { height: 24px; margin-block: var(--jz-gap-l); }
  .online-skeleton-action { width: min(160px, 100%); }
  .online-skeleton-episodes { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .online-skeleton--continue .online-skeleton-items { grid-auto-columns: 260px; }
}
@media (prefers-reduced-motion: reduce) { .online-skeleton-shape { animation: none; } }
</style>
