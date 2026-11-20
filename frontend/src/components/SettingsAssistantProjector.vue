<template>
  <span class="projector-character" aria-hidden="true" :data-pose="phase" :data-paused="paused || undefined">
    <picture>
      <source media="(prefers-reduced-motion: reduce)" :srcset="stillArtwork" />
      <img :src="artwork" alt="" draggable="false" />
    </picture>
    <template v-if="phase === 'thinking'">
      <span class="projector-reel projector-reel--left"></span>
      <span class="projector-reel projector-reel--right"></span>
    </template>
    <span v-if="phase === 'reply'" class="projector-lens"></span>
    <span v-else-if="phase === 'smile'" class="projector-smile"></span>
    <template v-else-if="phase === 'cry'">
      <span class="projector-tear projector-tear--left"></span>
      <span class="projector-tear projector-tear--right"></span>
    </template>
    <span v-else-if="phase === 'wave' || phase === 'ambient-wave'" class="projector-wave"></span>
    <template v-else-if="phase === 'ambient-frown'">
      <span class="projector-brows"></span>
      <span class="projector-pursed-mouth"></span>
    </template>
    <span v-else-if="phase === 'ambient-sleep'" class="projector-lids"></span>
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { projectorArtwork } from '../assistantArtwork.js'

const props = defineProps({ phase: { type: String, default: 'still' }, paused: Boolean })
const stillArtwork = computed(() => {
  if (props.phase === 'cry') return projectorArtwork.drag
  if (props.phase === 'smile' || props.phase === 'dance' || props.phase === 'wave') return projectorArtwork.talk
  return projectorArtwork.idle
})
const artwork = computed(() => {
  if (props.phase === 'drag') return projectorArtwork.drag
  if (props.phase === 'reply' || props.phase === 'ambient-wave') return projectorArtwork.talk
  return stillArtwork.value
})
</script>

