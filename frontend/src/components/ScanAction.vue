<template>
  <div class="scan-action">
    <label v-if="candidates.length">扫描目标 <select v-model.number="selected" :disabled="scan.blocked.value">
      <option v-for="l in candidates" :key="l.id" :value="l.id">{{ l.media_name ? l.media_name + ' · ' : '' }}{{ l.name }}</option>
    </select></label>
    <JzButton v-if="target" :disabled="scan.blocked.value" @click="scan.start" type="button" icon="scan">{{ scan.running.value ? '扫描中…' : '扫描新文件' }}</JzButton>
    <JzButton v-if="scan.running.value" @click="scan.cancel" type="button">取消扫描</JzButton>
    <p v-if="!target">当前没有启用的{{ kind === 'tv' ? '剧集' : '电影' }}库。<router-link to="/settings?sec=sec-libraries">添加视频库</router-link></p>
    <p v-if="scan.message.value" role="status">{{ scan.message.value }}</p>
  </div>
</template>
<script setup>
import JzButton from './JzButton.vue'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { currentMediaVideoLibs, onLibChange } from '../libraries.js'
import { useLibraryScan } from '../useLibraryScan.js'
const props = defineProps({ kind: { type: String, default: 'movie' } })
const emit = defineEmits(['done'])
const candidates = ref([])
const selected = ref(null)
function sync() { candidates.value = currentMediaVideoLibs(props.kind).filter(l => l.enabled !== false && l.enabled !== 0 && l.media_enabled !== false && l.media_enabled !== 0) }
watch(candidates, list => { if (!list.some(l => Number(l.id) === selected.value)) selected.value = list[0]?.id ?? null })
const target = computed(() => candidates.value.find(l => Number(l.id) === Number(selected.value)))
const scan = useLibraryScan(() => target.value, () => emit('done'))
let unsubscribe
onMounted(() => { sync(); unsubscribe = onLibChange(sync) })
onUnmounted(() => unsubscribe?.())
</script>
<style scoped>
.scan-action { border: 1px solid var(--jz-border); border-radius: 8px; padding: 12px; margin-bottom: 16px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
label { display: flex; align-items: center; gap: 8px; max-width: 100%; }
select { min-width: 0; max-width: 65vw; }
p { margin: 0; flex-basis: 100%; font-size: var(--jz-font-m); }
a { color: var(--jz-link); }
</style>
