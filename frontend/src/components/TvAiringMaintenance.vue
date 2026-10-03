<template>
  <section class="card-block airing-maintenance">
    <div class="section-heading"><h3>剧集播出资料</h3><span class="hint">全部启用的剧集视频库</span></div>
    <p class="hint">连载剧集每 7 天检查，完结剧集每 90 天检查。手动检查只更新播出资料，不修改媒体文件。</p>
    <p v-if="loading && !status" class="hint" role="status">正在读取维护状态…</p>
    <template v-if="status">
      <p v-if="!status.configured" class="hint">配置并保存 TMDB 凭据后，即可检查剧集播出资料。</p>
      <dl class="airing-counts"><div><dt>可检查剧集</dt><dd>{{ status.total || 0 }}</dd></div><div><dt>已有资料</dt><dd>{{ status.checked || 0 }}</dd></div><div><dt>待检查</dt><dd>{{ status.pending || 0 }}</dd></div><div><dt>检查失败</dt><dd>{{ status.failed || 0 }}</dd></div></dl>
      <p v-if="inProgress" class="feedback" role="status">{{ status.running ? '正在检查剧集播出资料…' : '检查已排队，正在等待处理…' }}</p>
      <p v-else class="hint">上次检查：{{ formatTime(status.last_checked_at) }}<span v-if="status.next_check_at"> · 下次检查：{{ formatTime(status.next_check_at) }}</span></p>
    </template>
    <div class="bar"><JzButton icon="refresh" :disabled="!status?.configured || inProgress || checking" :loading="checking" @click="check">{{ inProgress ? '检查进行中' : '立即检查播出资料' }}</JzButton><JzButton variant="ghost" :disabled="loading || checking" @click="reload">刷新状态</JzButton></div>
    <p v-if="error || status?.error" class="feedback" role="status">{{ airingErrorText(error || status.error) }}</p>
  </section>
</template>
<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import JzButton from './JzButton.vue'
import { api } from '../api.js'
import { airingErrorText } from '../tvCollection.js'

const props = defineProps({ active: Boolean })
const status = ref(null)
const loading = ref(false)
const checking = ref(false)
const error = ref('')
const inProgress = computed(() => !!status.value?.running || ['queued', 'pending', 'running'].includes(status.value?.status))
let generation = 0, controller, timer
function formatTime(value) {
  return Number(value) > 0 ? new Date(Number(value) * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未检查'
}
function stop() { generation++; controller?.abort(); clearTimeout(timer); loading.value = false; checking.value = false }
function schedule() {
  clearTimeout(timer)
  if (props.active && inProgress.value) timer = setTimeout(reload, 1500)
}
async function request(manual = false) {
  if (!props.active || (checking.value && !manual)) return
  controller?.abort(); clearTimeout(timer)
  const seq = ++generation
  controller = new AbortController()
  error.value = ''
  if (manual) checking.value = true
  else loading.value = true
  try {
    const options = { signal: controller.signal }
    if (manual) { options.method = 'POST'; options.body = '{}' }
    const result = await api('/api/tv/airing/' + (manual ? 'check' : 'status'), options)
    if (seq !== generation) return
    status.value = result
    schedule()
  } catch (e) {
    if (seq === generation) error.value = (manual ? '检查失败：' : '读取状态失败：') + airingErrorText(e.message)
  } finally {
    if (seq === generation) { loading.value = false; checking.value = false }
  }
}
function reload() { return request() }
function check() { if (!checking.value && !inProgress.value) return request(true) }
watch(() => props.active, active => { stop(); if (active) reload() }, { immediate: true })
onUnmounted(stop)
</script>
<style scoped>
.airing-counts { display: flex; gap: var(--jz-gap-xl); flex-wrap: wrap; margin: var(--jz-gap-m) 0; }
.airing-counts div { min-width: 72px; }
.airing-counts dt { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.airing-counts dd { margin: 4px 0 0; font-size: 1.2rem; font-variant-numeric: tabular-nums; }
.airing-maintenance .feedback { overflow-wrap: anywhere; }
</style>