<style scoped>
.projector-character { display: block; position: relative; pointer-events: none; transform-origin: 50% 90%; }
.projector-character picture, .projector-character img { display: block; width: 100%; height: 100%; }
.projector-character img { object-fit: contain; }
.projector-reel, .projector-lens { display: none; }
@media (prefers-reduced-motion: no-preference) {
  .projector-character[data-pose="idle"] { animation: projector-breathe 7s ease-in-out infinite; }
  .projector-character[data-pose="drag"] { transform: translateY(-1px) rotate(var(--projector-lean)); transition: transform 90ms ease-out; }
  .projector-character[data-pose="settle"] { animation: projector-settle 320ms ease-out both; }
  .projector-character[data-pose="reply"] { animation: projector-nod 480ms ease-in-out; }
  /* Rotate just the printed faces inside the stationary rims of the 256px master. */
  .projector-reel { display: block; position: absolute; inset: 0;
    background: url('../assets/assistant/projector-idle.webp') center / contain no-repeat;
    clip-path: circle(16% at var(--reel-center)); transform-origin: var(--reel-center);
    animation: projector-wind 2.4s linear infinite; }
  .projector-reel--left { --reel-center: 23.8% 26.5%; }
  .projector-reel--right { --reel-center: 68.6% 26.5%; animation-duration: 2s; }
  .projector-lens { display: block; position: absolute; left: 74.5%; top: 49%; width: 24%; height: 31%;
    background: radial-gradient(ellipse, #fff7d9 0%, rgb(255 220 143 / 85%) 24%, rgb(255 192 82 / 45%) 45%, transparent 72%);
    opacity: 0; animation: projector-light 900ms ease-out; }
  .projector-character[data-paused], .projector-character[data-paused] * { animation-play-state: paused; }
  @keyframes projector-breathe {
    0%, 24%, 100% { transform: none; }
    12% { transform: translateY(-1px) scale(1.012, 1.025); }
  }
  @keyframes projector-settle {
    from { transform: translateY(-1px) rotate(var(--projector-lean)); }
    48% { transform: rotate(calc(var(--projector-lean) * -.35)) scale(1.035, .96); }
    to { transform: none; }
  }
  @keyframes projector-wind { to { transform: rotate(1turn); } }
  @keyframes projector-nod { 40% { transform: translateY(1px) rotate(3deg) scaleY(.97); } }
  @keyframes projector-light {
    0% { opacity: 0; transform: scale(.75); }
    25% { opacity: .95; }
    100% { opacity: 0; transform: scale(1.28); }
  }
}
/* The masters share a face. Lay the smiling eyes over the open grin and warm lens. */
.projector-smile { position: absolute; inset: 0; background: url('../assets/assistant/projector-drag.webp') center / contain no-repeat;
  mask-image: radial-gradient(ellipse 8% 6.5% at 26% 66.5%, #000 93%, transparent), radial-gradient(ellipse 8% 6.5% at 49.5% 66.5%, #000 93%, transparent); }
.projector-tear { position: absolute; top: 69%; width: 7.5%; height: 12%; border-radius: 65% 18% 65% 55%; background: linear-gradient(145deg, #e2f7ff 4%, #70beed 58%, #3790c8); box-shadow: 0 0 0 .5px #357da0; transform: rotate(-28deg); transform-origin: top; }
.projector-tear--left { left: 19%; }
.projector-tear--right { left: 51%; }
.projector-wave { position: absolute; left: 91%; top: 42%; width: 12%; height: 19%; border-right: 2px solid #f4ca82; border-radius: 50%; transform: rotate(-20deg); }
.projector-wave::after { content: ""; position: absolute; left: 48%; top: -27%; width: 100%; height: 140%; border-right: 1px solid #ffdfaa; border-radius: 50%; }
@media (prefers-reduced-motion: no-preference) {
  [data-pose="wave"] { animation: projector-hello 850ms ease-in-out; }
  [data-pose="ambient-wave"] { animation: projector-greet 1.2s ease-in-out; }
  [data-pose="ambient-wave"] .projector-wave { animation: projector-gesture 1.2s ease both; }
  [data-pose="ambient-frown"] { animation: projector-ponder 1.6s ease-in-out; }
  .projector-brows, .projector-pursed-mouth, .projector-lids { position: absolute; inset: 0; }
  .projector-brows { animation: projector-knit 1.6s ease-in-out both; }
  .projector-brows::before, .projector-brows::after {
    content: ""; position: absolute; top: 60%; width: 9.5%; height: 2.8%;
    background: #491515; border-radius: 60%; transform: rotate(19deg);
  }
  .projector-brows::before { left: 21%; }
  .projector-brows::after { left: 45%; transform: rotate(-19deg); }
  .projector-pursed-mouth, .projector-lids {
    background: url('../assets/assistant/projector-drag.webp') center / contain no-repeat;
  }
  .projector-pursed-mouth { mask-image: radial-gradient(ellipse 7.5% 3.8% at 38% 72%, #000 90%, transparent); }
  /* The drag master has two arched lids. Reflect the eye strip around its own center for closed, relaxed eyes. */
  .projector-lids {
    mask-image: radial-gradient(ellipse 8.5% 6% at 26% 66.5%, #000 90%, transparent),
      radial-gradient(ellipse 8.5% 6% at 49.5% 66.5%, #000 90%, transparent);
    transform-origin: 50% 66.5%; transform: scaleY(-1);
  }
  [data-pose="ambient-sleep"] { animation: projector-doze 1.8s ease-in-out both; }
  [data-pose="smile"] { animation: projector-laugh 450ms ease-in-out 2; }
  [data-pose="cry"] { animation: projector-sniff 850ms ease-in-out 2; }
  .projector-tear { animation: projector-teardrop 810ms ease-in 2 both; }
  .projector-tear--right { animation-delay: 160ms; }
  [data-pose="dance"] { animation: projector-dance 840ms ease-in-out 2; }
  @keyframes projector-hello {
    23%, 68% { transform: rotate(-10deg); } 45% { transform: rotate(6deg); }
  }
  @keyframes projector-greet {
    25%, 65% { transform: rotate(-6deg); }
    45% { transform: rotate(3deg); }
  }
  @keyframes projector-gesture { 0%, 100% { opacity: 0; } 25%, 70% { opacity: 1; } }
  @keyframes projector-ponder { 30%, 68% { transform: rotate(-4deg); } }
  @keyframes projector-knit {
    0%, 100% { opacity: 0; transform: translateY(-2%); }
    25%, 75% { opacity: 1; transform: translateY(1%); }
  }
  @keyframes projector-doze {
    0% { transform: none; }
    35% { transform: rotate(3deg); }
    60% { transform: rotate(1deg); }
    100% { transform: translateY(2%) rotate(7deg) scaleY(.96); }
  }
  @keyframes projector-laugh {
    35% { transform: translateY(-3%) scale(.98, 1.04); } 70% { transform: scale(1.04, .95); }
  }
  @keyframes projector-sniff {
    28% { transform: rotate(-3deg) scaleY(.98); } 62% { transform: rotate(2deg); }
  }
  @keyframes projector-teardrop {
    0% { opacity: 0; transform: rotate(-28deg) scale(.45); }
    28% { opacity: 1; }
    72% { opacity: 1; transform: translateY(65%) rotate(-28deg); }
    100% { opacity: 0; transform: translateY(100%) rotate(-28deg); }
  }
  @keyframes projector-dance {
    14% { transform: translate(-6%, -9%) rotate(-13deg); }
    30% { transform: translateX(-7%) rotate(-6deg) scale(1.05, .94); }
    48% { transform: translateY(-13%) scale(.98, 1.03); }
    67% { transform: translate(7%, -6%) rotate(12deg); }
    82% { transform: translateX(5%) rotate(5deg) scale(1.04, .96); }
  }
}
@media (prefers-reduced-motion: reduce) {
  [data-pose="ambient-wave"] .projector-wave { display: none; }
}
</style>
