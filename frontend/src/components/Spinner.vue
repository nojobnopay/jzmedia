<template>
  <span class="ios-spin" :style="{ width: size + 'px', height: size + 'px' }" aria-hidden="true">
    <i v-for="n in 12" :key="n" :style="{ transform: `rotate(${n * 30}deg) translateY(-${size * 0.36}px)` }"></i>
  </span>
</template>
<script setup>
defineProps({ size: { type: Number, default: 14 } })
</script>
<style scoped>
.ios-spin {
  position: relative;
  display: inline-block;
  vertical-align: -2px;
  flex-shrink: 0;
}
.ios-spin i {
  position: absolute;
  left: calc(50% - 1px);
  top: 50%;
  width: 2px;
  height: 28%;
  border-radius: 2px;
  background: currentColor;
  transform-origin: 1px 0;
  opacity: 0;
  animation: ios-fade 1s linear infinite;
}
.ios-spin i:nth-child(1) { animation-delay: 0s; }
.ios-spin i:nth-child(2) { animation-delay: 0.083s; }
.ios-spin i:nth-child(3) { animation-delay: 0.166s; }
.ios-spin i:nth-child(4) { animation-delay: 0.25s; }
.ios-spin i:nth-child(5) { animation-delay: 0.333s; }
.ios-spin i:nth-child(6) { animation-delay: 0.416s; }
.ios-spin i:nth-child(7) { animation-delay: 0.5s; }
.ios-spin i:nth-child(8) { animation-delay: 0.583s; }
.ios-spin i:nth-child(9) { animation-delay: 0.666s; }
.ios-spin i:nth-child(10) { animation-delay: 0.75s; }
.ios-spin i:nth-child(11) { animation-delay: 0.833s; }
.ios-spin i:nth-child(12) { animation-delay: 0.916s; }
@keyframes ios-fade {
  0% { opacity: 1; }
  100% { opacity: 0.15; }
}
@media (prefers-reduced-motion: reduce) {
  .ios-spin i { animation: none; opacity: 0.6; }
}
</style>
